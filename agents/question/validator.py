"""Cheap structural and information-leak checks for generated wording."""

from __future__ import annotations

import re

from agents.config import AgentSettings, load_agent_settings
from agents.domain.models import QuestionValidationResult
from shared.contracts import PlannedQuestion

_RUBRIC_LEAK_PATTERNS = (
    re.compile(r"\bto (?:get|receive|earn) (?:a )?level [1-5]\b", re.IGNORECASE),
    re.compile(r"\bthe expected answer is\b", re.IGNORECASE),
    re.compile(r"\byou should mention\b", re.IGNORECASE),
    re.compile(r"\bscoring (?:criteria|rubric)\b", re.IGNORECASE),
    re.compile(r"\bexpected signals?\b", re.IGNORECASE),
)

_IMMUTABLE_FIELDS = (
    "target_competency",
    "project_id",
    "topic",
    "difficulty",
    "probe_depth",
    "question_type",
    "intent",
    "required_context_sources",
)


class QuestionValidator:
    def __init__(self, settings: AgentSettings | None = None) -> None:
        self._settings = settings or load_agent_settings()

    def validate(
        self,
        question: PlannedQuestion,
        original_plan: PlannedQuestion | None = None,
    ) -> QuestionValidationResult:
        errors: list[str] = []
        text = (question.text or "").strip()
        rules = self._settings.question_validation
        if not text:
            errors.append("EMPTY_TEXT")
        else:
            word_count = len(text.split())
            if word_count < rules.minimum_words:
                errors.append("TOO_SHORT")
            if word_count > rules.maximum_words:
                errors.append("TOO_LONG")
            if text.count("?") > rules.maximum_question_marks:
                errors.append("MULTIPLE_PRIMARY_QUESTIONS")
            if any(pattern.search(text) for pattern in _RUBRIC_LEAK_PATTERNS):
                errors.append("RUBRIC_OR_EXPECTED_ANSWER_LEAK")

        if not 1 <= question.difficulty <= 5:
            errors.append("INVALID_DIFFICULTY")
        if original_plan is not None:
            for field_name in _IMMUTABLE_FIELDS:
                if getattr(question, field_name) != getattr(original_plan, field_name):
                    errors.append(f"CHANGED_{field_name.upper()}")
        return QuestionValidationResult(is_valid=not errors, errors=errors)

    def is_valid(
        self,
        question: PlannedQuestion,
        original_plan: PlannedQuestion | None = None,
    ) -> bool:
        return self.validate(question, original_plan).is_valid
