"""Live progression is independent of ordered, source-bound shadow scoring."""

import asyncio
import threading

import pytest

from agents.domain.errors import InvalidAgentState, StateConflictError
from app.adapters.background_evaluation import BackgroundShadowEvaluationAdapter
from app.adapters.repository import InMemoryInterviewRepository
from app.application import MVPInterviewApplication
from evaluation.analyzer import ConversationAnalysis
from tests.agent.integration.test_interview_planning import PlannerLLM, setup
from tests.app.fixtures import FixtureLLM
from tests.app.test_interview import RESUME
from tests.evaluation.test_port_integration import ports, request_for


async def prepared():
    agent, repo, _, _ = await setup(PlannerLLM())
    formal, inline = ports(repo)
    shadow = BackgroundShadowEvaluationAdapter(inline.legacy, formal)
    return agent, repo, shadow


async def commit(agent, repo, shadow, index):
    request = await request_for(repo, index=index)
    feedback = await shadow.evaluate(request)
    await agent.apply_evaluation_feedback(request.interview_id, feedback, answer=request.answer)
    return request, feedback


async def test_slow_shadow_does_not_block_next_turn_or_overwrite_new_state(monkeypatch):
    agent, repo, shadow = await prepared()
    entered, release = asyncio.Event(), asyncio.Event()
    original = shadow.formal._score

    async def slow(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(shadow.formal, "_score", slow)
    first, feedback = await commit(agent, repo, shadow, 1)
    assert "shadow_job" not in feedback.model_dump()
    assert not await repo.get_evaluation_records(first.interview_id)
    shadow.start_background(first.interview_id)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second, _ = await asyncio.wait_for(commit(agent, repo, shadow, 2), 1)
        shadow.start_background(first.interview_id)
        before = await repo.get_interview_context(first.interview_id)
        assert len(await repo.get_shadow_jobs(first.interview_id)) == 2
        assert not await repo.get_evaluation_records(first.interview_id)
        release.set()
        assert await shadow.drain(first.interview_id, timeout_seconds=2)
        records = await repo.get_evaluation_records(first.interview_id)
        assert [r.input.request_id for r in records] == [first.request_id, second.request_id]
        assert all(r.scored.evaluation.status == "completed" for r in records)
        assert records[1].scored.aggregation.inputs.supersedes_snapshot_id == (
            records[0].scored.evaluation.score_snapshot.snapshot_id
        )
        assert await repo.get_interview_context(first.interview_id) == before
        assert len(await repo.get_evaluation_records(first.interview_id)) == 2
        assert (await shadow.evaluate(first)).model_dump() == feedback.model_dump()
    finally:
        await shadow.close()


async def test_cancelled_job_survives_and_can_be_resumed(monkeypatch):
    agent, repo, shadow = await prepared()
    entered = asyncio.Event()
    original = shadow.formal._score

    async def slow(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(shadow.formal, "_score", slow)
    request, _ = await commit(agent, repo, shadow, 1)
    shadow.start_background(request.interview_id)
    await entered.wait()
    assert not await shadow.drain(request.interview_id, timeout_seconds=0.01)
    await shadow.close()
    assert len(await repo.get_shadow_jobs(request.interview_id)) == 1
    assert not await repo.get_evaluation_records(request.interview_id)
    monkeypatch.setattr(shadow.formal, "_score", original)
    assert await shadow.drain(request.interview_id, timeout_seconds=2)
    await shadow.close()
    assert len(await repo.get_evaluation_records(request.interview_id)) == 1


async def test_failure_is_recorded_without_changing_legacy_feedback(monkeypatch):
    agent, repo, shadow = await prepared()
    original = shadow.formal._score

    async def fail(*args):
        raise RuntimeError("private provider content")

    monkeypatch.setattr(shadow.formal, "_score", fail)
    request, _ = await commit(agent, repo, shadow, 1)
    before = await repo.get_interview_context(request.interview_id)
    assert await shadow.drain(request.interview_id, timeout_seconds=2)
    assert await repo.get_interview_context(request.interview_id) == before
    records = await repo.get_evaluation_records(request.interview_id)
    assert records[0].scored.evaluation.status == "failed"
    assert "private provider content" not in records[0].model_dump_json()
    monkeypatch.setattr(shadow.formal, "_score", original)
    second, _ = await commit(agent, repo, shadow, 2)
    assert await shadow.drain(second.interview_id, timeout_seconds=2)
    records = await repo.get_evaluation_records(request.interview_id)
    assert records[1].scored.aggregation.inputs.evaluation_failure_codes == (
        f"unassessed_feedback:{request.request_id}",
    )
    await shadow.close()


async def test_finalization_storage_failure_cannot_abort_the_live_result(monkeypatch):
    agent, repo, shadow = await prepared()
    request, _ = await commit(agent, repo, shadow, 1)
    before = await repo.get_interview_context(request.interview_id)

    async def unavailable(_):
        raise RuntimeError("private storage failure")

    monkeypatch.setattr(repo, "get_evaluation_records", unavailable)
    assert not await shadow.drain(request.interview_id, timeout_seconds=1)
    assert await repo.get_interview_context(request.interview_id) == before
    assert len(await repo.get_shadow_jobs(request.interview_id)) == 1
    await shadow.close()


async def test_mismatched_source_job_rolls_back_and_result_append_is_idempotent():
    agent, repo, shadow = await prepared()
    request = await request_for(repo)
    feedback = await shadow.evaluate(request)
    before = await repo.get_interview_context(request.interview_id)
    feedback.shadow_job.request.answer.text = "Changed source"
    with pytest.raises(InvalidAgentState):
        await agent.apply_evaluation_feedback(request.interview_id, feedback, answer=request.answer)
    assert await repo.get_interview_context(request.interview_id) == before
    assert not await repo.get_shadow_jobs(request.interview_id)
    request, _ = await commit(agent, repo, shadow, 1)
    assert await shadow.drain(request.interview_id, timeout_seconds=2)
    job = (await repo.get_shadow_jobs(request.interview_id))[0]
    record = (await repo.get_evaluation_records(request.interview_id))[0]
    await repo.append_shadow_record(job, record)
    changed = record.model_copy(deep=True)
    changed.feedback.analysis.summary = "Changed result"
    with pytest.raises(StateConflictError):
        await repo.append_shadow_record(job, changed)
    assert len(await repo.get_evaluation_records(request.interview_id)) == 1
    await shadow.close()


async def test_cli_candidate_input_allows_background_scoring_to_run():
    started = threading.Event()

    class ObservedFixture(FixtureLLM):
        def __call__(self, prompt, data, schema):
            if schema is ConversationAnalysis:
                started.set()
            return super().__call__(prompt, data, schema)

    calls = 0

    def answer(_):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert started.wait(timeout=2), "Synchronous candidate input blocked the worker"
        return "I profiled and fixed lock contention."

    repo = InMemoryInterviewRepository()
    app = MVPInterviewApplication(ObservedFixture(), repository=repo)
    result = await app.run(RESUME, max_questions=2, read_answer=answer, write=lambda _: None)
    assert len(await repo.get_evaluation_records(result["interview_id"])) == 2
    assert all(worker.done() for worker in app.evaluation._workers.values())
