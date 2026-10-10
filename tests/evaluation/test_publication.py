"""Report publication distinguishes supported numbers from missing or failed evidence."""

from types import SimpleNamespace

import pytest

from agents.config import load_agent_settings
from agents.policies.score_coverage import coverage_question
from evaluation.aggregator import aggregate_scores
from evaluation.publication import formal_publication
from tests.evaluation.resolver_helpers import assessment, decision, resolve, source
from tests.evaluation.scoring_helpers import all_assessments, scoring_input
from tests.evaluation.test_background_shadow import commit, prepared


def example(*, complete=False, thresholds=None):
    item = source("a")
    scores = (
        all_assessments(item)
        if complete
        else (assessment("a", item, criterion="ownership.personal_contribution"),)
    )
    aggregate = aggregate_scores(
        scoring_input(resolve((item, decision(item))), *scores, thresholds=thresholds)
    )
    context = SimpleNamespace(processed_feedback_ids=["request"], unobserved_feedback_ids=[])
    record = SimpleNamespace(
        input=SimpleNamespace(request_id="request"),
        scored=SimpleNamespace(
            aggregation=aggregate, evaluation=SimpleNamespace(status="completed")
        ),
    )
    return context, record


def test_partial_evidence_publishes_provisional_number_without_mutating_formal_thresholds():
    context, record = example(thresholds={"min_competency_coverage": 0.6})
    original = record.scored.aggregation.model_dump_json()
    result = formal_publication(context, [record])
    assert result["overall_score"] == 3
    assert result["score_status"] == "provisional"
    assert result["competencies"]["ownership"]["status"] == "provisional"
    assert result["competencies"]["technical_depth"]["score"] is None
    assert record.scored.aggregation.model_dump_json() == original
    assert record.scored.aggregation.snapshot.overall_score is None


def test_complete_evidence_publishes_the_exact_formal_score():
    context, record = example(complete=True)
    result = formal_publication(context, [record])
    assert result["score_status"] == "published"
    assert result["overall_score"] == record.scored.aggregation.snapshot.overall_score == 4
    assert not result["missing_competencies"]


@pytest.mark.parametrize("failure", ["failed", "none", "weak"])
def test_missing_failed_or_below_threshold_evidence_never_invents_a_score(failure):
    context, record = example(
        thresholds={"min_effective_weight": 2.0} if failure == "weak" else None
    )
    records = [record]
    if failure == "failed":
        record.scored.evaluation.status = "failed"
    if failure == "none":
        records = []
    result = formal_publication(context, records)
    assert result["overall_score"] is None
    assert result["score_status"] == "unavailable"


async def test_gap_questions_are_bounded_nonrepeating_and_preserve_explicit_finish():
    agent, repo, shadow = await prepared()
    agent._evaluation = shadow
    try:
        await commit(agent, repo, shadow, 1)
        assert await shadow.drain("pipeline-interview", timeout_seconds=3)
        context = await repo.get_interview_context("pipeline-interview")
        record = (await repo.get_evaluation_records(context.interview_id))[-1]
        settings = load_agent_settings()
        first = coverage_question(context, record.scored.aggregation, settings)
        assert first is not None
        context.coverage_question_criteria = [first[0]]
        second = coverage_question(context, record.scored.aggregation, settings)
        assert second is None or second[0] != first[0]
        context.coverage_question_criteria = [
            str(i) for i in range(settings.scoring.max_supplemental_questions)
        ]
        assert coverage_question(context, record.scored.aggregation, settings) is None
        action = await agent.finish_interview(context.interview_id)
        assert action.type == "finish"
        assert action.decision_trace.reason_code == "USER_FINISHED"
    finally:
        await shadow.close()


async def test_soft_finish_commits_gap_question_atomically_and_hard_limits_win():
    agent, repo, shadow = await prepared()
    agent._evaluation = shadow
    try:
        await commit(agent, repo, shadow, 1)
        assert await shadow.drain("pipeline-interview", timeout_seconds=3)
        context = await repo.get_interview_context("pipeline-interview")
        action = await agent._finish(
            context, feedback_request_id=None, started_at=0, reason="NO_MORE_TOPICS"
        )
        assert action.type == "ask_question"
        updated = await repo.get_interview_context(context.interview_id)
        assert len(updated.coverage_question_criteria) == 1
        assert updated.state.question_index == context.state.question_index + 1
        assert await repo.get_question(action.question.question_id) == action.question
        for exhausted in ("time", "count"):
            limited = updated.model_copy(deep=True)
            if exhausted == "time":
                limited.state.remaining_seconds = 0
                limited.state.elapsed_seconds = limited.plan.duration_seconds
            else:
                limited.state.question_index = limited.plan.max_questions
            assert await agent._coverage_question(limited, None, 0) is None
    finally:
        await shadow.close()


@pytest.mark.parametrize("failure", ["pending", "last_failed", "historical_failure"])
def test_one_unscored_turn_preserves_valid_scores_as_explicitly_partial(failure):
    context, record = example(complete=True)
    context.processed_feedback_ids.append("later")
    records = [record]
    if failure == "last_failed":
        records.append(
            SimpleNamespace(
                input=SimpleNamespace(request_id="later"),
                scored=SimpleNamespace(
                    aggregation=None, evaluation=SimpleNamespace(status="failed")
                ),
            )
        )
    elif failure == "historical_failure":
        record.scored.aggregation = aggregate_scores(
            record.scored.aggregation.inputs.model_copy(
                update={"evaluation_failure_codes": ("unassessed_feedback:later",)}
            )
        )
    original = record.scored.aggregation.model_dump_json()
    result = formal_publication(context, records)
    assert result["score_status"] == "provisional"
    assert result["overall_score"] == 4
    assert result["unscored_answer_count"] == 1
    assert "unassessed_feedback:later" in result["score_reasons"]
    assert result["competencies"]["debugging"]["status"] == "provisional"
    assert record.scored.aggregation.model_dump_json() == original


def test_partial_projection_still_enforces_criterion_quality():
    context, record = example(thresholds={"min_effective_weight": 2.0})
    context.processed_feedback_ids.append("later")
    record.scored.aggregation = aggregate_scores(
        record.scored.aggregation.inputs.model_copy(
            update={"evaluation_failure_codes": ("unassessed_feedback:later",)}
        )
    )
    result = formal_publication(context, [record])
    assert result["overall_score"] is None
    assert result["unscored_answer_count"] == 1


def test_unknown_evaluation_failure_is_not_bypassed():
    context, record = example(complete=True)
    record.scored.aggregation = aggregate_scores(
        record.scored.aggregation.inputs.model_copy(
            update={"evaluation_failure_codes": ("invalid_evidence",)}
        )
    )
    assert formal_publication(context, [record])["overall_score"] is None
