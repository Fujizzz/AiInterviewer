"""A deployment may tune CPU retrieval latency, without accepting an unbounded deadline."""

import pytest

from app.settings import interview_settings


@pytest.mark.parametrize("value", ["0", "-1", "31", "nan", "inf", "bad"])
def test_invalid_retrieval_deadlines_fail_before_provider_io(monkeypatch, value):
    monkeypatch.setenv("RAG_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError):
        interview_settings()


def test_cpu_retrieval_override_keeps_question_budgets(monkeypatch):
    monkeypatch.setenv("RAG_TIMEOUT_SECONDS", "5")
    settings = interview_settings(max_questions=8, max_questions_per_project=3)
    assert settings.timeouts.rag_seconds == 5
    assert settings.max_questions == 8
    assert settings.max_questions_per_project == 3
