"""Grounded, append-only capability scores; dialogue decisions remain immutable."""

from typing import Literal

from pydantic import Field

from agents.domain.errors import InvalidAgentState, StateConflictError
from evaluation.contracts import EvaluationModel
from shared.contracts import DimensionEvidence, EvaluationFeedback


class AssessmentRecord(EvaluationModel):
    request_id: str
    interview_id: str
    question_id: str
    answer_id: str
    base_state_version: int = Field(ge=0)
    assessment_status: Literal["valid", "unavailable"]
    evidence_strength: float = Field(default=0, ge=0, le=1)
    dimensions: list[DimensionEvidence] = Field(default_factory=list)
    evaluation_issues: list[str] = Field(default_factory=list)

    def feedback(self, job):
        return EvaluationFeedback.model_validate(
            {
                **job.feedback.model_dump(),
                "assessment_status": self.assessment_status,
                "dimensions": [d.model_dump() for d in self.dimensions],
                "evidence_strength": self.evidence_strength,
                "evidence_ids": [
                    f"evidence-{self.answer_id}-{d.competency.value}" for d in self.dimensions
                ],
                "evaluation_issues": [*job.feedback.evaluation_issues, *self.evaluation_issues],
            }
        )


def validate_assessment(job, record):
    """Validate source identity and exact quotes again at publication/projection boundaries."""
    record = AssessmentRecord.model_validate(record.model_dump())
    request = job.request
    if not job.assessment_requested or (
        record.request_id,
        record.interview_id,
        record.question_id,
        record.answer_id,
        record.base_state_version,
    ) != (
        request.request_id,
        request.interview_id,
        request.question.question_id,
        request.answer.answer_id,
        job.snapshot().state.state_version,
    ):
        raise InvalidAgentState("Assessment must match its saved source turn")
    if record.assessment_status != "valid" and (record.dimensions or record.evidence_strength):
        raise InvalidAgentState("Failed assessment cannot publish capability evidence")
    forbidden = job.feedback.analysis_status == "valid" and (
        job.feedback.analysis.status in {"non_answer", "explicit_unknown", "refusal"}
        or job.feedback.analysis.answer_scope in {"none", "label_only"}
    )
    if forbidden and record.dimensions:
        raise InvalidAgentState("Non-answers cannot publish capability evidence")
    from app.adapters.evaluation import answer_segments, grounded_dimensions

    grounded = grounded_dimensions(
        record.dimensions,
        request.answer.text,
        answer_segments(request.answer.answer_id, request.answer.text),
    )
    if grounded != record.dimensions or len({d.competency for d in grounded}) != len(grounded):
        raise InvalidAgentState("Assessment evidence is not in canonical grounded form")
    return record


def validate_assessment_append(job, record, context, prior, jobs):
    pending = [
        j
        for j in jobs
        if j.assessment_requested and j.request.request_id not in {r.request_id for r in prior}
    ]
    if not pending or pending[0] != job:
        raise StateConflictError("Assessments must be appended in answer order")
    if job.request.request_id not in context.processed_feedback_ids:
        raise InvalidAgentState("Assessment source is not committed")
    if context.state.state_version <= job.snapshot().state.state_version:
        raise InvalidAgentState("Assessment requires a committed source version")
    return validate_assessment(job, record)


def project_assessments(context, jobs, records):
    """Pure derived view; use at the next atomic turn or final report, never inside a worker."""
    from evaluation.compatibility import apply_evidence, rebuild_scores

    updated = context.model_copy(deep=True)
    by_id = {j.request.request_id: j for j in jobs}
    applied = []
    for record in records:
        if record.request_id in updated.applied_assessment_ids:
            continue
        job = by_id.get(record.request_id)
        if (
            job is None
            or record.interview_id != updated.interview_id
            or record.request_id not in updated.processed_feedback_ids
        ):
            raise InvalidAgentState("Assessment cannot project onto a different source/interview")
        record = validate_assessment(job, record)
        feedback = record.feedback(job)
        # Corrections were validated on the live path. Do not replay their dialogue side effects.
        evidence_feedback = feedback.model_copy(deep=True)
        evidence_feedback.analysis.answer_relations = []
        apply_evidence(updated, job.request.question, job.request.answer, evidence_feedback)
        for entry in updated.question_history:
            if entry.feedback.request_id == record.request_id:
                entry.feedback = feedback
        for index, previous in enumerate(updated.recent_feedback):
            if previous.request_id == record.request_id:
                updated.recent_feedback[index] = feedback
        if record.assessment_status == "valid":
            updated.unassessed_answer_ids = [
                a for a in updated.unassessed_answer_ids if a != record.answer_id
            ]
            updated.state.evidence_ids = list(
                dict.fromkeys([*updated.state.evidence_ids, *feedback.evidence_ids])
            )
        updated.applied_assessment_ids.append(record.request_id)
        applied.append((job.request.question, feedback))
    if not applied:
        return updated, applied
    from evaluation.compatibility import retire_related_evidence

    retire_related_evidence(updated)
    rebuild_scores(updated)
    return updated, applied


def update_display_history(history, context):
    """Expose latest assessments without changing the immutable question/answer or decisions."""
    feedback_by_question = {e.question.question_id: e.feedback for e in context.question_history}
    for entry in history:
        feedback = feedback_by_question.get(entry["question_id"])
        if feedback is not None:
            entry["evaluation"] = feedback.model_dump(
                mode="json",
                include={
                    "analysis",
                    "dimensions",
                    "answer_relevance",
                    "evidence_strength",
                    "evidence_ids",
                    "analysis_status",
                    "assessment_status",
                },
            )


async def assessed_report_context(repository, context):
    """Report completed scores; unavailable storage leaves answers explicitly unassessed."""
    if not context.assessment_feedback_ids:
        return context
    try:
        jobs = await repository.get_shadow_jobs(context.interview_id)
        records = await repository.get_assessment_records(context.interview_id)
        return project_assessments(context, jobs, records)[0]
    except Exception as error:
        from agents.tracing import emit_trace

        emit_trace(
            "evaluation.assessment_report_unavailable",
            interview_id=context.interview_id,
            error_type=type(error).__name__,
        )
        return context
