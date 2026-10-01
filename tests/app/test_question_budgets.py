"""Entry-point configuration uses one canonical pair of limits."""

import pytest

from agents.config import AgentSettings
from app.application import build_application
from app.cli import run_interview
from app.settings import interview_settings
from shared.contracts import InterviewPlan
from tests.app.fixtures import FixtureLLM
from tests.app.test_interview import RESUME


@pytest.mark.parametrize(
    "limits",
    [
        {"max_questions_per_project": 0},
        {"max_questions_per_topic": 0},
        {"max_questions_per_project": -1},
        {"max_questions_per_topic": 2, "max_follow_up_per_topic": 1},
    ],
)
def test_invalid_budgets_fail_before_model_call(limits):
    model = FixtureLLM()
    with pytest.raises(ValueError):
        run_interview(
            build_application(model),
            RESUME,
            **limits,
            read_answer=lambda _: "unused",
            write=lambda _: None,
        )
    assert model.calls == []


def test_old_followup_option_converts_to_total_once():
    settings = interview_settings(max_follow_up_per_topic=2, max_questions_per_project=4)
    assert settings.max_questions_per_topic == 3
    assert settings.max_questions_per_project == 4
    assert "max_consecutive_probes" not in settings.model_dump()
    assert interview_settings(max_follow_up_per_topic=0).max_questions_per_topic == 1
    plan = InterviewPlan.model_validate(
        {
            "interview_id": "old",
            "duration_seconds": 600,
            "stages": [],
            "max_consecutive_probes": 1,
        }
    )
    assert plan.max_questions_per_topic == 2
    assert "max_consecutive_probes" not in plan.model_dump()
    assert AgentSettings(max_consecutive_probes=0).max_questions_per_topic == 1
