import asyncio
from collections import Counter

import pytest

from app.providers.llm import LLMError
from evaluation.model_calls import EvaluationStageError
from evaluation.recovery import is_transient, reserve_stage
from tests.evaluation.test_decoupled_assessment import commit
from tests.evaluation.test_formal_assessment import create_port


async def test_transient_resolver_failure_only_retries_that_stage():
    agent, repo, port, calls, provider = await create_port()
    client = port.formal.service._resolver._client
    counts = Counter()

    def flaky(prompt, data, schema):
        counts[schema.__name__] += 1
        if counts[schema.__name__] == 1:
            raise LLMError("synthetic timeout", code="timeout")
        return provider(prompt, data, schema)

    client._llm = flaky
    try:
        request, _, _ = await commit(agent, repo, port, 1)
        assert await port.drain(request.interview_id, timeout_seconds=5)
        record = (await repo.get_evaluation_records(request.interview_id))[0]
        assert record.scored.evaluation.status == "completed"
        assert sum(counts.values()) == 2
        assert len(calls) == 4  # Original four successful calls; exactly one extra failed attempt.
        assert repo._evaluation_recoveries[request.interview_id] == 1
        assert await port.drain(request.interview_id)
        assert sum(counts.values()) == 2
    finally:
        await port.close()


async def test_restart_reuses_completed_extraction_and_enforces_budget():
    from app.adapters.rubric_evaluation import build_evaluation_adapter

    agent, repo, port, calls, provider = await create_port()
    entered = asyncio.Event()

    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    port.formal.service._resolver.resolve = wait
    request, _, _ = await commit(agent, repo, port, 1)
    port.start_background(request.interview_id)
    await asyncio.wait_for(entered.wait(), 3)
    await port.close()
    before = len(calls)
    resumed = build_evaluation_adapter(provider, repo, None, mode="shadow")
    try:
        assert await resumed.drain(request.interview_id, timeout_seconds=5)
        assert len(calls) - before == 2  # Resolver + Judge; no repeated extraction/analysis.
    finally:
        await resumed.close()


def test_persistent_stage_and_interview_budget():
    used = 0
    for index in range(3):
        journal = {}
        _, used = reserve_stage(journal, str(index), "resolver", recovery_used=used)
        _, used = reserve_stage(journal, str(index), "resolver", recovery_used=used)
        with pytest.raises(EvaluationStageError):
            reserve_stage(journal, str(index), "resolver", recovery_used=used)
    assert used == 3
    journal = {}
    reserve_stage(journal, "fourth", "judge", recovery_used=used)
    with pytest.raises(EvaluationStageError):
        reserve_stage(journal, "fourth", "judge", recovery_used=used)
    assert not is_transient(LLMError("auth error"))


async def test_terminal_wait_deadline_keeps_scoring_alive():
    from app.application import MVPInterviewApplication
    from tests.app.fixtures import FixtureLLM
    from tests.app.test_interview import RESUME

    app = MVPInterviewApplication(FixtureLLM())
    score, drain = app.evaluation.formal._score, app.evaluation.drain
    waits = []

    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await score(*args, **kwargs)

    async def deadline(interview_id):
        result = await drain(interview_id, timeout_seconds=0.001 if not waits else 5)
        waits.append(result)
        return result

    app.evaluation.formal._score = delayed
    app.evaluation.drain = deadline
    result = await app.run(
        RESUME,
        max_questions=1,
        read_answer=lambda _: "I measured and fixed lock contention.",
        write=lambda _: None,
    )
    assert waits == [False, True]
    assert result["final_report"]["unscored_answer_count"] == 0
