"""Phase-two orchestration; no live EvaluationPort, judging, scoring or persistence."""

from agents.tracing import emit_trace
from app.providers.llm import StructuredLLM
from evaluation.analyzer import NON_ANSWER_STATUSES, ConversationAnalyzer
from evaluation.contracts import EvaluationResult
from evaluation.extractor import EvidenceExtractor
from evaluation.inputs import EvaluationInput
from evaluation.model_calls import EvaluationStageError
from shared.contracts import AnswerAnalysis


class EvaluationService:
    def __init__(
        self,
        llm: StructuredLLM,
        *,
        extractor_llm: StructuredLLM | None = None,
        analyzer_timeout_seconds: float = 30,
        extractor_timeout_seconds: float = 30,
    ) -> None:
        self._analyzer = ConversationAnalyzer(llm, timeout_seconds=analyzer_timeout_seconds)
        self._extractor = EvidenceExtractor(
            extractor_llm if extractor_llm is not None else llm,
            timeout_seconds=extractor_timeout_seconds,
        )

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
            emit_trace(
                "evaluation.fallback",
                question_id=context.question.question_id,
                answer_id=context.answer.answer_id,
                stage=error.stage,
                reason_code=f"{error.stage}_{error.reason}",
            )
            # Discard even successful analysis if extraction fails. No side effects:
            # callers retain the original answer for normal atomic feedback submission.
            # asyncio cancellation deliberately propagates through both stages.
            return EvaluationResult(
                **identity,
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
