"""Apply Evaluation-owned projections; the Agent never recomputes scores."""

from agents.domain.errors import StateConflictError
from evaluation.compatibility import apply_evidence
from evaluation.persistence import EvaluatedFeedback
from shared.contracts import CompetencyState


def apply_evaluation(context, question, answer, feedback):
    if not isinstance(feedback, EvaluatedFeedback):
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
