"""Durable shadow jobs bound to committed answers, independent of live state CAS."""

from typing import Any

from pydantic import Field

from agents.domain.errors import InvalidAgentState, StateConflictError
from evaluation.contracts import EvaluationModel
from evaluation.persistence import input_for_request, validate_record
from shared.contracts import EvaluationFeedback, EvaluationRequest


class ShadowJob(EvaluationModel):
    request: EvaluationRequest
    feedback: EvaluationFeedback
    context: dict[str, Any]
    assessment_requested: bool = False
    shadow_enabled: bool = True

    def snapshot(self):
        from agents.domain.models import InterviewContext

        return InterviewContext.model_validate(self.context)


class DeferredShadowFeedback(EvaluationFeedback):
    shadow_job: ShadowJob = Field(exclude=True, repr=False)


def validate_shadow_job(request, stored, question):
    job = request.shadow_job
    if job is None:
        if request.new_context is not None and request.new_context.pending_shadow_job is not None:
            raise InvalidAgentState("Shadow job cannot be omitted from its feedback turn")
        return None
    job = ShadowJob.model_validate(job.model_dump())
    entries = request.new_context.question_history if request.new_context else []
    if not entries or request.evaluation_record is not None:
        raise InvalidAgentState("Shadow job requires one atomic legacy feedback turn")
    entry = entries[-1]
    if (
        job.feedback.assessment_status == "pending"
        and not job.assessment_requested
        or job.assessment_requested
        and job.request.request_id not in request.new_context.assessment_feedback_ids
    ):
        raise InvalidAgentState("Pending assessment requires its committed background job")
    if (
        job.context != stored.model_dump(mode="json")
        or job.request.interview_id != stored.interview_id
        or job.request.request_id != request.feedback_request_id
        or job.feedback.request_id != request.feedback_request_id
        or job.feedback.question_id != stored.state.current_question_id
        or question != job.request.question
        or question != entry.question
        or job.request.answer != entry.answer
        or job.feedback != EvaluationFeedback.model_validate(entry.feedback.model_dump())
    ):
        raise InvalidAgentState("Shadow job must match the immutable committed source turn")
    return job


def validate_shadow_record(job, record, stored, prior, jobs):
    """Append in answer order and validate against the source snapshot, never current CAS."""
    job = ShadowJob.model_validate(job.model_dump())
    ids = {r.input.request_id for r in prior}
    pending = [j for j in jobs if j.shadow_enabled and j.request.request_id not in ids]
    if not pending or pending[0] != job:
        raise StateConflictError("Shadow results must be appended in committed answer order")
    if job.request.request_id not in stored.processed_feedback_ids:
        raise InvalidAgentState("Shadow source answer has not been committed")
    frozen = job.snapshot()
    if stored.state.state_version <= frozen.state.state_version or record.mode != "shadow":
        raise InvalidAgentState("Shadow result requires its committed source turn")
    return validate_record(
        record,
        stored=frozen,
        prior=prior,
        question=job.request.question,
        answer=job.request.answer,
        feedback=job.feedback,
    )


def shadow_input(job, records):
    return input_for_request(job.request, job.snapshot(), records)


class MemoryShadowRepository:
    """Shared memory-adapter behavior; publication methods contain no await point."""

    async def get_shadow_jobs(self, interview_id):
        if interview_id not in self.contexts:
            raise InvalidAgentState("Interview context was not found")
        return [
            j.model_copy(deep=True) for j in getattr(self, "_shadow_jobs", {}).get(interview_id, [])
        ]

    async def get_assessment_records(self, interview_id):
        if interview_id not in self.contexts:
            raise InvalidAgentState("Interview context was not found")
        return [
            r.model_copy(deep=True)
            for r in getattr(self, "_assessment_records", {}).get(interview_id, [])
        ]

    async def append_assessment_record(self, job, record):
        from evaluation.assessment import validate_assessment_append

        interview_id = job.request.interview_id
        jobs = getattr(self, "_shadow_jobs", {}).get(interview_id, [])
        canonical = next((j for j in jobs if j.request.request_id == job.request.request_id), None)
        if canonical != job:
            raise InvalidAgentState("Assessment job does not match the saved source")
        if not hasattr(self, "_assessment_records"):
            self._assessment_records = {}
        prior = self._assessment_records.get(interview_id, [])
        existing = next((r for r in prior if r.request_id == record.request_id), None)
        if existing is not None:
            if existing != record:
                raise StateConflictError("Assessment already has different content")
            return
        record = validate_assessment_append(job, record, self.contexts[interview_id], prior, jobs)
        self._assessment_records.setdefault(interview_id, []).append(record)

    def publish_shadow_job(self, job):
        if job is not None:
            if not hasattr(self, "_shadow_jobs"):
                self._shadow_jobs = {}
            self._shadow_jobs.setdefault(job.request.interview_id, []).append(job)

    async def append_shadow_record(self, job, record):
        interview_id = job.request.interview_id
        jobs = getattr(self, "_shadow_jobs", {}).get(interview_id, [])
        canonical = next((j for j in jobs if j.request.request_id == job.request.request_id), None)
        if canonical != job:
            raise InvalidAgentState("Shadow job does not match the saved source")
        prior = self._evaluation_records.get(interview_id, [])
        existing = next((r for r in prior if r.input.request_id == job.request.request_id), None)
        if existing is not None:
            if existing != record:
                raise StateConflictError(
                    "Shadow result is already committed with different content"
                )
            return
        record = validate_shadow_record(job, record, self.contexts[interview_id], prior, jobs)
        self._evaluation_records.setdefault(interview_id, []).append(record)
