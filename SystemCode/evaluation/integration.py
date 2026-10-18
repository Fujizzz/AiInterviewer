"""Apply Evaluation-owned projections; the Agent never recomputes scores."""

from agents.domain.errors import StateConflictError
from evaluation.background import DeferredShadowFeedback
from evaluation.compatibility import apply_evidence
from evaluation.persistence import EvaluatedFeedback
from shared.contracts import CompetencyState


def apply_evaluation(context, question, answer, feedback):
    if isinstance(feedback, DeferredShadowFeedback):
        context.pending_shadow_job = feedback.shadow_job
        if feedback.shadow_job.assessment_requested:
            context.assessment_feedback_ids = list(
                dict.fromkeys([*context.assessment_feedback_ids, feedback.request_id])
            )
        apply_evidence(context, question, answer, feedback)
        return
    if not isinstance(feedback, EvaluatedFeedback):
        # Silent skips intentionally bypass model evaluation. Keep their identities in
        # committed state so later scoring does not mistake them for lost evaluations.
        if (
            answer is None
            and feedback.analysis.status == "non_answer"
            and feedback.analysis.answer_scope == "none"
            and not feedback.analysis.new_information
            and not feedback.dimensions
            and not feedback.evidence_ids
            and feedback.request_id not in context.unobserved_feedback_ids
        ):
            context.unobserved_feedback_ids.append(feedback.request_id)
        # Contract 2.0 callers remain supported by the Evaluation compatibility layer.
        apply_evidence(context, question, answer, feedback)
        return
    record = feedback.evaluation_record
    if record.base_state_version != context.state.state_version:
        raise StateConflictError("Evaluation base_state_version is stale; evaluate again")
    context.pending_evaluation = record
    if record.mode == "shadow":
        apply_evidence(context, question, answer, feedback)
        return
    snapshot = record.scored.evaluation.score_snapshot
    if snapshot is None:
        return
    for score in snapshot.competencies:
        contributions = [c for criterion in score.criteria for c in criterion.contributions]
        context.state.competencies[score.competency] = CompetencyState(
            competency=score.competency,
            score=score.score,
            coverage=score.coverage,
            evidence_count=len({e for c in contributions for e in c.evidence_ids}),
            independent_evidence_count=len({c.independence_group_id for c in contributions}),
            last_asked_at_question_index=context.state.question_index,
        )
