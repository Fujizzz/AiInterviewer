"""Compare information goals rather than competency labels."""

from shared.contracts import PlannedQuestion


class RedundancyPolicy:
    @staticmethod
    def key(question: PlannedQuestion):
        return (
            question.project_id,
            question.topic_key,
            question.information_goal.casefold().strip(),
        )

    def is_redundant(self, question: PlannedQuestion, previous_questions) -> bool:
        return any(self.key(question) == self.key(previous) for previous in previous_questions)
