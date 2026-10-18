"""Resolve CLI/web question budgets and the configured retrieval deadline before provider I/O."""

import os

from agents.config import AgentSettings, load_agent_settings


def interview_settings(
    *,
    max_questions=None,
    max_questions_per_project=None,
    max_questions_per_topic=None,
    max_follow_up_per_topic=None,
):
    if max_follow_up_per_topic is not None:
        if max_follow_up_per_topic < 0:
            raise ValueError("max_follow_up_per_topic must be nonnegative")
        if max_questions_per_topic is not None:
            raise ValueError("Choose max_questions_per_topic OR max_follow_up_per_topic, not both")
        max_questions_per_topic = max_follow_up_per_topic + 1
    values = load_agent_settings().model_dump()
    # CLI, backend and offline preparation share the same lightweight review.
    # The core's strict profile remains available for evidence-contract regressions.
    values["question_agent"]["quality_mode"] = os.getenv("QUESTION_QUALITY_MODE", "relaxed")
    if values["question_agent"]["quality_mode"] == "relaxed":
        # Two related sentences can ask one answer target. Semantic review, rather
        # than punctuation alone, rejects overloaded questions in this profile.
        values["question_validation"]["maximum_question_marks"] = 2
    if "RAG_TIMEOUT_SECONDS" in os.environ:
        timeout = float(os.environ["RAG_TIMEOUT_SECONDS"])
        if not 0 < timeout <= 30:
            raise ValueError("RAG_TIMEOUT_SECONDS must be between 0 and 30")
        values["timeouts"]["rag_seconds"] = timeout
    if max_questions is not None:
        values["max_questions"] = max_questions
    if max_questions_per_project is not None:
        values["max_questions_per_project"] = max_questions_per_project
    if max_questions_per_topic is not None:
        values["max_questions_per_topic"] = max_questions_per_topic
    return AgentSettings.model_validate(values)
