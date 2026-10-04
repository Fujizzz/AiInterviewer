import asyncio
from threading import Event

import pytest

from agents.model_calls import current_model_call
from app.providers.llm import LLMError
from evaluation.aggregation import ScoredEvaluation
from evaluation.aggregator import replay_aggregation
from evaluation.judge import JudgeDraft
from evaluation.rubric import load_rubric_pack
from evaluation.service import EvaluationService
from tests.evaluation.scoring_helpers import configuration
from tests.evaluation.test_resolved_service import model_for, proposals


def judge_proposals(data):
    """Test provider output; only the diagnostic method has supporting behavior."""
    key = data["evidence"][0]["evidence_id"]
    return dict(
        assessments=[
            dict(
                competency=r["competency"],
                criterion_id=c["criterion_id"],
                evidence_ids=[key] if c["criterion_id"] == "debugging.diagnostic_method" else [],
                assigned_level=3 if c["criterion_id"] == "debugging.diagnostic_method" else None,
                matched_anchor_ids=[c["criterion_id"] + ".l3"]
                if c["criterion_id"] == "debugging.diagnostic_method"
                else [],
                decision="included"
                if c["criterion_id"] == "debugging.diagnostic_method"
                else "insufficient",
                reason_codes=["observable_behavior"]
                if c["criterion_id"] == "debugging.diagnostic_method"
                else ["no_evidence"],
                concise_rationale="Observable diagnostic evidence or a gap.",
            )
            for r in data["rubric"]["rubrics"]
            for c in r["criteria"]
        ],
        unmapped_evidence=[
            dict(
                evidence_id=i["evidence_id"],
                reason_code="no_relevant_criterion",
                concise_rationale="No relevant behavior.",
            )
            for i in data["evidence"]
            if i["evidence_id"] != key
        ],
    )


async def score(service, context, **kwargs):
    policy, profile = configuration()
    return await service.evaluate_scored(
        context, rubric=load_rubric_pack(), policy=policy, profile=profile, **kwargs
    )


def service_model(analysis_payload, extraction_payload):
    return model_for(
        analysis_payload, extraction_payload, lambda prompt, data, schema: schema(**proposals(data))
    )


async def test_four_stage_service_replays_and_does_not_confuse_coverage_with_topic_completion(
    phase_two_input,
    analysis_payload,
    extraction_payload,
):
    def judge(prompt, data, schema):
        assert schema is JudgeDraft
        return schema(**judge_proposals(data))

    service = EvaluationService(
        service_model(analysis_payload, extraction_payload), judge_llm=judge
    )
    before = phase_two_input.model_dump_json()
    first = await score(service, phase_two_input)
    assert first.evaluation.status == "completed"
    assert first.evaluation.analysis.thread_complete
    assert first.evaluation.score_snapshot.overall_score is None
    assert first.aggregation.snapshot == replay_aggregation(first.aggregation)
    assert len(first.evaluation.assessments) == 21
    assert first == await score(service, phase_two_input)
    assert phase_two_input.model_dump_json() == before
    assert ScoredEvaluation.model_validate_json(first.model_dump_json()) == first


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("model", "model_error"),
        ("json", "invalid_output"),
        ("schema", "invalid_output"),
        ("reference", "invalid_assessment"),
        ("timeout", "timeout"),
    ],
)
async def test_judge_failure_discards_all_new_artifacts(
    phase_two_input,
    analysis_payload,
    extraction_payload,
    failure,
    reason,
):
    def judge(prompt, data, schema):
        if failure == "model":
            raise LLMError("Sensitive provider detail")
        if failure == "json":
            raise LLMError("Sensitive JSON", code="invalid_json")
        if failure == "schema":
            return {"score": 5}
        if failure == "timeout":
            raise TimeoutError()
        value = judge_proposals(data)
        value["assessments"] = []
        return schema(**value)

    service = EvaluationService(
        service_model(analysis_payload, extraction_payload), judge_llm=judge
    )
    result = await score(service, phase_two_input)
    assert result.evaluation.status == "failed"
    assert result.evaluation.reason_codes == (f"judge_{reason}",)
    assert result.resolution is result.aggregation is result.evaluation.score_snapshot is None
    assert result.evaluation.evidence_items == result.evaluation.assessments == ()
    assert not result.evaluation.analysis.thread_complete
    assert "Sensitive" not in result.model_dump_json()


async def test_scored_service_discards_late_judge_result(
    phase_two_input,
    analysis_payload,
    extraction_payload,
):
    release, finished = Event(), Event()
    scopes = []

    def judge(prompt, data, schema):
        scopes.append(current_model_call.get())
        try:
            release.wait(timeout=2)
            return schema(**judge_proposals(data))
        finally:
            finished.set()

    service = EvaluationService(
        service_model(analysis_payload, extraction_payload),
        judge_llm=judge,
        judge_timeout_seconds=0.05,
    )
    try:
        result = await asyncio.wait_for(score(service, phase_two_input), timeout=1)
        saved = result.model_dump_json()
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait, 1)
    assert scopes[0].abandoned and result.model_dump_json() == saved
    assert result.evaluation.reason_codes == ("judge_timeout",)


async def test_scored_service_cancellation_propagates(
    phase_two_input,
    analysis_payload,
    extraction_payload,
):
    def judge(*args):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await score(
            EvaluationService(service_model(analysis_payload, extraction_payload), judge_llm=judge),
            phase_two_input,
        )


async def test_previous_failure_short_circuits_judge_and_aggregator(phase_two_input):
    def fail(*args):
        raise LLMError("Unavailable")

    result = await score(
        EvaluationService(fail, judge_llm=lambda *_: pytest.fail("called")), phase_two_input
    )
    assert result.evaluation.reason_codes == ("analyzer_model_error",)
    assert result.aggregation is None


async def test_aggregator_validation_failure_uses_safe_recovery(
    phase_two_input,
    analysis_payload,
    extraction_payload,
):
    service = EvaluationService(
        service_model(analysis_payload, extraction_payload),
        judge_llm=lambda p, d, s: s(**judge_proposals(d)),
    )
    result = await score(service, phase_two_input, supersedes_snapshot_id="missing-reason")
    assert result.evaluation.reason_codes == ("aggregator_invalid_input",)
    assert result.resolution is result.aggregation is None
    assert not result.evaluation.analysis.thread_complete


async def test_explicit_prior_evaluation_failure_blocks_completion_and_scores(
    phase_two_input,
    analysis_payload,
    extraction_payload,
):
    service = EvaluationService(
        service_model(analysis_payload, extraction_payload),
        judge_llm=lambda p, d, s: s(**judge_proposals(d)),
    )
    result = await score(service, phase_two_input, evaluation_failure_codes=("prior_timeout",))
    assert not result.evaluation.analysis.thread_complete
    assert result.evaluation.score_snapshot.status == "evaluation_failed"
    assert result.evaluation.score_snapshot.overall_score is None
