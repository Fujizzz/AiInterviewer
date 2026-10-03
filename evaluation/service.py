"""Internal evaluation stages; live port integration and persistence are phase five."""

from agents.tracing import emit_trace
from app.providers.llm import StructuredLLM
from evaluation.aggregation import AggregationInput, ScoredEvaluation
from evaluation.aggregator import aggregate_scores
from evaluation.analyzer import NON_ANSWER_STATUSES, ConversationAnalyzer
from evaluation.contracts import EvaluationResult
from evaluation.extractor import EvidenceExtractor
from evaluation.inputs import EvaluationInput
from evaluation.judge import RubricJudge
from evaluation.model_calls import EvaluationStageError
from evaluation.policy import AggregationPolicy, ScoringProfile
from evaluation.resolution import ResolutionHistory, ResolvedEvaluation
from evaluation.resolver import EvidenceResolver
from evaluation.rubric import RubricPack
from shared.contracts import AnswerAnalysis


class EvaluationService:
    def __init__(
        self,
        llm: StructuredLLM,
        *,
        extractor_llm: StructuredLLM | None = None,
        resolver_llm: StructuredLLM | None = None,
        judge_llm: StructuredLLM | None = None,
        analyzer_timeout_seconds: float = 30,
        extractor_timeout_seconds: float = 30,
        resolver_timeout_seconds: float = 30,
        judge_timeout_seconds: float = 30,
    ) -> None:
        self._analyzer = ConversationAnalyzer(llm, timeout_seconds=analyzer_timeout_seconds)
        self._extractor = EvidenceExtractor(
            extractor_llm if extractor_llm is not None else llm,
            timeout_seconds=extractor_timeout_seconds,
        )
        self._resolver = EvidenceResolver(
            resolver_llm if resolver_llm is not None else llm,
            timeout_seconds=resolver_timeout_seconds,
        )
        self._judge = RubricJudge(
            judge_llm if judge_llm is not None else llm,
            timeout_seconds=judge_timeout_seconds,
        )

    async def evaluate_scored(
        self,
        context: EvaluationInput,
        *,
        rubric: RubricPack,
        policy: AggregationPolicy,
        profile: ScoringProfile,
        history: ResolutionHistory | None = None,
        evaluation_failure_codes: tuple[str, ...] = (),
        supersedes_snapshot_id: str | None = None,
        reevaluation_reason: str | None = None,
    ) -> ScoredEvaluation:
        """Rejudge the complete resolved history and return a replayable score record.

        Callers explicitly supply publication configuration; it is never sent to
        the model. Prior snapshots remain immutable. A stage failure publishes no
        new evidence, resolution, assessment, score or topic completion.
        """
        resolved = await self.evaluate_resolved(context, history=history)
        if resolved.evaluation.status == "failed":
            return ScoredEvaluation(evaluation=resolved.evaluation)
        try:
            judgement = await self._judge.judge(resolved.resolution, rubric=rubric)
            try:
                aggregation = aggregate_scores(
                    AggregationInput(
                        history=resolved.resolution.history,
                        rubric=rubric,
                        judgement=judgement,
                        policy=policy,
                        profile=profile,
                        evaluation_failure_codes=evaluation_failure_codes,
                        supersedes_snapshot_id=supersedes_snapshot_id,
                        reevaluation_reason=reevaluation_reason,
                    )
                )
            except ValueError as error:
                raise EvaluationStageError("aggregator", "invalid_input") from error
            result = resolved.evaluation
            analysis = result.analysis.model_copy(deep=True)
            if evaluation_failure_codes:
                analysis.thread_complete = False
            result = EvaluationResult.model_validate(
                {
                    **result.model_dump(),
                    "analysis": analysis,
                    "assessments": aggregation.gated_assessments,
                    "score_snapshot": aggregation.snapshot,
                }
            )
            return ScoredEvaluation(
                evaluation=result,
                resolution=resolved.resolution,
                aggregation=aggregation,
            )
        except EvaluationStageError as error:
            return ScoredEvaluation(evaluation=self._failure(context, error))

    async def evaluate_resolved(
        self, context: EvaluationInput, *, history: ResolutionHistory | None = None
    ) -> ResolvedEvaluation:
        """Phase-three entry point; keep evaluate() as the extraction-only API.

        Pass the complete pre-answer history, including original candidate source
        snapshots. Failed resolution discards this call's analysis/evidence too.
        """
        result = await self.evaluate(context)
        if result.status == "failed":
            return ResolvedEvaluation(evaluation=result)
        try:
            resolution = await self._resolver.resolve(
                context, result.evidence_items, history=history
            )
            current_ids = {item.evidence_id for item in result.evidence_items}
            current_states = [s for s in resolution.states if s.evidence_id in current_ids]
            analysis = result.analysis.model_copy(deep=True)
            if any(s.status in {"disputed", "insufficient"} for s in current_states) or not any(
                s.status == "eligible" for s in current_states
            ):
                analysis.thread_complete = False
            result = EvaluationResult.model_validate(
                {
                    **result.model_dump(),
                    "analysis": analysis,
                    "evidence_items": tuple(
                        item
                        for item in resolution.evidence_items
                        if item.evidence_id in current_ids
                    ),
                }
            )
            return ResolvedEvaluation(evaluation=result, resolution=resolution)
        except EvaluationStageError as error:
            return ResolvedEvaluation(evaluation=self._failure(context, error))

    async def evaluate(self, context: EvaluationInput) -> EvaluationResult:
        identity = dict(
            request_id=context.request_id,
            interview_id=context.interview_id,
            question_id=context.question.question_id,
            answer_id=context.answer.answer_id,
        )
        try:
            analysis = await self._analyzer.analyze(context)
            evidence = ()
            if analysis.status not in NON_ANSWER_STATUSES and analysis.answer_scope == "concrete":
                evidence = await self._extractor.extract(context)
            if not evidence:
                analysis.thread_complete = False
            return EvaluationResult(
                **identity, status="completed", analysis=analysis, evidence_items=evidence
            )
        except EvaluationStageError as error:
            return self._failure(context, error)

    @staticmethod
    def _failure(context: EvaluationInput, error: EvaluationStageError) -> EvaluationResult:
        emit_trace(
            "evaluation.fallback",
            question_id=context.question.question_id,
            answer_id=context.answer.answer_id,
            stage=error.stage,
            reason_code=f"{error.stage}_{error.reason}",
        )
        # Callers retain the original answer for atomic feedback submission.
        # asyncio cancellation deliberately propagates through every stage.
        return EvaluationResult(
            request_id=context.request_id,
            interview_id=context.interview_id,
            question_id=context.question.question_id,
            answer_id=context.answer.answer_id,
            status="failed",
            reason_codes=(f"{error.stage}_{error.reason}",),
            analysis=AnswerAnalysis(
                status="partial",
                summary="Automated evaluation was unavailable; this answer is unassessed.",
                uncertainties=[
                    "Evaluation failed; do not infer that the candidate lacks knowledge."
                ],
            ),
        )
