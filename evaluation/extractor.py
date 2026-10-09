"""Extract atomic current-answer claims without assigning levels or competency scores."""

from typing import Literal

from pydantic import Field

from app.providers.llm import StructuredLLM
from evaluation.analyzer import is_bare_label, non_answer_status
from evaluation.contracts import EvaluationModel, EvidenceItem, QuoteSpan, Text
from evaluation.ids import evidence_id
from evaluation.inputs import EvaluationInput, ThreadTurn
from evaluation.model_calls import EvaluationModelClient, EvaluationStageError, load_prompt
from evaluation.validator import validate_evidence
from shared.contracts import Competency

EXTRACTION_VERSION = "extractor-1.1.0"


class QuoteReference(EvaluationModel):
    segment_id: Text
    quote: Text


def answer_segments(context: EvaluationInput) -> list[dict]:
    """Lossless paragraphs, never arbitrary windows that cut a quoted sentence."""
    segments, start = [], 0
    for index, text in enumerate(context.answer.text.splitlines(keepends=True)):
        segments.append(
            {
                "segment_id": f"{context.answer.answer_id}:s{index}",
                "text": text,
                "char_start": start,
            }
        )
        start += len(text)
    return segments


def resolve_quotes(references, segments) -> tuple[QuoteSpan, ...]:
    spans = []
    for reference in references:
        segment = segments.get(reference.segment_id)
        if segment is None:
            raise ValueError("unknown current-answer segment")
        source = segment["text"]
        start = source.find(reference.quote)
        if start < 0 or source.find(reference.quote, start + 1) >= 0:
            raise ValueError("quote must have exactly one match in its source segment")
        start += segment["char_start"]
        spans.append(
            QuoteSpan(
                quote=reference.quote, char_start=start, char_end=start + len(reference.quote)
            )
        )
    return tuple(spans)


class EvidenceDraft(EvaluationModel):
    quote_spans: tuple[QuoteReference, ...] = Field(min_length=1)
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
        segments = answer_segments(context)
        payload["answer_segments"] = [
            {"segment_id": segment["segment_id"], "text": segment["text"]} for segment in segments
        ]
        payload["answer"].pop("text")  # One authoritative quote source, not two competing views.
        payload["competency_taxonomy"] = [item.value for item in Competency]
        output = await self._client.call(
            stage="extractor", prompt=self._prompt, payload=payload, schema=EvidenceExtraction
        )
        items = []
        seen = set()
        try:
            for draft in output.evidence:
                spans = resolve_quotes(draft.quote_spans, {s["segment_id"]: s for s in segments})
                item = EvidenceItem(
                    **draft.model_dump(exclude={"quote_spans"}),
                    quote_spans=spans,
                    evidence_id=evidence_id(
                        answer_id=context.answer.answer_id,
                        quote_spans=spans,
                        normalized_claim=draft.normalized_claim,
                        evidence_kind=draft.evidence_kind,
                    ),
                    answer_id=context.answer.answer_id,
                    question_id=context.question.question_id,
                    thread_id=context.question.thread_id,
                    project_id=context.question.project_id,
                    extraction_version=EXTRACTION_VERSION,
                )
                item = validate_evidence(
                    item,
                    ThreadTurn(question=context.question, answer=context.answer),
                    interview_id=context.interview_id,
                )
                if item.evidence_id in seen:
                    raise ValueError("duplicate extraction item")
                seen.add(item.evidence_id)
                items.append(item)
        except ValueError as error:
            raise EvaluationStageError("extractor", "invalid_evidence") from error
        return tuple(items)
