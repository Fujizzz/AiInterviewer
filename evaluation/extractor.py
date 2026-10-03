"""Extract atomic current-answer claims without assigning levels or competency scores."""

from typing import Literal

from pydantic import Field

from app.providers.llm import StructuredLLM
from evaluation.analyzer import is_bare_label, non_answer_status
from evaluation.contracts import EvaluationModel, EvidenceItem, QuoteSpan, Text
from evaluation.ids import evidence_id
from evaluation.inputs import EvaluationInput
from evaluation.model_calls import EvaluationModelClient, EvaluationStageError, load_prompt
from shared.contracts import Competency

EXTRACTION_VERSION = "extractor-1.0.0"


class EvidenceDraft(EvaluationModel):
    quote_spans: tuple[QuoteSpan, ...] = Field(min_length=1)
    normalized_claim: Text
    evidence_kind: Literal[
        "personal_action", "technical_explanation", "decision", "outcome", "reflection"
    ]
    ownership_scope: Literal["personal", "shared", "team_only", "unclear"]
    factuality: Literal["reported_experience", "hypothetical", "opinion", "unclear"]
    specificity: Literal["concrete", "partial", "vague"]


class EvidenceExtraction(EvaluationModel):
    evidence: tuple[EvidenceDraft, ...]


class EvidenceExtractor:
    def __init__(self, llm: StructuredLLM, *, timeout_seconds: float = 30) -> None:
        self._client = EvaluationModelClient(llm, timeout_seconds=timeout_seconds)
        self._prompt = load_prompt("evidence_extractor_v1")

    async def extract(self, context: EvaluationInput) -> tuple[EvidenceItem, ...]:
        if non_answer_status(context.answer.text) or is_bare_label(context.answer.text):
            return ()
        payload = context.extraction_payload()
        payload["competency_taxonomy"] = [item.value for item in Competency]
        output = await self._client.call(
            stage="extractor", prompt=self._prompt, payload=payload, schema=EvidenceExtraction
        )
        items = []
        seen = set()
        try:
            for draft in output.evidence:
                quoted_text = " ".join(span.quote for span in draft.quote_spans)
                if non_answer_status(quoted_text) or is_bare_label(quoted_text):
                    raise ValueError("a non-answer or bare label cannot support a claim")
                item = EvidenceItem(
                    **draft.model_dump(),
                    evidence_id=evidence_id(
                        answer_id=context.answer.answer_id,
                        quote_spans=draft.quote_spans,
                        normalized_claim=draft.normalized_claim,
                        evidence_kind=draft.evidence_kind,
                    ),
                    answer_id=context.answer.answer_id,
                    question_id=context.question.question_id,
                    thread_id=context.question.thread_id,
                    project_id=context.question.project_id,
                    extraction_version=EXTRACTION_VERSION,
                )
                # Reuse phase-one hard grounding checks now: never repair invented quotes.
                item.validate_answer(context.answer.as_candidate_answer())
                if item.evidence_id in seen:
                    raise ValueError("duplicate extraction item")
                seen.add(item.evidence_id)
                items.append(item)
        except ValueError as error:
            raise EvaluationStageError("extractor", "invalid_evidence") from error
        return tuple(items)
