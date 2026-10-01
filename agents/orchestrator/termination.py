"""End on budget or terminal stage; assessment dimensions never drive termination."""

from shared.contracts import InterviewStage


class TerminationPolicy:
    def __init__(self, settings=None):
        self.minimum_question_seconds = (
            settings.planning.minimum_question_seconds if settings else 30
        )

    def should_finish(self, state, plan=None) -> bool:
        return (
            state.remaining_seconds <= 0
            or state.stage == InterviewStage.FINISHED
            or (plan is not None and state.question_index >= plan.max_questions)
            or (
                plan is not None
                and plan.planning_enabled
                and state.remaining_seconds < plan.closing_seconds + self.minimum_question_seconds
            )
        )

    @staticmethod
    def reason_code(state, plan=None):
        if state.remaining_seconds <= 0:
            return "TIME_EXHAUSTED"
        if plan is not None and state.question_index >= plan.max_questions:
            return "QUESTION_SAFETY_LIMIT"
        if plan is not None and plan.planning_enabled:
            return "INSUFFICIENT_TIME_FOR_QUESTION"
        return "STAGE_FINISHED"
