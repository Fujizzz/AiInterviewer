"""Fixed, unscored opening and validation of its atomic answer receipt."""

from agents.domain.errors import InvalidAgentState
from shared.contracts import InterviewStage

INTRODUCTION_TEXT = "Please briefly introduce yourself."
INTRODUCTION_TOPIC = "Self introduction"


def validate_introduction_commit(request, stored):
    """Only the current fixed opening may commit an answer without evaluation or scoring."""
    answer = request.unscored_answer
    if answer is None:
        return
    if (
        stored.introduction_question_id is None
        or stored.state.stage != InterviewStage.INTRO
        or stored.introduction_answer is not None
        or answer.interview_id != stored.interview_id
        or answer.question_id != stored.introduction_question_id
        or answer.question_id != stored.state.current_question_id
        or request.feedback_request_id is None
        or request.evaluation_record is not None
        or request.shadow_job is not None
        or request.new_context is None
        or request.new_context.introduction_answer != answer
        or request.new_context.introduction_question_id != stored.introduction_question_id
        or request.new_context.question_history != stored.question_history
        or request.new_state.competencies != stored.state.competencies
        or request.new_context.evidence_records != stored.evidence_records
    ):
        raise InvalidAgentState("Unscored answer must match the current introduction")
