"""Resolve CLI/web question budget overrides once, before calling any provider."""

from agents.config import AgentSettings, load_agent_settings


def interview_settings(
    *, max_questions_per_project=None, max_questions_per_topic=None, max_follow_up_per_topic=None
):
    if max_follow_up_per_topic is not None:
        if max_follow_up_per_topic < 0:
            raise ValueError("max_follow_up_per_topic must be nonnegative")
        if max_questions_per_topic is not None:
            raise ValueError("Choose max_questions_per_topic OR max_follow_up_per_topic, not both")
        max_questions_per_topic = max_follow_up_per_topic + 1
    values = load_agent_settings().model_dump()
    if max_questions_per_project is not None:
        values["max_questions_per_project"] = max_questions_per_project
    if max_questions_per_topic is not None:
        values["max_questions_per_topic"] = max_questions_per_topic
    return AgentSettings.model_validate(values)
