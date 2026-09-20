"""Adjust conversation difficulty using substantive answer evidence."""

from agents.config import AgentSettings, load_agent_settings
from shared.contracts import EvaluationFeedback


class DifficultyController:
    def __init__(self, settings: AgentSettings | None = None):
        self._settings = settings or load_agent_settings()

    def adjust(self, current_difficulty: int, feedback: EvaluationFeedback) -> int:
        if feedback.analysis.status in {"partial", "non_answer"}:
            return max(self._settings.min_question_difficulty, current_difficulty - 1)
        rules = self._settings.difficulty
        if (
            feedback.analysis.status == "substantive"
            and feedback.evidence_strength >= rules.strong_evidence_threshold
            and feedback.answer_relevance >= rules.strong_relevance_threshold
        ):
            return min(current_difficulty + 1, self._settings.max_question_difficulty)
        return current_difficulty
