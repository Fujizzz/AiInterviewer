"""Decisions, delayed scores, corrections and reports have independent failure boundaries."""

import asyncio

import pytest

from agents.domain.errors import InvalidAgentState
from app.adapters.assessment import AnswerAssessment, AnswerDecision, RealtimeDecisionAdapter
from app.adapters.decision import CompactAnswerDecision
from app.adapters.rubric_evaluation import build_evaluation_adapter
from evaluation.assessment import assessed_report_context, project_assessments
from shared.contracts.agent_contracts import AnswerRelation
from tests.agent.integration.test_interview_planning import PlannerLLM, setup
from tests.app.decision_fixtures import compact_output, legacy_data
from tests.evaluation.test_port_integration import request_for


def model(prompt, data, schema):
    if schema is CompactAnswerDecision:
        return compact_output(model(prompt, legacy_data(data), AnswerDecision).model_dump())
    if schema is AnswerDecision:
        assert "dimensions" not in schema.model_fields
        assert "rubric_level" not in prompt
        return schema(
            answer_relevance=0.9,
            analysis=dict(
                status="substantive",
                answer_scope="concrete",
                new_information=True,
                thread_complete=False,
                missing_information=["How was the fix validated?"],
            ),
        )
    assert schema is AnswerAssessment
    assert "objectives" not in data and "analysis" not in data
    return schema(
        evidence_strength=0.9,
        dimensions=[
            dict(
                competency="ownership",
                observation="supported",
                quote="",
                source_segment_ids=[data["answer_segments"][0]["id"]],
                fact=data["answer"],
                rationale="Concrete personal action",
                rubric_level=4,
                strength=0.9,
            )
        ],
    )


async def prepared(provider=model):
    agent, repo, _, _ = await setup(PlannerLLM())
    port = build_evaluation_adapter(provider, repo, None, mode="legacy")
    return agent, repo, port


async def commit(agent, repo, port, index):
    request = await request_for(repo, index=index)
    feedback = await port.evaluate(request)
    action = await agent.apply_evaluation_feedback(
        request.interview_id, feedback, answer=request.answer
    )
    return request, feedback, action


async def test_pending_scores_do_not_block_progress_and_are_consumed_once_on_a_later_turn(
    monkeypatch,
):
    agent, repo, port = await prepared()
    entered, release = asyncio.Event(), asyncio.Event()
    original = port.assessment.assess

    async def slow(job):
        entered.set()
        await release.wait()
        return await original(job)

    monkeypatch.setattr(port.assessment, "assess", slow)
    request, feedback, _ = await commit(agent, repo, port, 1)
    assert feedback.assessment_status == "pending" and not feedback.dimensions
    assert feedback.analysis.missing_information == ["How was the fix validated?"]
    port.start_background(request.interview_id)
    try:
        await entered.wait()
        second, _, _ = await asyncio.wait_for(commit(agent, repo, port, 2), 1)
        port.start_background(request.interview_id)
        before = await repo.get_interview_context(request.interview_id)
        assert before.thread_difficulty == 2
        assert before.state.competencies["ownership"].score is None
        assert not await repo.get_assessment_records(request.interview_id)
        release.set()
        assert await port.drain(request.interview_id, timeout_seconds=2)
        assert await repo.get_interview_context(request.interview_id) == before
        third, _, _ = await commit(agent, repo, port, 3)
        current = await repo.get_interview_context(request.interview_id)
        assert current.applied_assessment_ids == [request.request_id, second.request_id]
        assert current.state.competencies["ownership"].score == 4
        assert current.thread_difficulty == 3
        assert request.answer.answer_id not in current.unassessed_answer_ids
        assert third.answer.answer_id in current.unassessed_answer_ids
        repeated, _ = project_assessments(
            current,
            await repo.get_shadow_jobs(request.interview_id),
            await repo.get_assessment_records(request.interview_id),
        )
        assert repeated == current
    finally:
        await port.close()


async def test_bad_score_does_not_change_decision_or_publish_ungrounded_evidence():
    def bad_score(prompt, data, schema):
        result = model(prompt, data, schema)
        if schema is AnswerAssessment:
            result.dimensions[0].source_segment_ids = ["invented-source"]
        return result

    agent, repo, port = await prepared(bad_score)
    request, feedback, _ = await commit(agent, repo, port, 1)
    before = await repo.get_interview_context(request.interview_id)
    assert await port.drain(request.interview_id, timeout_seconds=2)
    record = (await repo.get_assessment_records(request.interview_id))[0]
    assert record.assessment_status == "unavailable" and not record.dimensions
    assert await repo.get_interview_context(request.interview_id) == before
    report = await assessed_report_context(repo, before)
    assert request.answer.answer_id in report.unassessed_answer_ids
    assert report.question_history[-1].feedback.analysis == feedback.analysis
    assert report.state.competencies["ownership"].score is None
    await port.close()


async def test_cancelled_assessment_is_retained_and_can_be_resumed(monkeypatch):
    agent, repo, port = await prepared()
    entered = asyncio.Event()
    original = port.assessment.assess

    async def blocked(_):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(port.assessment, "assess", blocked)
    request, _, _ = await commit(agent, repo, port, 1)
    port.start_background(request.interview_id)
    await entered.wait()
    await port.close()
    assert not await repo.get_assessment_records(request.interview_id)
    assert len(await repo.get_shadow_jobs(request.interview_id)) == 1
    monkeypatch.setattr(port.assessment, "assess", original)
    assert await port.drain(request.interview_id, timeout_seconds=2)
    await port.close()


@pytest.mark.parametrize(
    "status,scope",
    [
        ("explicit_unknown", "none"),
        ("refusal", "none"),
        ("non_answer", "none"),
        ("substantive", "label_only"),
    ],
)
async def test_non_evidence_answers_do_not_call_the_assessment_model(status, scope):
    calls = []

    def provider(prompt, data, schema):
        calls.append(schema)
        assert schema is CompactAnswerDecision
        return schema(answer_relevance=0.5, analysis=dict(status=status, scope=scope))

    agent, repo, port = await prepared(provider)
    request, _, _ = await commit(agent, repo, port, 1)
    assert await port.drain(request.interview_id, timeout_seconds=2)
    assert calls == [CompactAnswerDecision]
    assert (await repo.get_assessment_records(request.interview_id))[0].dimensions == []
    await port.close()


async def test_late_score_cannot_revive_evidence_retracted_by_a_later_answer():
    agent, repo, port = await prepared()
    first = await request_for(repo, index=1)
    first.answer.text = "I stored it in Redis."
    feedback = await port.evaluate(first)
    await agent.apply_evaluation_feedback(first.interview_id, feedback, answer=first.answer)
    second = await request_for(repo, index=2)
    second.answer.text = "I need to correct that: we used an in-memory dictionary."
    feedback2 = await port.evaluate(second)
    relation = AnswerRelation(
        kind="supersedes",
        earlier_answer_id=first.answer.answer_id,
        earlier_quote=first.answer.text,
        current_quote=second.answer.text,
        explanation="Explicit correction",
        relation_id="correction",
    )
    feedback2.analysis.answer_relations = [relation]
    feedback2.shadow_job.feedback.analysis.answer_relations = [relation]
    await agent.apply_evaluation_feedback(second.interview_id, feedback2, answer=second.answer)
    jobs = await repo.get_shadow_jobs(first.interview_id)
    record = await port.assessment.assess(jobs[0])
    await repo.append_assessment_record(jobs[0], record)
    current = await repo.get_interview_context(first.interview_id)
    projected, _ = project_assessments(current, jobs, [record])
    assert projected.state.competencies["ownership"].score is None
    assert projected.evidence_records[0].status == "superseded"
    assert current.state.competencies["ownership"].score is None
    await port.close()


async def test_score_publication_rejects_wrong_source_and_preserves_live_context():
    agent, repo, port = await prepared()
    request, _, _ = await commit(agent, repo, port, 1)
    job = (await repo.get_shadow_jobs(request.interview_id))[0]
    record = await port.assessment.assess(job)
    changed = record.model_copy(update={"answer_id": "different-answer"})
    before = await repo.get_interview_context(request.interview_id)
    with pytest.raises(InvalidAgentState):
        await repo.append_assessment_record(job, changed)
    assert not await repo.get_assessment_records(request.interview_id)
    assert await repo.get_interview_context(request.interview_id) == before
    await repo.append_assessment_record(job, record)
    await repo.append_assessment_record(job, record)
    assert len(await repo.get_assessment_records(request.interview_id)) == 1
    await port.close()


async def test_realtime_decision_invalid_structure_keeps_failure_separate_from_candidate():
    def broken(prompt, data, schema):
        return schema.model_validate({"analysis": {"status": "broken"}, "answer_relevance": 0.8})

    _, repo, _ = await prepared()
    request = await request_for(repo)
    feedback = await RealtimeDecisionAdapter(broken, repo).evaluate(request)
    assert feedback.analysis_status == "unavailable"
    assert not feedback.analysis.thread_complete and not feedback.dimensions


async def test_shadow_reuses_valid_decision_and_makes_five_calls_per_concrete_answer():
    from collections import Counter

    from evaluation.analyzer import ConversationAnalysis
    from evaluation.extractor import EvidenceExtraction
    from evaluation.judge import GroupedJudgeDraft
    from evaluation.resolution import ResolutionDraft
    from tests.evaluation.port_helpers import SCHEMAS, evaluation_output

    calls = []

    def provider(prompt, data, schema):
        calls.append(schema)
        return (
            evaluation_output(prompt, data, schema)
            if schema in SCHEMAS
            else model(prompt, data, schema)
        )

    agent, repo, _, _ = await setup(PlannerLLM())
    port = build_evaluation_adapter(provider, repo, None, mode="shadow")
    try:
        for index in (1, 2):
            request, _, _ = await commit(agent, repo, port, index)
            assert await port.drain(request.interview_id, timeout_seconds=2)
        records = await repo.get_evaluation_records(request.interview_id)
        assert len(records) == 2
        assert all(r.scored.evaluation.status == "completed" for r in records)
        assert ConversationAnalysis not in calls
        assert Counter(calls) == {
            CompactAnswerDecision: 2,
            AnswerAssessment: 2,
            EvidenceExtraction: 2,
            ResolutionDraft: 2,
            GroupedJudgeDraft: 2,
        }
    finally:
        await port.close()


@pytest.mark.parametrize("retain_grounded", [True, False])
async def test_uncited_low_score_is_omitted_without_losing_grounded_scores(retain_grounded):
    from shared.contracts import DimensionEvidence

    def provider(prompt, data, schema):
        result = model(prompt, data, schema)
        if schema is AnswerAssessment:
            missing = DimensionEvidence(
                competency="evaluation",
                observation="weak",
                fact="No tests described.",
                rationale="There is no observed evaluation behavior.",
                strength=0.2,
                rubric_level=1,
            )
            result.dimensions = [*result.dimensions, missing] if retain_grounded else [missing]
        return result

    agent, repo, port = await prepared(provider)
    try:
        request, _, _ = await commit(agent, repo, port, 1)
        assert await port.drain(request.interview_id, timeout_seconds=2)
        record = (await repo.get_assessment_records(request.interview_id))[0]
        assert record.evaluation_issues == ["UNCITED_DIMENSION_OMITTED:evaluation"]
        if retain_grounded:
            assert record.assessment_status == "valid"
            assert [(d.competency.value, d.rubric_level) for d in record.dimensions] == [
                ("ownership", 4)
            ]
        else:
            assert record.assessment_status == "unavailable" and not record.dimensions
            assert record.evidence_strength == 0
    finally:
        await port.close()
