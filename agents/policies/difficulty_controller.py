"""Bounded question difficulty adaptation from Evaluation feedback."""

from agents.config import AgentSettings, load_agent_settings
from shared.contracts import EvaluationFeedback


class DifficultyController:
    def __init__(self, settings: AgentSettings | None = None) -> None:
        self._settings = settings or load_agent_settings()

    def adjust(self, current_difficulty: int, feedback: EvaluationFeedback) -> int:
        rules = self._settings.difficulty
        if (
            feedback.evidence_strength >= rules.strong_evidence_threshold
            and feedback.evaluation_confidence >= rules.strong_confidence_threshold
            and feedback.answer_relevance >= rules.strong_relevance_threshold
        ):
            return min(current_difficulty + 1, self._settings.max_question_difficulty)
        if (
            feedback.evidence_strength <= rules.weak_evidence_threshold
            and feedback.answer_relevance >= rules.minimum_relevance_to_decrease
        ):
            return max(current_difficulty - 1, self._settings.min_question_difficulty)
        return min(
            max(current_difficulty, self._settings.min_question_difficulty),
            self._settings.max_question_difficulty,
        )
