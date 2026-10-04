"""Conversation control analysis; no rubric, evidence strength or scores."""

import re
from typing import Literal

from pydantic import Field

from app.providers.llm import StructuredLLM
from evaluation.contracts import EvaluationModel, Text
from evaluation.inputs import EvaluationInput
from evaluation.model_calls import EvaluationModelClient, load_prompt
from shared.contracts import AnswerAnalysis

ANALYZER_VERSION = "analyzer-1.0.0"
NON_ANSWER_STATUSES = {"non_answer", "explicit_unknown", "refusal"}


class ConversationContradiction(EvaluationModel):
    earlier_answer_id: Text
    earlier_quote: Text
    current_quote: Text
    explanation: Text


class ConversationAnalysis(EvaluationModel):
    status: Literal["substantive", "partial", "non_answer", "explicit_unknown", "refusal"]
    answer_scope: Literal["label_only", "concrete", "none"]
    summary: Text
    new_information: bool = Field(strict=True)
    missing_information: tuple[Text, ...]
    uncertainties: tuple[Text, ...]
    contradiction_evidence: tuple[ConversationContradiction, ...]
    thread_complete: bool = Field(strict=True)


def non_answer_status(text: str) -> str | None:
    """Conservative literal gates; other semantic non-answers remain the analyzer's task."""
    normalized = re.sub(r"[^\w]+", "", text.casefold())
    if normalized in {"", "yes", "ok", "okay", "嗯", "是", "是的", "好的", "好"}:
        return "non_answer"
    if normalized in {
        "idontknow",
        "idonotknow",
        "dontknow",
        "notsure",
        "不知道",
        "不清楚",
        "我不知道",
        "不会",
    }:
        return "explicit_unknown"
    if normalized in {"iprefernottoanswer", "ideclinetoanswer", "拒绝回答", "不想回答"}:
        return "refusal"
    return None


def is_bare_label(text: str) -> bool:
    return re.fullmatch(r"[A-Za-z][A-Za-z0-9_+.#/-]{0,31}", text.strip()) is not None


def safe_analysis(result: ConversationAnalysis, context: EvaluationInput) -> AnswerAnalysis:
    analysis = AnswerAnalysis(**result.model_dump(mode="json"))
    literal_status = non_answer_status(context.answer.text)
    if literal_status:
        analysis.status = literal_status
        analysis.new_information = False
        analysis.summary = "The answer does not provide a concrete detail."
        analysis.missing_information = ["Describe one concrete action you personally took"]
    if is_bare_label(context.answer.text) and analysis.status not in NON_ANSWER_STATUSES:
        analysis.answer_scope = "label_only"
    if analysis.status in NON_ANSWER_STATUSES:
        analysis.answer_scope = "none"
        analysis.new_information = False
        analysis.uncertainties = []
    if any(
        turn.answer.text.strip().casefold() == context.answer.text.strip().casefold()
        for turn in context.history
    ):
        analysis.new_information = False

    # Preserve the legacy safety check for conversation feedback. Evidence relations
    # and semantic contradiction resolution still belong to phase three.
    prior = {turn.answer.answer_id: turn.answer.text for turn in context.history}
    grounded = [
        proof
        for proof in analysis.contradiction_evidence
        if proof.earlier_answer_id in prior
        and proof.earlier_quote in prior[proof.earlier_answer_id]
        and proof.current_quote in context.answer.text
        and proof.earlier_quote.strip().casefold() != proof.current_quote.strip().casefold()
        and analysis.answer_scope == "concrete"
    ]
    if len(grounded) != len(analysis.contradiction_evidence) and analysis.answer_scope != "none":
        analysis.uncertainties.append("A possible inconsistency needs clarification.")
    analysis.contradiction_evidence = grounded
    analysis.contradictions = [proof.explanation for proof in grounded]
    if (
        context.topic is None
        or analysis.status != "substantive"
        or analysis.answer_scope != "concrete"
        or analysis.missing_information
        or analysis.contradictions
    ):
        analysis.thread_complete = False
    return analysis


class ConversationAnalyzer:
    def __init__(self, llm: StructuredLLM, *, timeout_seconds: float = 30) -> None:
        self._client = EvaluationModelClient(llm, timeout_seconds=timeout_seconds)
        self._prompt = load_prompt("conversation_analyzer_v1")

    async def analyze(self, context: EvaluationInput) -> AnswerAnalysis:
        output = await self._client.call(
            stage="analyzer",
            prompt=self._prompt,
            payload=context.conversation_payload(),
            schema=ConversationAnalysis,
        )
        return safe_analysis(output, context)
