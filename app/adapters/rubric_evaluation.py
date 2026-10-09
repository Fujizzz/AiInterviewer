"""Formal EvaluationPort and a shadow rollout preserving contract 2.0/UI output."""

import asyncio
import os
from pathlib import Path

from agents.domain.errors import InvalidAgentState, StateConflictError
from agents.tracing import emit_trace
from app.adapters.assessment import BackgroundAssessmentAdapter, RealtimeDecisionAdapter
from app.adapters.background_evaluation import BackgroundShadowEvaluationAdapter
from evaluation.aggregation import ScoredEvaluation
from evaluation.contracts import EvaluationResult
from evaluation.inputs import AnswerSnapshot, QuestionSnapshot
from evaluation.persistence import (
    EvaluatedFeedback,
    EvaluationRecord,
    failure_codes,
    input_for_request,
    scoring_history,
)
from evaluation.policy import AggregationPolicy, ScoringProfile
from evaluation.rubric import load_rubric_pack
from evaluation.service import EvaluationService
from shared.contracts import AnswerAnalysis, Competency, EvaluationFeedback


class RubricEvaluationAdapter:
    """A formal port; explicit policy/profile required for non-shadow use.

    Evaluation does not write the repository. Its receipt is published only when
    the Agent commits this feedback, answer and resulting action under the same CAS.
    """

    def __init__(self, service, repository, *, policy, profile, rubric=None):
        self.service = service
        self.repository = repository
        self.policy = policy
        self.profile = profile
        self.rubric = rubric or load_rubric_pack()

    async def evaluate(self, request):
        context, inputs, records = await self._load(request)
        cached = self._cached(request, records)
        if cached is not None:
            return cached
        scored = await self._score(context, inputs, records)
        result = scored.evaluation
        feedback = EvaluationFeedback(
            request_id=request.request_id,
            question_id=request.question.question_id,
            answer_relevance=float(result.analysis.status == "substantive"),
            evidence_strength=float(bool(result.evidence_items)),
            analysis=result.analysis,
            evidence_ids=[e.evidence_id for e in result.evidence_items],
        )
        return self._envelope(context, inputs, feedback, scored, mode="formal")

    async def _load(self, request):
        context = await self.repository.get_interview_context(request.interview_id)
        records = await self.repository.get_evaluation_records(request.interview_id)
        # A concurrent commit between these reads is rejected, never silently rebased.
        if any(r.base_state_version >= context.state.state_version for r in records):
            raise StateConflictError("Evaluation context changed while loading the ledger")
        cached = self._cached(request, records)
        if cached is not None:
            return context, cached.evaluation_record.input, records
        if request.request_id in context.processed_feedback_ids:
            raise StateConflictError("Legacy feedback is already committed; reuse its saved action")
        question = await self.repository.get_question(request.question.question_id)
        if (
            question != request.question
            or context.state.current_question_id != question.question_id
        ):
            raise InvalidAgentState("Evaluation requires the current immutable question")
        inputs = input_for_request(request, context, records)
        return context, inputs, records

    @staticmethod
    def _cached(request, records):
        record = next((r for r in records if r.input.request_id == request.request_id), None)
        if record is None:
            return None
        if (
            record.input.interview_id != request.interview_id
            or record.input.answer != AnswerSnapshot.from_answer(request.answer)
            or record.input.question != QuestionSnapshot.from_question(request.question)
        ):
            raise InvalidAgentState("Evaluation request ID already belongs to another input")
        return EvaluatedFeedback(**record.feedback.model_dump(), evaluation_record=record)

    async def _score(self, context, inputs, records):
        history, previous = scoring_history(context.interview_id, records)
        profile = self.profile(context.job_profile) if callable(self.profile) else self.profile
        return await self.service.evaluate_scored(
            inputs,
            rubric=self.rubric,
            policy=self.policy,
            profile=profile,
            history=history,
            evaluation_failure_codes=failure_codes(
                context.processed_feedback_ids,
                records,
                unobserved_feedback_ids=context.unobserved_feedback_ids,
            ),
            supersedes_snapshot_id=previous,
            reevaluation_reason="new_answer" if previous else None,
        )

    @staticmethod
    def _envelope(context, inputs, feedback, scored, *, mode):
        record = EvaluationRecord(
            mode=mode,
            base_state_version=context.state.state_version,
            input=inputs,
            feedback=feedback,
            scored=scored,
        )
        return EvaluatedFeedback(**feedback.model_dump(), evaluation_record=record)


class ShadowEvaluationAdapter:
    """Run both engines concurrently; expose only legacy feedback and projection."""

    def __init__(self, legacy, formal):
        self.legacy = legacy
        self.formal = formal

    async def evaluate(self, request):
        context, inputs, records = await self.formal._load(request)
        cached = self.formal._cached(request, records)
        if cached is not None:
            return cached

        async def shadow():
            try:
                return await self.formal._score(context, inputs, records)
            except Exception:
                # Shadow bugs cannot discard the legacy answer. Cancellation still
                # propagates; provider text is never added to the persistent record.
                emit_trace("evaluation.shadow_failed", reason_code="shadow_internal_error")
                return ScoredEvaluation(
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

        tasks = [asyncio.create_task(self.legacy.evaluate(request)), asyncio.create_task(shadow())]
        try:
            feedback, scored = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return self.formal._envelope(context, inputs, feedback, scored, mode="shadow")


def shadow_profile(job):
    return ScoringProfile(
        profile_id=f"shadow-uncalibrated:{job.job_id}",
        competencies=tuple(
            dict(competency=c, weight=float(job.competency_importance.get(c, 0)), mandatory=False)
            for c in Competency
        ),
    )


def build_evaluation_adapter(llm, repository, legacy, *, mode=None):
    mode = mode if mode is not None else os.getenv("EVALUATION_MODE", "shadow")
    decision = RealtimeDecisionAdapter(llm, repository)
    assessment = BackgroundAssessmentAdapter(llm)
    if mode == "legacy":
        return BackgroundShadowEvaluationAdapter(
            decision, None, assessment=assessment, repository=repository
        )
    if mode != "shadow":
        raise ValueError("EVALUATION_MODE must be shadow or legacy")
    policy = AggregationPolicy.model_validate_json(
        Path(__file__).parents[2].joinpath("evaluation/config/shadow-v1.json").read_text()
    )
    formal = RubricEvaluationAdapter(
        EvaluationService(llm),
        repository,
        policy=policy,
        profile=shadow_profile,
    )
    return BackgroundShadowEvaluationAdapter(decision, formal, assessment=assessment)
