"""Formal/shadow ports through actual Agent commits and repository boundaries."""

import asyncio
import json
from copy import deepcopy

import pytest

from agents.domain.errors import InvalidAgentState, StateConflictError
from agents.orchestrator import InterviewAgentService
from app.adapters.evaluation import AnswerEvidence, LLMEvaluationAdapter
from app.adapters.repository import InMemoryInterviewRepository
from app.adapters.rubric_evaluation import RubricEvaluationAdapter, ShadowEvaluationAdapter
from app.application import MVPInterviewApplication
from app.providers.llm import LLMError
from evaluation.aggregator import replay_aggregation
from evaluation.persistence import EvaluationRecord, validate_turn_evaluation
from evaluation.service import EvaluationService
from shared.contracts import CandidateAnswer, EvaluationRequest
from tests.agent.integration.test_interview_planning import PlannerLLM, setup
from tests.app.fixtures import FixtureLLM
from tests.app.test_interview import RESUME
from tests.evaluation.port_helpers import evaluation_output
from tests.evaluation.scoring_helpers import configuration


def legacy_output(prompt, data, schema):
    assert schema is AnswerEvidence
    return schema(
        answer_relevance=0.8,
        evidence_strength=0.8,
        analysis=dict(status="substantive", new_information=True, thread_complete=True),
        dimensions=[
            dict(
                competency="ownership",
                observation="supported",
                quote=data["answer"],
                fact=data["answer"],
                rationale="Personal implementation",
                rubric_level=4,
                strength=0.8,
            )
        ],
    )


async def request_for(repo, interview_id="pipeline-interview", index=1):
    context = await repo.get_interview_context(interview_id)
    question = await repo.get_question(context.state.current_question_id)
    return EvaluationRequest(
        request_id=f"request-{index}",
        interview_id=interview_id,
        question=question,
        answer=CandidateAnswer(
            interview_id=interview_id,
            question_id=question.question_id,
            answer_id=f"answer-{index}",
            text=f"I profiled incident {index} and found lock contention.",
        ),
    )


def ports(repo, *, model=evaluation_output, **kwargs):
    policy, profile = configuration()
    formal = RubricEvaluationAdapter(
        EvaluationService(model, **kwargs),
        repo,
        policy=policy,
        profile=profile,
    )
    return formal, ShadowEvaluationAdapter(LLMEvaluationAdapter(legacy_output, repo), formal)


@pytest.mark.parametrize("mode", ["formal", "shadow"])
async def test_port_commits_complete_record_and_keeps_scoring_out_of_prompts(mode):
    llm = PlannerLLM()
    agent, repo, _, _ = await setup(llm)
    request = await request_for(repo)
    formal, shadow = ports(repo)
    feedback = await (formal if mode == "formal" else shadow).evaluate(request)
    record = feedback.evaluation_record
    assert record.scored.evaluation.status == "completed"
    assert not await repo.get_evaluation_records(request.interview_id)
    assert record.input.topic.objective
    assert "evaluation_record" not in feedback.model_dump()
    await agent.apply_evaluation_feedback(request.interview_id, feedback, answer=request.answer)
    saved = await repo.get_interview_context(request.interview_id)
    records = await repo.get_evaluation_records(request.interview_id)
    assert records == [record]
    assert saved.pending_evaluation is None
    assert saved.state.state_version == record.base_state_version + 1
    assert (
        replay_aggregation(records[0].scored.aggregation) == record.scored.evaluation.score_snapshot
    )
    assert EvaluationRecord.model_validate_json(record.model_dump_json()) == record
    if mode == "shadow":
        assert saved.state.competencies["ownership"].score == 4
        assert saved.state.competencies["debugging"].score is None
        assert saved.question_history[-1].feedback.analysis.thread_complete
    else:
        assert saved.state.competencies["debugging"].score == 3
        assert saved.state.competencies["ownership"].score is None
    for _name, payload in llm.calls:
        text = json.dumps(payload)
        assert "matched_anchor_ids" not in text
        assert "aggregation-trace" not in text
        assert "shadow-uncalibrated" not in text


async def test_shadow_projection_matches_legacy_and_idempotency_adds_no_second_record():
    agent, repo, clock, _ = await setup(PlannerLLM())
    old_repo = deepcopy(repo)
    old_agent = InterviewAgentService(repository=old_repo, llm=PlannerLLM(), clock=clock)
    request = await request_for(repo)
    old = await LLMEvaluationAdapter(legacy_output, old_repo).evaluate(request)
    _, shadow = ports(repo)
    feedback = await shadow.evaluate(request)
    assert feedback.model_dump() == old.model_dump()
    action = await agent.apply_evaluation_feedback(
        request.interview_id, feedback, answer=request.answer
    )
    await old_agent.apply_evaluation_feedback(request.interview_id, old, answer=request.answer)
    current = await repo.get_interview_context(request.interview_id)
    previous = await old_repo.get_interview_context(request.interview_id)
    assert current.state.competencies == previous.state.competencies
    assert current.evidence_records == previous.evidence_records
    assert action == await agent.apply_evaluation_feedback(
        request.interview_id, feedback, answer=request.answer
    )
    assert len(await repo.get_evaluation_records(request.interview_id)) == 1
    cached = await shadow.evaluate(request)
    assert cached == feedback
    changed = request.model_copy(deep=True)
    changed.answer.text = "Changed retry input"
    with pytest.raises(InvalidAgentState, match="another input"):
        await shadow.evaluate(changed)


async def test_application_default_shadow_keeps_report_and_all_successful_records():
    repo = InMemoryInterviewRepository()
    app = MVPInterviewApplication(FixtureLLM(), repository=repo)
    result = await app.run(
        RESUME,
        max_questions=3,
        read_answer=lambda _: "I profiled and fixed lock contention.",
        write=lambda _: None,
    )
    records = await repo.get_evaluation_records(result["interview_id"])
    assert len(records) == 3
    assert all(r.scored.evaluation.status == "completed" for r in records)
    assert result["final_report"]["overall_score"] == 3
    assert "score_snapshot" not in json.dumps(result)
    first = records[0].model_dump_json()
    for before, after in zip(records, records[1:], strict=False):
        assert after.scored.aggregation.inputs.supersedes_snapshot_id == (
            before.scored.aggregation.snapshot.snapshot_id
        )
        n = len(before.scored.aggregation.inputs.history.sources)
        assert after.scored.aggregation.inputs.history.sources[:n] == (
            before.scored.aggregation.inputs.history.sources
        )
    assert (await repo.get_evaluation_records(result["interview_id"]))[0].model_dump_json() == first
    records[0].feedback.analysis.summary = "mutated caller copy"
    assert (await repo.get_evaluation_records(result["interview_id"]))[0].model_dump_json() == first


@pytest.mark.parametrize("failure", ["analyzer", "extractor", "resolver", "judge", "unexpected"])
async def test_shadow_failure_is_recorded_and_blocks_later_formal_overall(failure):
    agent, repo, _, _ = await setup(PlannerLLM())
    from tests.evaluation.port_helpers import SCHEMAS

    stages = dict(zip(SCHEMAS, ("analyzer", "extractor", "resolver", "judge"), strict=True))

    def model(prompt, data, schema):
        if failure == "unexpected":
            raise RuntimeError("private error body")
        if stages[schema] == failure:
            raise LLMError("private error body")
        return evaluation_output(prompt, data, schema)

    request = await request_for(repo)
    _, shadow = ports(repo, model=model)
    feedback = await shadow.evaluate(request)
    assert feedback.evaluation_record.scored.evaluation.status == "failed"
    assert feedback.evaluation_record.scored.aggregation is None
    assert "private error body" not in feedback.evaluation_record.model_dump_json()
    await agent.apply_evaluation_feedback(request.interview_id, feedback, answer=request.answer)
    current = await repo.get_interview_context(request.interview_id)
    assert current.question_history[-1].answer == request.answer
    assert current.state.competencies["ownership"].score == 4
    request2 = await request_for(repo, index=2)
    formal, _ = ports(repo)
    second = await formal.evaluate(request2)
    assert second.evaluation_record.scored.evaluation.status == "completed"
    assert second.evaluation_record.scored.aggregation.inputs.evaluation_failure_codes == (
        "unassessed_feedback:request-1",
    )
    assert second.evaluation_record.scored.aggregation.snapshot.overall_score is None
    assert not second.analysis.thread_complete
    await agent.apply_evaluation_feedback(request2.interview_id, second, answer=request2.answer)
    assert len(await repo.get_evaluation_records(request.interview_id)) == 2


async def test_formal_failure_retains_answer_does_not_complete_topic_and_can_continue():
    agent, repo, _, _ = await setup(PlannerLLM())
    request = await request_for(repo)

    def unavailable(*args):
        raise LLMError("unavailable")

    formal, _ = ports(repo, model=unavailable)
    feedback = await formal.evaluate(request)
    action = await agent.apply_evaluation_feedback(
        request.interview_id, feedback, answer=request.answer
    )
    current = await repo.get_interview_context(request.interview_id)
    assert action.question is not None
    assert current.question_history[-1].answer == request.answer
    assert current.topic_progress[request.question.topic_key].status != "completed"
    assert all(c.score is None for c in current.state.competencies.values())


async def test_legacy_history_gap_is_explicit_after_enabling_shadow():
    agent, repo, _, _ = await setup(PlannerLLM())
    request = await request_for(repo)
    feedback = await LLMEvaluationAdapter(legacy_output, repo).evaluate(request)
    await agent.apply_evaluation_feedback(request.interview_id, feedback, answer=request.answer)
    next_request = await request_for(repo, index=2)
    formal, _ = ports(repo)
    result = await formal.evaluate(next_request)
    assert result.evaluation_record.scored.aggregation.inputs.evaluation_failure_codes == (
        "unassessed_feedback:request-1",
    )


async def test_concurrent_commit_never_rebases_old_scoring(monkeypatch):
    agent, repo, _, _ = await setup(PlannerLLM())
    request = await request_for(repo)
    _, shadow = ports(repo)
    feedback = await shadow.evaluate(request)
    original = await repo.get_interview_context(request.interview_id)
    calls = 0

    async def conflict(commit):
        nonlocal calls
        calls += 1
        repo.contexts[request.interview_id].state.state_version += 1
        raise StateConflictError("Concurrent state update")

    monkeypatch.setattr(repo, "commit_turn", conflict)
    with pytest.raises(StateConflictError, match="base_state_version"):
        await agent.apply_evaluation_feedback(request.interview_id, feedback, answer=request.answer)
    assert calls == 1
    assert not await repo.get_evaluation_records(request.interview_id)
    assert repo.contexts[request.interview_id].question_history == original.question_history


@pytest.mark.parametrize(
    "tamper", ["version", "answer", "question", "topic", "trace", "snapshot_link", "omitted"]
)
async def test_repository_rejects_bad_receipt_without_partial_commit(monkeypatch, tamper):
    agent, repo, _, _ = await setup(PlannerLLM())
    request = await request_for(repo)
    formal, _ = ports(repo)
    feedback = await formal.evaluate(request)
    before = await repo.get_interview_context(request.interview_id)
    commit_original = repo.commit_turn

    async def bad_commit(commit):
        if tamper == "omitted":
            commit.evaluation_record = None
            return await commit_original(commit)
        payload = commit.evaluation_record.model_dump()
        if tamper == "version":
            payload["base_state_version"] -= 1
        elif tamper == "answer":
            payload["input"]["answer"]["text"] = "Different accepted answer"
        elif tamper == "question":
            payload["input"]["question"]["text"] = "A different question"
        elif tamper == "topic":
            payload["input"]["topic"]["objective"] = "A stale Planner objective"
        elif tamper == "trace":
            payload["scored"]["aggregation"]["overall_mean"]["numerator"] += 1
        else:
            payload["scored"]["aggregation"]["inputs"].update(
                supersedes_snapshot_id="fabricated-predecessor", reevaluation_reason="new_answer"
            )
        commit.evaluation_record = EvaluationRecord.model_validate(payload)
        return await commit_original(commit)

    monkeypatch.setattr(repo, "commit_turn", bad_commit)
    with pytest.raises((InvalidAgentState, StateConflictError, ValueError)):
        await agent.apply_evaluation_feedback(request.interview_id, feedback, answer=request.answer)
    assert await repo.get_interview_context(request.interview_id) == before
    assert not await repo.get_evaluation_records(request.interview_id)


async def test_shadow_cancellation_publishes_nothing():
    _, repo, _, _ = await setup(PlannerLLM())
    request = await request_for(repo)
    formal, shadow = ports(repo)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def waiting(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    formal.service.evaluate_scored = waiting
    task = asyncio.create_task(shadow.evaluate(request))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert not await repo.get_evaluation_records(request.interview_id)


async def test_repository_requires_receipt_to_be_bound_to_feedback_turn(monkeypatch):
    agent, repo, _, _ = await setup(PlannerLLM())
    request = await request_for(repo)
    formal, _ = ports(repo)
    result = await formal.evaluate(request)
    stored = await repo.get_interview_context(request.interview_id)

    async def unbound(commit):
        commit.feedback_request_id = None
        validate_turn_evaluation(commit, stored, [], request.question)

    monkeypatch.setattr(repo, "commit_turn", unbound)
    with pytest.raises(InvalidAgentState, match="atomic feedback turn"):
        await agent.apply_evaluation_feedback(request.interview_id, result, answer=request.answer)


async def test_complete_ledger_survives_agent_history_retention():
    agent, repo, _, _ = await setup(PlannerLLM())
    agent._settings.question_agent.history_retention = 1
    formal, _ = ports(repo)
    for index in range(1, 4):
        request = await request_for(repo, index=index)
        feedback = await formal.evaluate(request)
        await agent.apply_evaluation_feedback(request.interview_id, feedback, answer=request.answer)
        context = await repo.get_interview_context(request.interview_id)
        assert len(context.question_history) == 1
    records = await repo.get_evaluation_records(request.interview_id)
    assert len(records[-1].scored.aggregation.inputs.history.sources) == 3
    assert [
        s.turn.answer.answer_id for s in records[-1].scored.aggregation.inputs.history.sources
    ] == (["answer-1", "answer-2", "answer-3"])


async def test_persistence_rejects_an_internally_valid_but_truncated_ledger(monkeypatch):
    agent, repo, _, _ = await setup(PlannerLLM())
    formal, _ = ports(repo)
    request = await request_for(repo)
    first = await formal.evaluate(request)
    await agent.apply_evaluation_feedback(request.interview_id, first, answer=request.answer)
    request2 = await request_for(repo, index=2)
    second = await formal.evaluate(request2)
    record = second.evaluation_record
    # Re-run with no prior evidence to build a valid mathematical record, then
    # restore the real predecessor link: replay alone cannot detect this omission.
    scored = await formal.service.evaluate_scored(
        record.input.model_copy(update={"existing_evidence": ()}),
        rubric=formal.rubric,
        policy=formal.policy,
        profile=formal.profile,
        supersedes_snapshot_id=first.evaluation_record.scored.aggregation.snapshot.snapshot_id,
        reevaluation_reason="new_answer",
    )
    assert scored.evaluation.status == "completed"
    bad = record.model_copy(update={"scored": scored})
    original = repo.commit_turn

    async def truncated(commit):
        commit.evaluation_record = bad
        return await original(commit)

    monkeypatch.setattr(repo, "commit_turn", truncated)
    with pytest.raises(InvalidAgentState, match="complete committed ledger"):
        await agent.apply_evaluation_feedback(request2.interview_id, second, answer=request2.answer)
    assert await repo.get_evaluation_records(request.interview_id) == [first.evaluation_record]


async def test_shadow_concurrency_starts_both_engines_before_either_finishes():
    _, repo, _, _ = await setup(PlannerLLM())
    request = await request_for(repo)
    formal, shadow = ports(repo)
    legacy_started, formal_started = asyncio.Event(), asyncio.Event()
    score = formal._score
    legacy = shadow.legacy.evaluate

    async def score_after_legacy(*args):
        formal_started.set()
        await legacy_started.wait()
        return await score(*args)

    async def legacy_after_formal(*args):
        legacy_started.set()
        await formal_started.wait()
        return await legacy(*args)

    formal._score = score_after_legacy
    shadow.legacy.evaluate = legacy_after_formal
    result = await asyncio.wait_for(shadow.evaluate(request), timeout=1)
    assert result.evaluation_record.scored.evaluation.status == "completed"


def test_rollout_switch_keeps_legacy_available_and_rejects_unreviewed_live_mode():
    application = MVPInterviewApplication(FixtureLLM(), evaluation_mode="legacy")
    from app.adapters.assessment import RealtimeDecisionAdapter

    assert isinstance(application.evaluation.legacy, RealtimeDecisionAdapter)
    assert application.evaluation.formal is None
    assert application.evaluation.assessment is not None
    with pytest.raises(ValueError, match="shadow or legacy"):
        MVPInterviewApplication(FixtureLLM(), evaluation_mode="formal")


async def test_multiple_failure_codes_use_aggregator_canonical_order():
    agent, repo, _, _ = await setup(PlannerLLM())

    def unavailable(*args):
        raise LLMError("unavailable")

    formal, _ = ports(repo, model=unavailable)
    for index in ("z", "a"):
        request = await request_for(repo, index=index)
        failed = await formal.evaluate(request)
        await agent.apply_evaluation_feedback(request.interview_id, failed, answer=request.answer)
    formal, _ = ports(repo)
    request = await request_for(repo, index="success")
    feedback = await formal.evaluate(request)
    assert feedback.evaluation_record.scored.aggregation.inputs.evaluation_failure_codes == (
        "unassessed_feedback:request-a",
        "unassessed_feedback:request-z",
    )
    await agent.apply_evaluation_feedback(request.interview_id, feedback, answer=request.answer)
    assert len(await repo.get_evaluation_records(request.interview_id)) == 3
