"""Formal receipts replace the second scoring model without changing source/commit guards."""

import asyncio

import pytest

from agents.domain.errors import InvalidAgentState
from app.adapters.assessment import AnswerAssessment
from app.adapters.rubric_evaluation import build_evaluation_adapter
from evaluation.assessment import validate_assessment
from evaluation.formal_assessment import assessment_from_formal
from tests.agent.integration.test_interview_planning import PlannerLLM, setup
from tests.evaluation.port_helpers import SCHEMAS, evaluation_output
from tests.evaluation.test_decoupled_assessment import commit, model


async def create_port():
    calls = []

    def provider(prompt, data, schema):
        assert schema is not AnswerAssessment, "No independent assessment request is allowed"
        calls.append(schema)
        return (
            evaluation_output(prompt, data, schema)
            if schema in SCHEMAS
            else model(prompt, data, schema)
        )

    agent, repo, _, _ = await setup(PlannerLLM())
    return (
        agent,
        repo,
        build_evaluation_adapter(provider, repo, None, mode="shadow"),
        calls,
        provider,
    )


async def test_projection_contains_only_current_eligible_quotes_and_preserves_formal_snapshot():
    agent, repo, port, calls, _ = await create_port()
    try:
        for index in (1, 2):
            request, _, _ = await commit(agent, repo, port, index)
            assert await port.drain(request.interview_id, timeout_seconds=2)
        jobs = await repo.get_shadow_jobs(request.interview_id)
        records = await repo.get_evaluation_records(request.interview_id)
        saved = [r.model_dump_json() for r in records]
        for job, record in zip(jobs, records, strict=True):
            projected = assessment_from_formal(job, record)
            assert validate_assessment(job, projected) == projected
            assert [(d.competency.value, d.rubric_level) for d in projected.dimensions] == [
                ("debugging", 3)
            ]
            assert all(
                quote in job.request.answer.text
                for d in projected.dimensions
                for quote in d.source_quotes
            )
        assert [r.model_dump_json() for r in records] == saved
        assert len(calls) == 8
        assert len(await repo.get_assessment_records(request.interview_id)) == 2
    finally:
        await port.close()


async def test_cancel_between_formal_commit_and_projection_resumes_without_model_calls(monkeypatch):
    agent, repo, port, calls, provider = await create_port()
    entered = asyncio.Event()

    async def blocked(job):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(port.assessment, "assess", blocked)
    request, _, _ = await commit(agent, repo, port, 1)
    port.start_background(request.interview_id)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert len(await repo.get_evaluation_records(request.interview_id)) == 1
    finally:
        await port.close()
    assert not await repo.get_assessment_records(request.interview_id)
    count = len(calls)
    resumed = build_evaluation_adapter(provider, repo, None, mode="shadow")
    try:
        assert await resumed.drain(request.interview_id, timeout_seconds=2)
        assert len(calls) == count == 4
        assert len(await repo.get_assessment_records(request.interview_id)) == 1
        assert await resumed.drain(request.interview_id, timeout_seconds=2)
        assert len(calls) == count
    finally:
        await resumed.close()


async def test_failed_formal_receipt_projects_unavailable_without_fallback_model(monkeypatch):
    agent, repo, port, calls, _ = await create_port()

    async def failed(*args):
        raise RuntimeError("Do not leak this model detail")

    monkeypatch.setattr(port.formal, "_score", failed)
    request, _, _ = await commit(agent, repo, port, 1)
    try:
        assert await port.drain(request.interview_id, timeout_seconds=2)
        record = (await repo.get_assessment_records(request.interview_id))[0]
        assert record.assessment_status == "unavailable" and record.dimensions == []
        assert record.evaluation_issues == ["shadow_internal_error"]
        assert "Do not leak" not in record.model_dump_json()
    finally:
        await port.close()


async def test_projection_rejects_other_source_and_does_not_borrow_historical_ratings():
    agent, repo, port, _, _ = await create_port()
    try:
        for index in (1, 2):
            request, _, _ = await commit(agent, repo, port, index)
            assert await port.drain(request.interview_id, timeout_seconds=2)
        jobs = await repo.get_shadow_jobs(request.interview_id)
        records = await repo.get_evaluation_records(request.interview_id)
        with pytest.raises(InvalidAgentState):
            assessment_from_formal(jobs[1], records[0])
        record = records[1]
        historical = records[0].scored.evaluation.evidence_items[0].evidence_id
        ratings = tuple(
            a.model_copy(update={"evidence_ids": (*a.evidence_ids, historical)})
            if a.decision == "included" and historical not in a.evidence_ids
            else a
            for a in record.scored.evaluation.assessments
        )
        scored = record.scored.model_copy(
            update={
                "evaluation": record.scored.evaluation.model_copy(update={"assessments": ratings}),
                "aggregation": record.scored.aggregation.model_copy(
                    update={"gated_assessments": ratings}
                ),
            }
        )
        result = assessment_from_formal(jobs[1], record.model_copy(update={"scored": scored}))
        assert result.assessment_status == "valid" and not result.dimensions
    finally:
        await port.close()


async def test_pending_legacy_job_remains_resumable_after_switch_to_shadow():
    agent, repo, _, _ = await setup(PlannerLLM())
    calls = []

    def provider(prompt, data, schema):
        calls.append(schema)
        return model(prompt, data, schema)

    legacy = build_evaluation_adapter(provider, repo, None, mode="legacy")
    request, _, _ = await commit(agent, repo, legacy, 1)
    await legacy.close()
    assert not await repo.get_assessment_records(request.interview_id)
    shadow = build_evaluation_adapter(provider, repo, None, mode="shadow")
    try:
        assert await shadow.drain(request.interview_id, timeout_seconds=2)
        record = (await repo.get_assessment_records(request.interview_id))[0]
        assert record.assessment_status == "valid"
        assert [(d.competency.value, d.rubric_level) for d in record.dimensions] == [
            ("ownership", 4)
        ]
        assert calls.count(AnswerAssessment) == 1
        assert not await repo.get_evaluation_records(request.interview_id)
    finally:
        await shadow.close()


async def test_unselected_evidence_does_not_create_an_additional_capability_contribution():
    agent, repo, port, _, _ = await create_port()
    try:
        request, _, _ = await commit(agent, repo, port, 1)
        assert await port.drain(request.interview_id, timeout_seconds=2)
        job = (await repo.get_shadow_jobs(request.interview_id))[0]
        record = (await repo.get_evaluation_records(request.interview_id))[0]
        projected = assessment_from_formal(job, record)
        assert projected.dimensions
        assert all(
            d.rationale == "Supported by the quoted behavior in this answer."
            for d in projected.dimensions
        )
        aggregation = record.scored.aggregation.model_copy(
            update={
                "weights": tuple(
                    w.model_copy(update={"selected": False})
                    for w in record.scored.aggregation.weights
                )
            }
        )
        altered = record.model_copy(
            update={"scored": record.scored.model_copy(update={"aggregation": aggregation})}
        )
        assert assessment_from_formal(job, altered).dimensions == []
    finally:
        await port.close()
