"""Legacy EvaluationPort retained for compatibility and shadow comparisons."""

import asyncio
import re
from pathlib import Path

from pydantic import Field, TypeAdapter, ValidationError, field_validator

from agents.config import load_agent_settings
from agents.domain.errors import AgentError
from agents.model_calls import run_model_call, safe_error_details
from agents.planning.coverage import conflicts_for_coverage
from agents.tracing import emit_trace
from app.providers.llm import OutputModel, StructuredLLM
from shared.contracts import (
    AnswerAnalysis,
    DimensionEvidence,
    EvaluationFeedback,
    EvaluationRequest,
    ObjectiveCoverage,
)


class AnswerEvidence(OutputModel):
    """Validate conversation and assessment independently, without extra retries."""

    answer_relevance: float | None = Field(default=None, ge=0, le=1)
    evidence_strength: float | None = Field(default=None, ge=0, le=1)
    analysis: AnswerAnalysis | None = None
    dimensions: list[DimensionEvidence] | None = Field(
        default=None, description="One assessment per competency, with original segment IDs."
    )
    objective_coverage: list[ObjectiveCoverage] | None = None

    @field_validator("objective_coverage", mode="before")
    @classmethod
    def isolate_coverage(cls, value):
        try:
            return TypeAdapter(list[ObjectiveCoverage]).validate_python(value)
        except (ValidationError, TypeError):
            return None

    @field_validator("analysis", mode="before")
    @classmethod
    def isolate_conversation(cls, value):
        try:
            if isinstance(value, dict) and "status" not in value:
                return None
            return AnswerAnalysis.model_validate(value)
        except (ValidationError, TypeError):
            return None

    @field_validator("dimensions", mode="before")
    @classmethod
    def isolate_assessment(cls, value):
        try:
            return TypeAdapter(list[DimensionEvidence]).validate_python(value)
        except (ValidationError, TypeError):
            return None

    @field_validator("answer_relevance", "evidence_strength", mode="before")
    @classmethod
    def isolate_measurement(cls, value):
        if isinstance(value, (float, int)) and not isinstance(value, bool) and 0 <= value <= 1:
            return value
        return None


class InvalidEvaluationEvidence(ValueError):
    """Model evidence failed grounding checks; do not treat it as candidate evidence."""


class LLMEvaluationAdapter:
    response_schema = AnswerEvidence
    prompt_name = "evaluation_legacy_v1"
    deferred_assessment = False

    def _prompt(self):
        return (
            Path(__file__)
            .with_name("prompts")
            .joinpath(self.prompt_name + ".md")
            .read_text(encoding="utf-8")
        )

    def __init__(self, llm: StructuredLLM, repository) -> None:
        self._llm = llm
        self._repository = repository

    async def evaluate(self, request: EvaluationRequest) -> EvaluationFeedback:
        try:
            return await self._evaluate(request)
        except AgentError:
            # Authentication/quota and state/storage failures are not local analysis errors.
            raise
        except Exception as error:
            emit_trace(
                "evaluation.fallback",
                question_id=request.question.question_id,
                answer_id=request.answer.answer_id,
                **safe_error_details(error),
            )
            # A provider/validation failure says nothing about candidate competence.
            # Preserve the answer via normal feedback submission, but publish no scores,
            # completion claims or invented evidence. Cancellation still propagates.
            return EvaluationFeedback(
                request_id=request.request_id,
                question_id=request.question.question_id,
                answer_relevance=0,
                evidence_strength=0,
                analysis=_unavailable_analysis(),
                analysis_status="unavailable",
                assessment_status="unavailable",
                evaluation_issues=[type(error).__name__],
            )

    async def _evaluate(self, request: EvaluationRequest) -> EvaluationFeedback:
        context = await self._repository.get_interview_context(request.interview_id)
        history = [
            entry
            for entry in context.question_history
            if entry.question.project_id == request.question.project_id
            and (entry.question.thread_id or entry.question.question_id)
            == (request.question.thread_id or request.question.question_id)
        ][-10:]
        # Cross-thread history is only for explicit corrections, not old open questions.
        relation_history = [
            entry
            for entry in context.question_history
            if entry.question.project_id == request.question.project_id
            and entry.answer
            and (
                len(entry.answer.text.strip().split()) >= 3
                or (
                    re.search(r"[\u4e00-\u9fff]", entry.answer.text)
                    and len(entry.answer.text.strip()) >= 8
                )
            )
        ][-12:]
        segments = answer_segments(request.answer.answer_id, request.answer.text)
        objectives = agenda_objectives(context, request.question.project_id)
        result = await self._analyze(
            request, context, segments, objectives, history, relation_history
        )
        if self.deferred_assessment:
            result = AnswerEvidence(**result.model_dump(), dimensions=[], evidence_strength=0)
        issues = []
        analysis_status = "valid" if result.analysis is not None else "unavailable"
        if analysis_status == "unavailable":
            issues.append("INVALID_CONVERSATION_STRUCTURE")
        analysis = (
            result.analysis.model_copy(deep=True)
            if result.analysis is not None
            else _unavailable_analysis()
        )
        normalized = re.sub(r"[^\w]+", "", request.answer.text.casefold())
        acknowledgement = normalized in {"yes", "ok", "okay", "嗯", "是", "是的", "好的", "好"}
        if analysis_status == "valid" and acknowledgement:
            analysis.status = "non_answer"
            analysis.new_information = False
            analysis.summary = "The answer does not provide a concrete detail."
            analysis.missing_information = ["Describe one concrete action you personally took"]
        if any(
            e.answer and e.answer.text.strip().casefold() == request.answer.text.strip().casefold()
            for e in history
        ):
            analysis.new_information = False
        # A bare label is insufficient evidence even if it answers a narrow clarification.
        short_label = re.fullmatch(r"[A-Za-z][A-Za-z_-]{0,31}", request.answer.text.strip())
        if short_label and analysis.status not in {"non_answer", "explicit_unknown", "refusal"}:
            analysis.answer_scope = "label_only"
        if analysis.answer_scope == "label_only":
            analysis.thread_complete = False
        if analysis.status in {"non_answer", "explicit_unknown", "refusal"}:
            analysis.answer_scope = "none"
            analysis.thread_complete = False
        # Only evidence grounded in two actual candidate answers may be called a contradiction.
        confirmed, proofs = [], []
        prior_answers = {
            e.answer.answer_id: e.answer.text
            for e in context.question_history
            if e.answer and e.question.project_id == request.question.project_id
        }
        for proof in analysis.contradiction_evidence:
            earlier = prior_answers.get(proof.earlier_answer_id)
            if (
                earlier
                and proof.earlier_quote.strip()
                and proof.current_quote.strip()
                and proof.earlier_quote in earlier
                and proof.current_quote in request.answer.text
                and proof.earlier_quote.strip().casefold() != proof.current_quote.strip().casefold()
                and analysis.answer_scope not in {"label_only", "none"}
            ):
                confirmed.append(proof.explanation)
                proofs.append(proof)
        if (analysis.contradictions or analysis.contradiction_evidence) and not confirmed:
            analysis.uncertainties.append("The role of the mentioned approach needs clarification.")
        if analysis.answer_scope == "none":
            # A non-answer cannot revive a suspicion from an older answer.
            analysis.uncertainties = []
        analysis.contradictions = confirmed
        analysis.contradiction_evidence = proofs
        accepted_relations = []
        for index, relation in enumerate(analysis.answer_relations):
            earlier = prior_answers.get(relation.earlier_answer_id, "")
            if (
                relation.earlier_quote.strip()
                and relation.current_quote.strip()
                and relation.earlier_quote in earlier
                and relation.current_quote in request.answer.text
                and relation.earlier_quote != relation.current_quote
                and relation.earlier_answer_id != request.answer.answer_id
            ):
                accepted_relations.append(
                    relation.model_copy(
                        update={"relation_id": f"relation-{request.answer.answer_id}-{index}"}
                    )
                )
            else:
                issues.append("INVALID_ANSWER_RELATION")
        analysis.answer_relations = accepted_relations
        assessment_status = "valid"
        try:
            if result.dimensions is None or result.evidence_strength is None:
                raise InvalidEvaluationEvidence("INVALID_ASSESSMENT_STRUCTURE")
            dimensions = grounded_dimensions(result.dimensions, request.answer.text, segments)
        except InvalidEvaluationEvidence as error:
            dimensions = []
            assessment_status = "unavailable"
            issues.append(str(error))
        if (
            analysis.status
            in {
                "non_answer",
                "explicit_unknown",
                "refusal",
            }
            or analysis.answer_scope in {"label_only", "none"}
            or acknowledgement
        ):
            dimensions = []
        coverage, coverage_issues = grounded_objective_coverage(
            result.objective_coverage,
            objectives,
            segments,
            request.answer.answer_id,
            analysis,
            analysis_status,
        )
        issues.extend(coverage_issues)
        coverage_status = "valid" if coverage else "unavailable"
        if issues:
            emit_trace(
                "evaluation.components",
                question_id=request.question.question_id,
                analysis_status=analysis_status,
                assessment_status=assessment_status,
                objective_coverage_status=coverage_status,
                issues=issues,
            )
        return EvaluationFeedback(
            request_id=request.request_id,
            question_id=request.question.question_id,
            answer_relevance=result.answer_relevance or 0,
            evidence_strength=result.evidence_strength if dimensions else 0,
            analysis=analysis,
            dimensions=dimensions,
            analysis_status=analysis_status,
            assessment_status="pending" if self.deferred_assessment else assessment_status,
            evaluation_issues=issues,
            objective_coverage=coverage,
            objective_coverage_status=coverage_status,
            evidence_ids=[
                f"evidence-{request.answer.answer_id}-{d.competency.value}" for d in dimensions
            ],
        )

    async def _analyze(self, request, context, segments, objectives, history, relation_history):
        return await run_model_call(
            lambda: asyncio.to_thread(
                self._llm,
                (self._prompt()),
                {
                    "question": request.question.model_dump(mode="json"),
                    "answer": request.answer.text,
                    "answer_segments": segments,
                    "objectives": objectives,
                    "current_objective_id": next(
                        (
                            item["objective_id"]
                            for item in objectives
                            if item["topic_key"] == request.question.topic_key
                        ),
                        None,
                    ),
                    "relation_history": [
                        {"answer_id": e.answer.answer_id, "answer": e.answer.text}
                        for e in relation_history
                    ],
                    "thread": context.active_thread.model_dump(mode="json")
                    if context.active_thread
                    else None,
                    "thread_root": next(
                        (
                            e.question.model_dump(mode="json")
                            for e in context.question_history
                            if e.question.question_id == request.question.thread_id
                        ),
                        None,
                    ),
                    "history": [
                        {
                            "question": e.question.text,
                            "question_id": e.question.question_id,
                            "thread_id": e.question.thread_id,
                            "answer_id": e.answer.answer_id if e.answer else None,
                            "answer": e.answer.text if e.answer else None,
                        }
                        for e in history
                    ],
                },
                self.response_schema,
            ),
            operation="evaluation",
            question_id=request.question.question_id,
            timeout_seconds=load_agent_settings().timeouts.evaluation_seconds,
        )


def agenda_objectives(context, project_id):
    from agents.planning.completion import requirements_for_objective

    plan = getattr(context, "plan", None)
    progress_by_topic = getattr(context, "topic_progress", {})
    return [
        {
            "objective_id": getattr(progress_by_topic.get(item.topic_key), "objective_id", "")
            or item.topic_key,
            "topic_key": item.topic_key,
            "objective": item.objective,
            "completion_criteria": item.completion_criteria,
            "coverage_status": getattr(
                progress_by_topic.get(item.topic_key), "coverage_status", "unassessed"
            ),
            "missing_information": getattr(
                progress_by_topic.get(item.topic_key), "missing_information", []
            ),
            "accepted_evidence": getattr(
                progress_by_topic.get(item.topic_key), "coverage_evidence", []
            ),
            "completion_requirements": [
                requirement.model_dump(mode="json")
                for requirement in requirements_for_objective(
                    item, progress_by_topic.get(item.topic_key)
                )
            ],
        }
        for item in getattr(plan, "topics", [])
        if item.project_id == project_id
    ]


def grounded_objective_coverage(updates, objectives, segments, answer_id, analysis, status):
    from agents.planning.completion import project_completion
    from shared.contracts.planning import CompletionRequirement

    if updates is None:
        return [], ["OBJECTIVE_COVERAGE_UNAVAILABLE"] if objectives else []
    if status != "valid" or analysis.status in {"non_answer", "explicit_unknown", "refusal"}:
        return [], ["OBJECTIVE_COVERAGE_WITHOUT_VALID_ANSWER"] if updates else []
    if analysis.answer_scope in {"none", "label_only"}:
        return [], ["OBJECTIVE_COVERAGE_WITHOUT_CONCRETE_EVIDENCE"] if updates else []
    allowed = {item["objective_id"]: item for item in objectives}
    by_id = {segment["id"]: segment["text"] for segment in segments}
    counts = {}
    for update in updates:
        counts[update.objective_id] = counts.get(update.objective_id, 0) + 1
    accepted, issues = [], []
    for update in updates:
        if update.objective_id not in allowed or counts[update.objective_id] != 1:
            issues.append("INVALID_OBJECTIVE_ID")
        elif not update.supporting_segment_ids or any(
            key not in by_id for key in update.supporting_segment_ids
        ):
            issues.append("INVALID_OBJECTIVE_EVIDENCE_SEGMENT")
        elif update.coverage_status == "sufficient" and (
            update.missing_information
            or conflicts_for_coverage(
                analysis,
                [by_id[key] for key in update.supporting_segment_ids],
                allowed[update.objective_id]["accepted_evidence"],
            )
        ):
            issues.append("CONFLICTING_OBJECTIVE_COMPLETION")
        else:
            requirements = [
                CompletionRequirement.model_validate(item)
                for item in allowed[update.objective_id].get("completion_requirements", [])
            ]
            known = {item.criterion_id: item for item in requirements}
            criterion_counts = {}
            for observed in update.criterion_coverage:
                criterion_counts[observed.criterion_id] = (
                    criterion_counts.get(observed.criterion_id, 0) + 1
                )
            observations = []
            for observed in update.criterion_coverage:
                prior = known.get(observed.criterion_id)
                if prior is None or criterion_counts[observed.criterion_id] != 1:
                    issues.append("INVALID_COMPLETION_CRITERION_ID")
                    continue
                if any(key not in by_id for key in observed.supporting_segment_ids):
                    issues.append("INVALID_COMPLETION_CRITERION_SEGMENT")
                    continue
                quotes = list(dict.fromkeys(by_id[key] for key in observed.supporting_segment_ids))
                if observed.coverage_status == "sufficient" and (
                    not quotes
                    or observed.missing_information
                    or conflicts_for_coverage(analysis, quotes, prior.evidence)
                ):
                    issues.append("UNSUPPORTED_COMPLETION_CRITERION")
                    continue
                observations.append(
                    observed.model_copy(
                        update={"answer_id": answer_id, "supporting_quotes": quotes}
                    )
                )
            projected, _ = project_completion(
                update.model_copy(update={"criterion_coverage": observations}), requirements
            )
            accepted.append(
                projected.model_copy(
                    update={
                        "answer_id": answer_id,
                        "supporting_segment_ids": list(
                            dict.fromkeys(update.supporting_segment_ids)
                        ),
                        "supporting_quotes": list(
                            dict.fromkeys(by_id[key] for key in update.supporting_segment_ids)
                        ),
                    }
                )
            )
    return accepted, issues


def _unavailable_analysis() -> AnswerAnalysis:
    return AnswerAnalysis(
        summary="Automated conversation analysis was unavailable; this answer is unassessed.",
        uncertainties=["System failure; do not infer candidate ability or completeness."],
    )


def answer_segments(answer_id: str, text: str) -> list[dict]:
    """Stable original spans: references never synthesize a continuous quotation."""
    spans = []
    for match in re.finditer(r"[^。！？.!?\n]+(?:[。！？.!?]+|(?=\n)|$)", text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append(
                {
                    "id": f"{answer_id}:s{len(spans)}",
                    "start": start,
                    "end": end,
                    "text": text[start:end],
                }
            )
    return spans


def grounded_dimensions(dimensions, answer, segments):
    """One conservative score per competency; every independent quote stays separate."""
    by_segment = {segment["id"]: segment["text"] for segment in segments}
    merged = {}
    for dimension in dimensions:
        evidence = dimension.model_copy(deep=True)
        if evidence.source_segment_ids:
            if any(identifier not in by_segment for identifier in evidence.source_segment_ids):
                raise InvalidEvaluationEvidence("UNKNOWN_EVIDENCE_SEGMENT")
            quotes = [by_segment[identifier] for identifier in evidence.source_segment_ids]
        else:
            quotes = evidence.source_quotes or [evidence.quote]
        if any(not quote.strip() or quote not in answer for quote in quotes):
            raise InvalidEvaluationEvidence("UNGROUNDED_EVIDENCE_QUOTE")
        evidence.source_quotes = list(dict.fromkeys(quotes))
        evidence.quote = evidence.source_quotes[0]
        previous = merged.get(evidence.competency)
        if previous is None:
            merged[evidence.competency] = evidence
            continue
        previous.source_quotes = list(
            dict.fromkeys(previous.source_quotes + evidence.source_quotes)
        )
        previous.source_segment_ids = list(
            dict.fromkeys(previous.source_segment_ids + evidence.source_segment_ids)
        )
        previous.strength = min(previous.strength, evidence.strength)
        if previous.rubric_level is None or evidence.rubric_level is None:
            previous.rubric_level = None
        else:
            previous.rubric_level = min(previous.rubric_level, evidence.rubric_level)
        if evidence.observation == "weak":
            previous.observation = "weak"
    return list(merged.values())
