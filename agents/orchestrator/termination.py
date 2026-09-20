"""End on budget or terminal stage; assessment dimensions never drive termination."""

from shared.contracts import InterviewStage


class TerminationPolicy:
    def __init__(self, settings=None):
        pass

    def should_finish(self, state, plan=None) -> bool:
        return state.remaining_seconds <= 0 or state.stage == InterviewStage.FINISHED

    @staticmethod
    def reason_code(state):
        return "TIME_EXHAUSTED" if state.remaining_seconds <= 0 else "STAGE_FINISHED"
