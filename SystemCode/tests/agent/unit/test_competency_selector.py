"""Regression: assessment dimensions must not determine question selection."""

from agents.config import load_agent_settings
from agents.domain.models import InterviewContext
from agents.policies.dialogue_policy import choose_dialogue
from tests.agent.factories import interview_plan, interview_state
from tests.agent.integration.test_question_pipeline_integration import pipeline_request


def context():
    request = pipeline_request()
    return InterviewContext(
        interview_id="interview-1",
        candidate_profile=request.candidate_profile,
        job_profile=request.job_profile,
        plan=interview_plan(),
        state=interview_state(),
        policy_config_version="dialogue-policy-v2",
    )


def test_scores_and_coverage_cannot_change_project_or_topic():
    original = context()
    changed = original.model_copy(deep=True)
    for dimension in changed.state.competencies.values():
        dimension.score = 5
        dimension.coverage = 1
        dimension.evidence_count = 20
    changed.job_profile.competency_importance = {}
    assert choose_dialogue(original, load_agent_settings()) == choose_dialogue(
        changed, load_agent_settings()
    )


def test_question_plan_excludes_assessment_targets():
    plan = interview_plan().model_dump()
    assert not {"competency_importance", "target_coverage", "target_confidence"} & plan.keys()
