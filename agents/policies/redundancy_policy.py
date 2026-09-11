"""First-pass exact redundancy detection without semantic embeddings."""

from collections.abc import Iterable

from agents.config import AgentSettings, load_agent_settings
from shared.contracts import Competency, PlannedQuestion, QuestionType

QuestionKey = tuple[Competency, str | None, QuestionType]


class RedundancyPolicy:
    def __init__(self, settings: AgentSettings | None = None) -> None:
        self._settings = settings or load_agent_settings()

    @staticmethod
    def key(question: PlannedQuestion) -> QuestionKey:
        return (question.target_competency, question.topic, question.question_type)

    def is_redundant(
        self,
        candidate: PlannedQuestion,
        previous_questions: Iterable[PlannedQuestion],
    ) -> bool:
        candidate_key = self.key(candidate)
        occurrences = sum(
            1
            for previous_question in previous_questions
            if self.key(previous_question) == candidate_key
        )
        return occurrences >= self._settings.redundancy.maximum_key_occurrences
