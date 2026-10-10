"""Legacy feedback drives the live interview; shadow scoring consumes saved jobs."""

import asyncio

from agents.domain.errors import InvalidAgentState, StateConflictError
from agents.tracing import emit_trace
from evaluation.aggregation import ScoredEvaluation
from evaluation.background import DeferredShadowFeedback, ShadowJob, shadow_input
from evaluation.contracts import EvaluationResult
from evaluation.persistence import EvaluationRecord
from shared.contracts import AnswerAnalysis, EvaluationFeedback


class BackgroundShadowEvaluationAdapter:
    def __init__(self, legacy, formal, *, assessment=None, repository=None):
        self.legacy, self.formal = legacy, formal
        self.assessment = assessment
        self._repository = repository
        self._workers = {}
        self._assessment_workers = {}
        self._wake_versions = {}

    @property
    def repository(self):
        return self.formal.repository if self.formal is not None else self._repository

    async def evaluate(self, request):
        repository = self.repository
        context = await repository.get_interview_context(request.interview_id)
        jobs = await repository.get_shadow_jobs(request.interview_id)
        cached = next((j for j in jobs if j.request.request_id == request.request_id), None)
        if cached is not None:
            if cached.request != request:
                raise InvalidAgentState("Shadow request ID already belongs to another input")
            return EvaluationFeedback.model_validate(cached.feedback.model_dump())
        if request.request_id in context.processed_feedback_ids:
            raise StateConflictError("Legacy feedback is already committed; reuse its saved action")
        question = await repository.get_question(request.question.question_id)
        if (
            question != request.question
            or context.state.current_question_id != question.question_id
        ):
            raise InvalidAgentState("Evaluation requires the current immutable question")
        feedback = await self.legacy.evaluate(request)
        job = ShadowJob(
            request=request.model_copy(deep=True),
            feedback=EvaluationFeedback.model_validate(feedback.model_dump()),
            context=context.model_dump(mode="json"),
            assessment_requested=self.assessment is not None,
            shadow_enabled=self.formal is not None,
        )
        return DeferredShadowFeedback(**feedback.model_dump(), shadow_job=job)

    def start_background(self, interview_id):
        """Call after the source turn commits; exactly one worker per local interview."""
        self._wake_versions[interview_id] = self._wake_versions.get(interview_id, 0) + 1
        worker = self._workers.get(interview_id)
        if self.formal is not None and (worker is None or worker.done()):
            worker = asyncio.create_task(self._run(interview_id), name=f"shadow:{interview_id}")
            self._workers[interview_id] = worker
        assessment_worker = self._assessment_workers.get(interview_id)
        if self.assessment is not None and (assessment_worker is None or assessment_worker.done()):
            assessment_worker = asyncio.create_task(
                self._run_assessment(interview_id), name=f"assessment:{interview_id}"
            )
            self._assessment_workers[interview_id] = assessment_worker
        return worker if worker is not None else assessment_worker

    async def _run_assessment(self, interview_id):
        try:
            while True:
                wake_version = self._wake_versions[interview_id]
                records = await self.repository.get_assessment_records(interview_id)
                jobs = await self.repository.get_shadow_jobs(interview_id)
                done = {r.request_id for r in records}
                job = next(
                    (
                        j
                        for j in jobs
                        if j.assessment_requested and j.request.request_id not in done
                    ),
                    None,
                )
                if job is None:
                    if wake_version != self._wake_versions[interview_id]:
                        continue
                    return
                emit_trace("evaluation.assessment_started", request_id=job.request.request_id)
                record = await self.assessment.assess(job)
                await self.repository.append_assessment_record(job, record)
                emit_trace(
                    "evaluation.assessment_completed",
                    request_id=record.request_id,
                    status=record.assessment_status,
                    assessment=record.model_dump(mode="json"),
                )
        except asyncio.CancelledError:
            emit_trace(
                "evaluation.assessment_pending",
                interview_id=interview_id,
                reason_code="WORKER_CANCELLED_JOB_RETAINED",
            )
            raise
        except Exception as error:
            emit_trace(
                "evaluation.assessment_pending",
                interview_id=interview_id,
                reason_code="WORKER_FAILED_JOB_RETAINED",
                error_type=type(error).__name__,
            )

    async def _run(self, interview_id):
        try:
            while True:
                wake_version = self._wake_versions[interview_id]
                records = await self.repository.get_evaluation_records(interview_id)
                jobs = await self.repository.get_shadow_jobs(interview_id)
                done = {r.input.request_id for r in records}
                job = next(
                    (j for j in jobs if j.shadow_enabled and j.request.request_id not in done), None
                )
                if job is None:
                    if wake_version != self._wake_versions[interview_id]:
                        continue
                    return
                context = job.snapshot()
                inputs = shadow_input(job, records)
                emit_trace("evaluation.shadow_started", request_id=job.request.request_id)
                try:
                    scored = await self.formal._score(context, inputs, records, job.feedback)
                except Exception:
                    scored = ScoredEvaluation(
                        evaluation=EvaluationResult(
                            request_id=inputs.request_id,
                            interview_id=inputs.interview_id,
                            question_id=inputs.question.question_id,
                            answer_id=inputs.answer.answer_id,
                            status="failed",
                            reason_codes=("shadow_internal_error",),
                            analysis=AnswerAnalysis(status="partial"),
                        )
                    )
                record = EvaluationRecord(
                    mode="shadow",
                    base_state_version=context.state.state_version,
                    input=inputs,
                    feedback=job.feedback,
                    scored=scored,
                )
                await self.repository.append_shadow_record(job, record)
                emit_trace(
                    "evaluation.shadow_completed",
                    request_id=job.request.request_id,
                    status=scored.evaluation.status,
                    reason_codes=scored.evaluation.reason_codes,
                )
        except asyncio.CancelledError:
            emit_trace(
                "evaluation.shadow_pending",
                interview_id=interview_id,
                reason_code="WORKER_CANCELLED_JOB_RETAINED",
            )
            raise
        except Exception as error:
            # Persistence is isolated from live feedback. Never silently claim a job completed.
            emit_trace(
                "evaluation.shadow_pending",
                interview_id=interview_id,
                reason_code="WORKER_FAILED_JOB_RETAINED",
                error_type=type(error).__name__,
            )

    async def drain(self, interview_id, *, timeout_seconds=60):
        self.start_background(interview_id)
        workers = {
            pool[interview_id]
            for pool in (self._workers, self._assessment_workers)
            if interview_id in pool
        }
        if not workers:
            return True
        _done, pending = await asyncio.wait(workers, timeout=timeout_seconds)
        if pending:
            emit_trace(
                "evaluation.shadow_pending",
                interview_id=interview_id,
                reason_code="FINALIZATION_TIMEOUT",
            )
            return False
        try:
            records = await self.repository.get_evaluation_records(interview_id)
            jobs = await self.repository.get_shadow_jobs(interview_id)
            assessments = await self.repository.get_assessment_records(interview_id)
        except Exception as error:
            emit_trace(
                "evaluation.shadow_pending",
                interview_id=interview_id,
                reason_code="FINALIZATION_FAILED_JOB_RETAINED",
                error_type=type(error).__name__,
            )
            return False
        completed = {r.input.request_id for r in records}
        assessed = {r.request_id for r in assessments}
        return all(
            (not j.shadow_enabled or j.request.request_id in completed)
            and (not j.assessment_requested or j.request.request_id in assessed)
            for j in jobs
        )

    async def close(self):
        workers = [*self._workers.values(), *self._assessment_workers.values()]
        for worker in workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()
        self._assessment_workers.clear()
        self._wake_versions.clear()
