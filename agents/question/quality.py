"""One semantic review boundary for candidate-facing ReAct questions."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.model_calls import run_model_call
from agents.tracing import emit_trace

# Match interview-control language, not technical resource/latency budgets.
_INTERNAL_RULES = re.compile(
    r"\b(?:topic|interview|follow[- ]?up|question|project\s+question)\s+"
    r"(?:question\s+)?(?:limit|budget|quota)\b"
    r"|\bexhausted\s+(?:the\s+)?questions\b"
    r"|\b(?:max_questions|max_follow_up|followups_remaining|followup_block)\b"
    r"|(?:话题|项目|追问|面试|提问).{0,8}(?:次数上限|题数上限|问题配额)"
    r"|(?:提问|追问|题数|问题数量).{0,6}(?:已达上限|用完|耗尽)",
    re.IGNORECASE,
)


def leaks_interview_rules(text: str) -> bool:
    return bool(_INTERNAL_RULES.search(text))


class AnswerRequest(BaseModel):
    """The semantic reviewer identifies outputs, not the number of topic nouns."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request: str = Field(min_length=1, max_length=400)
    quote: str = Field(min_length=1, max_length=1000)
    relation: Literal["independent_output", "input_to_same_output"]


class ReviewEvidenceError(ValueError):
    """An unsupported reviewer verdict must be corrected, never treated as PASS."""

    def __init__(self, errors, *, conflict=False, disputed_review=None):
        self.errors = list(dict.fromkeys(errors))
        self.code = "REVIEW_CONFLICT" if conflict else "REVIEW_INVALID"
        self.disputed_review = disputed_review
        super().__init__(self.code)


class ComparedAnswerUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_quote: str = Field(min_length=1)
    requested_fact: str = Field(min_length=1, max_length=400)
    previous_answer_quote: str = ""
    missing_detail: str = ""
    new_detail_quote: str = ""


class RepeatCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    comparison_question_id: str
    verdict: Literal["repeat", "not_repeat", "uncertain"]
    relation: Literal[
        "already_answered",
        "same_unanswered_request",
        "narrower_unanswered_request",
        "different_request",
        "uncertain",
    ]
    current_request_quote: str
    previous_request_quote: str
    direct_answer_quote: str = ""
    reason: str = Field(min_length=1, max_length=500)
    answer_units: list[ComparedAnswerUnit] = Field(default_factory=list, max_length=6)


class RepeatAdjudication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[RepeatCheck] = Field(min_length=1, max_length=6)


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_id: str
    quote: str = Field(min_length=1)


class GroundingCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    issue_index: int = Field(ge=0)
    verdict: Literal["confirmed", "refuted", "uncertain"]
    relation: Literal[
        "suggested_answer",
        "unestablished_experience",
        "established_context",
        "neutral_request",
        "uncertain",
    ]
    request_quote: str = Field(min_length=1)
    premise_basis: Literal["new_assertion", "requested_detail", "established_fact"] | None = None
    asserted_fact_quote: str = Field(default="", max_length=1000)
    sources: list[SourceReference] = Field(default_factory=list, max_length=6)
    reason: str = Field(min_length=1, max_length=500)


class GroundingAdjudication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[GroundingCheck] = Field(min_length=1, max_length=6)


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: Literal[
        "SEMANTIC_REPEAT",
        "OVERLOADED_QUESTION",
        "INTERNAL_RULE_LEAK",
        "UNSUPPORTED_PREMISE",
        "TOPIC_MISMATCH",
        "ANSWER_HINT",
    ]
    instruction: str = Field(min_length=1, max_length=1000)
    question_quote: str = Field(default="", max_length=2000)
    repair_action: Literal[
        "remove_hint",
        "remove_unfounded_premise",
        "focus_primary_request",
        "restore_intent",
        "narrow_unanswered_request",
        "rephrase_within_intent",
    ] = "rephrase_within_intent"
    comparison_question_id: str | None = None
    comparison_question_quote: str | None = None
    comparison_answer_id: str | None = None
    comparison_answer_quote: str | None = None
    repeat_relation: (
        Literal[
            "already_answered",
            "same_unanswered_request",
            "narrower_unanswered_request",
        ]
        | None
    ) = None
    scope_relation: Literal["same_target", "subgoal", "outside_target"] | None = None
    actual_request: str | None = Field(default=None, max_length=500)

    @field_validator("instruction", mode="before")
    @classmethod
    def bound_display_note(cls, value):
        # An auxiliary explanation must not invalidate an otherwise actionable verdict.
        # Keep codes/IDs/schema types strict; only normalize this free-text note.
        return value.strip()[:1000] if isinstance(value, str) else value


class QuestionQualityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Required: a malformed/missing review must never implicitly pass.
    issues: list[QualityIssue] = Field(max_length=6)
    answer_requests: list[str] | None = Field(
        default=None,
        max_length=8,
        description=(
            "One entry per independent answer required by the draft; alternatives and examples "
            "for one answer stay in ONE entry. Required when reporting OVERLOADED_QUESTION."
        ),
    )
    answer_units: list[AnswerRequest] = Field(default_factory=list, max_length=8)

    def consistent_verdict(self):
        """Reject inconsistent evidence instead of silently deleting a blocking issue."""
        if any(issue.code == "OVERLOADED_QUESTION" for issue in self.issues):
            units = {
                (" ".join(unit.request.casefold().split()), unit.quote)
                for unit in self.answer_units
                if unit.relation == "independent_output"
            }
            if len({request for request, _ in units}) < 2 or len({quote for _, quote in units}) < 2:
                raise ReviewEvidenceError(["OVERLOAD_REQUIRES_DISTINCT_INDEPENDENT_OUTPUTS"])
        return self

    def validate_evidence(self, payload, *, for_adjudication=False):
        """Validate sources and verdict consistency; semantics still belong to the model."""
        self.consistent_verdict()
        text = payload["candidate_question"] or ""
        history = {item["question_id"]: item for item in payload["previous_questions"]}
        errors = []
        conflict = False
        allowed_actions = {
            "ANSWER_HINT": {"remove_hint"},
            "UNSUPPORTED_PREMISE": {"remove_unfounded_premise"},
            "TOPIC_MISMATCH": {"restore_intent"},
            "OVERLOADED_QUESTION": {"focus_primary_request"},
            "SEMANTIC_REPEAT": {"narrow_unanswered_request", "rephrase_within_intent"},
            "INTERNAL_RULE_LEAK": {"rephrase_within_intent"},
        }
        for issue in self.issues:
            if not issue.question_quote.strip() or issue.question_quote not in text:
                errors.append(f"{issue.code}:INVALID_DRAFT_QUOTE")
            if issue.repair_action not in allowed_actions[issue.code]:
                errors.append(f"{issue.code}:REPAIR_ACTION_CONFLICT")
                conflict = True
            if issue.code == "SEMANTIC_REPEAT":
                prior = history.get(issue.comparison_question_id)
                if (
                    prior is None
                    or not issue.comparison_question_quote
                    or issue.comparison_question_quote not in (prior["text"] or "")
                ):
                    errors.append("REPEAT_REQUIRES_PRIOR_QUESTION_REFERENCE")
                    continue
                if issue.repeat_relation == "already_answered":
                    if (
                        not issue.comparison_answer_id
                        or issue.comparison_answer_id != prior.get("answer_id")
                        or not issue.comparison_answer_quote
                        or issue.comparison_answer_quote not in (prior.get("answer") or "")
                    ):
                        errors.append("ANSWERED_REPEAT_REQUIRES_ANSWER_EVIDENCE")
                elif issue.repeat_relation != "same_unanswered_request" and not for_adjudication:
                    errors.append("NARROWER_OR_UNSPECIFIED_REQUEST_CANNOT_PROVE_REPEAT")
                    conflict = True
                if not issue.actual_request or not issue.actual_request.strip():
                    errors.append("REPEAT_REQUIRES_REQUEST_COMPARISON")
            if issue.code == "TOPIC_MISMATCH":
                if issue.scope_relation != "outside_target":
                    errors.append("LEGAL_SUBGOAL_CANNOT_PROVE_TOPIC_MISMATCH")
                    conflict = True
                if not issue.actual_request or not issue.actual_request.strip():
                    errors.append("MISMATCH_REQUIRES_ACTUAL_REQUEST")
        if any(issue.code == "OVERLOADED_QUESTION" for issue in self.issues):
            if any(unit.quote not in text for unit in self.answer_units):
                errors.append("ANSWER_UNIT_REQUIRES_DRAFT_QUOTE")
        if errors:
            raise ReviewEvidenceError(errors, conflict=conflict)
        return self


def followup_brief(interview, *, text_limit=4000):
    """A small generation hint scoped to the active thread; never a new policy."""
    active = interview.active_thread
    entries = [
        entry
        for entry in interview.question_history
        if active and entry.question.thread_id == active.thread_id
    ]
    if not entries:
        return None
    latest = entries[-1]
    if getattr(latest.feedback, "analysis_status", "valid") != "valid":
        return {
            "scope": "Only when continuing the active thread",
            "analysis_status": "unavailable",
            "latest_answer": latest.answer.text[:text_limit] if latest.answer else None,
            "missing_information": [],
            "focus": "The answer is unassessed; do not infer weak knowledge or no information.",
        }
    analysis = latest.feedback.analysis
    progress = interview.topic_progress.get(active.topic_key)
    objective_gaps = progress.missing_information if progress else []
    return {
        "scope": "Only when continuing the active thread; ignore on topic/project switches",
        "answer_scope": analysis.answer_scope,
        "status": analysis.status,
        "latest_answer": latest.answer.text[:text_limit] if latest.answer else None,
        "missing_information": [
            item[:300] for item in (objective_gaps or analysis.missing_information)[:4]
        ],
        "objective_id": progress.objective_id if progress else None,
        "next_need_id": progress.next_need_id if progress else None,
        "objective_coverage": progress.coverage_status if progress else None,
        "focus": (
            "Narrow an unresolved detail in the latest answer, even under the same information "
            "goal; do not repeat the same request at the same breadth. Ask openly without "
            "supplying possible technical answers. After an unspecified upgrade, "
            "ask for one concrete change. "
            "If the last question asked which component was handled, do not ask that again. "
            "Do not repeat a broad architecture question."
            if analysis.answer_scope in {"label_only", "none"}
            else "Ask for one new detail supported by the latest answer."
        ),
    }


class QuestionQualityGate:
    prompt_name = "question_quality_v1"

    def __init__(self, llm, settings):
        self._llm = llm
        self._settings = settings

    def payload(self, question, interview, previous_questions=()):
        limit = self._settings.question_agent.text_char_limit
        project = next(
            (
                p
                for p in interview.candidate_profile.projects
                if p.project_id == question.project_id
            ),
            None,
        )
        entries = [
            entry
            for entry in interview.question_history
            if entry.question.project_id == question.project_id
        ]
        continuing = question.dialogue_action in {"clarify", "probe"}
        thread = [
            entry
            for entry in entries
            if continuing and entry.question.thread_id == question.thread_id
        ]
        previous = {
            q.question_id: q for q in previous_questions if q.project_id == question.project_id
        }
        previous.update({entry.question.question_id: entry.question for entry in entries})
        by_question = {entry.question.question_id: entry for entry in entries}
        progress = interview.topic_progress.get(question.topic_key)

        def history_record(q):
            entry = by_question.get(q.question_id)
            valid = (
                entry is not None and getattr(entry.feedback, "analysis_status", "valid") == "valid"
            )
            return {
                "question_id": q.question_id,
                "text": q.text,
                "information_goal": q.information_goal,
                "thread_id": q.thread_id,
                "answer_id": entry.answer.answer_id if entry and entry.answer else None,
                "answer": entry.answer.text[:limit] if entry and entry.answer else None,
                "answer_truncated": bool(entry and entry.answer and len(entry.answer.text) > limit),
                "analysis_status": "valid" if valid else "unavailable",
                "answer_status": entry.feedback.analysis.status if valid else None,
                "missing_information": entry.feedback.analysis.missing_information if valid else [],
                "thread_complete": entry.feedback.analysis.thread_complete if valid else None,
            }

        sources = []
        if project:
            sources.extend(
                {
                    "source_id": f"resume:project:{project.project_id}:{name}",
                    "project_id": project.project_id,
                    "text": text[:limit],
                }
                for name, text in (("name", project.name), ("domain", project.domain))
                if text
            )
            sources.extend(
                {
                    "source_id": f"resume:claim:{c.claim_id}",
                    "project_id": project.project_id,
                    "text": c.text[:limit],
                }
                for c in project.claims[:20]
            )
            sources.extend(
                {"source_id": f"resume:{name}:{index}", "text": text[:limit]}
                for name, texts in (
                    ("description", [project.description or ""]),
                    ("technology", project.technologies[:20]),
                    ("metric", project.metrics[:20]),
                )
                for index, text in enumerate(texts)
                if text
            )
        sources.extend(
            {"source_id": f"answer:{entry.answer.answer_id}", "text": entry.answer.text[:limit]}
            for entry in entries
            if entry.answer
        )
        return {
            "grounding_sources": sources,
            "project_context": {
                "project_id": project.project_id,
                "name": project.name[:limit],
                "domain": project.domain,
                "claim_ids": [c.claim_id for c in project.claims[:20]],
            }
            if project
            else None,
            "candidate_question": question.text,
            "information_goal": question.information_goal,
            "topic": question.topic,
            "confirmed_intent": {
                "intent_id": getattr(question, "intent_id", ""),
                "objective_id": getattr(question, "objective_id", ""),
                "need_id": getattr(question, "need_id", ""),
                "target": question.information_goal,
                "answer_unit": getattr(question, "answer_unit", ""),
                "topic": question.topic,
            },
            "coverage": {
                name: getattr(progress, name, None)
                for name in (
                    "objective_id",
                    "coverage_status",
                    "missing_information",
                    "evidence_answer_ids",
                )
            }
            if progress
            else None,
            "scope_rule": (
                "Review the actual request against the confirmed intent. One legal subgoal of "
                "the topic is sufficient; never require all parts of a compound topic. "
                "A label alone does not prove that wording addresses its intent."
            ),
            "continuing_thread": continuing,
            "resume_claims": {
                "name": project.name[:limit],
                "description": (project.description or "")[:limit],
                "technologies": project.technologies[:20],
                "metrics": project.metrics[:20],
                "claims": [claim.text[:limit] for claim in project.claims[:20]],
            }
            if project
            else None,
            "previous_questions": [
                history_record(q)
                for q in list(previous.values())[-self._settings.question_agent.history_retention :]
            ],
            "current_thread": [
                {
                    "question_id": entry.question.question_id,
                    "answer_id": entry.answer.answer_id if entry.answer else None,
                    "question": entry.question.text,
                    "information_goal": entry.question.information_goal,
                    "answer": entry.answer.text[:limit] if entry.answer else None,
                    "analysis_status": getattr(entry.feedback, "analysis_status", "valid"),
                    "missing_information": entry.feedback.analysis.missing_information
                    if getattr(entry.feedback, "analysis_status", "valid") == "valid"
                    else [],
                    "thread_complete": entry.feedback.analysis.thread_complete
                    if getattr(entry.feedback, "analysis_status", "valid") == "valid"
                    else None,
                }
                for entry in thread[-self._settings.question_agent.history_tool_limit :]
            ],
            "answer_scope": thread[-1].feedback.analysis.answer_scope
            if thread and getattr(thread[-1].feedback, "analysis_status", "valid") == "valid"
            else None,
        }

    async def review(
        self,
        question,
        interview,
        *,
        previous_questions=(),
        step=None,
        deadline=None,
        review_feedback=None,
    ):
        payload = self.payload(question, interview, previous_questions)
        if review_feedback:
            payload["review_feedback"] = review_feedback
        disputed = review_feedback.get("disputed_review") if review_feedback else None
        disputed_kind = (
            review_feedback.get("adjudication_kind", "repeat") if review_feedback else "repeat"
        )
        checked_kinds = set(review_feedback.get("checked_kinds", [])) if review_feedback else set()
        if disputed:
            if disputed_kind == "grounding":
                review = await self._adjudicate_grounding(
                    payload, disputed, question, step, deadline
                )
            else:
                review = await self._adjudicate_repeat(payload, disputed, question, step, deadline)
            checked_kinds.add(disputed_kind)
        else:
            review = await run_model_call(
                lambda: self._llm.generate_structured(
                    prompt_name=self.prompt_name,
                    payload=payload,
                    response_model=QuestionQualityReview,
                ),
                operation="question_quality",
                question_id=question.question_id,
                step=step,
                timeout_seconds=self._settings.question_agent.quality_timeout_seconds,
                turn_deadline=deadline,
            )
        raw_review = QuestionQualityReview.model_validate(review.model_dump())
        try:
            review = raw_review.validate_evidence(payload)
            if "repeat" not in checked_kinds and any(
                issue.code == "SEMANTIC_REPEAT" for issue in review.issues
            ):
                raise ReviewEvidenceError(
                    ["REPEAT_REQUIRES_ANSWERABILITY_CHECK"],
                    conflict=True,
                    disputed_review=review.model_dump(mode="json"),
                )
            if "grounding" not in checked_kinds and any(
                issue.code in {"ANSWER_HINT", "UNSUPPORTED_PREMISE"} for issue in review.issues
            ):
                error = ReviewEvidenceError(
                    ["FINDING_REQUIRES_GROUNDING_CHECK"],
                    conflict=True,
                    disputed_review=review.model_dump(mode="json"),
                )
                error.adjudication_kind = "grounding"
                raise error
        except ReviewEvidenceError as error:
            error.checked_kinds = sorted(checked_kinds)
            if not disputed and any(i.code == "SEMANTIC_REPEAT" for i in raw_review.issues):
                try:
                    raw_review.validate_evidence(payload, for_adjudication=True)
                except ReviewEvidenceError:
                    pass  # Invalid provenance needs correction, never a semantic override.
                else:
                    error.disputed_review = raw_review.model_dump(mode="json")
            emit_trace(
                "question.quality",
                question_id=question.question_id,
                status=error.code,
                issues=[issue.code for issue in raw_review.issues],
                review_errors=error.errors,
                draft=question.text,
                evidence=raw_review.model_dump(mode="json"),
            )
            raise
        emit_trace(
            "question.quality",
            question_id=question.question_id,
            status="REVISE" if review.issues else "PASS",
            issues=[issue.code for issue in review.issues],
            draft=question.text if review.issues else None,
            guidance="; ".join(issue.instruction for issue in review.issues)[:700],
            overload_discarded=False,
            answer_requests=[item[:200] for item in review.answer_requests or []],
            evidence=review.model_dump(mode="json"),
        )
        return review

    async def _adjudicate_grounding(self, payload, disputed, question, step, deadline):
        original = QuestionQualityReview.model_validate(disputed).validate_evidence(payload)
        issues = {
            index: issue
            for index, issue in enumerate(original.issues)
            if issue.code in {"ANSWER_HINT", "UNSUPPORTED_PREMISE"}
        }
        response = await run_model_call(
            lambda: self._llm.generate_structured(
                prompt_name="question_issue_check_v1",
                payload={
                    "question": question.text,
                    "confirmed_intent": payload["confirmed_intent"],
                    "issues": [
                        {"issue_index": i, **issue.model_dump(mode="json")}
                        for i, issue in issues.items()
                    ],
                    "sources": payload["grounding_sources"],
                    "project_context": payload.get("project_context"),
                },
                response_model=GroundingAdjudication,
            ),
            operation="question_issue_check",
            question_id=question.question_id,
            step=step,
            timeout_seconds=self._settings.question_agent.quality_timeout_seconds,
            turn_deadline=deadline,
        )
        checked = GroundingAdjudication.model_validate(response.model_dump())
        checks = {c.issue_index: c for c in checked.checks}
        sources = {s["source_id"]: s["text"] for s in payload["grounding_sources"]}
        errors, cleared = [], set()
        if set(checks) != set(issues) or len(checks) != len(checked.checks):
            errors.append("GROUNDING_CHECK_ISSUES_MISMATCH")
        for index, check in checks.items():
            issue = issues.get(index)
            if not issue:
                continue
            if check.request_quote not in question.text:
                errors.append("GROUNDING_CHECK_INVALID_REQUEST_QUOTE")
            if any(
                s.source_id not in sources or s.quote not in sources[s.source_id]
                for s in check.sources
            ):
                errors.append("GROUNDING_CHECK_INVALID_SOURCE")
            if check.verdict == "uncertain":
                errors.append("GROUNDING_CHECK_UNRESOLVED")
            elif check.verdict == "confirmed":
                expected = (
                    "suggested_answer"
                    if issue.code == "ANSWER_HINT"
                    else "unestablished_experience"
                )
                if check.relation != expected:
                    errors.append("GROUNDING_CHECK_VERDICT_CONFLICT")
                if issue.code == "UNSUPPORTED_PREMISE":
                    # Missing requested detail is the purpose of an interview, not proof
                    # that its premise is fabricated. Require a separate asserted fact.
                    if check.premise_basis != "new_assertion":
                        errors.append("UNSUPPORTED_PREMISE_REQUIRES_NEW_ASSERTION")
                    assertion = check.asserted_fact_quote.strip()
                    if not assertion or assertion not in question.text:
                        errors.append("UNSUPPORTED_PREMISE_REQUIRES_ASSERTED_FACT_QUOTE")
                    elif re.search(
                        r"\b(?:how|what|why|which|whether|describe|explain)\b"
                        r"|如何|怎么|怎样|是否|请(?:描述|解释|说明)",
                        assertion,
                        re.IGNORECASE,
                    ):
                        errors.append("REQUEST_QUOTE_CANNOT_PROVE_UNSUPPORTED_PREMISE")
                    elif any(assertion.casefold() in text.casefold() for text in sources.values()):
                        errors.append("ESTABLISHED_FACT_CANNOT_PROVE_UNSUPPORTED_PREMISE")
            elif check.relation == "established_context":
                if not check.sources:
                    errors.append("ESTABLISHED_CONTEXT_REQUIRES_SOURCE")
                else:
                    cleared.add(index)
            elif check.relation == "neutral_request":
                cleared.add(index)
            else:
                errors.append("GROUNDING_CHECK_VERDICT_CONFLICT")
        emit_trace(
            "question.grounding_check",
            question_id=question.question_id,
            checks=checked.model_dump(mode="json"),
            errors=errors,
        )
        if errors:
            error = ReviewEvidenceError(errors, conflict=True, disputed_review=disputed)
            error.adjudication_kind = "grounding"
            raise error
        return original.model_copy(
            update={
                "issues": [i for index, i in enumerate(original.issues) if index not in cleared]
            }
        )

    async def _adjudicate_repeat(self, payload, disputed, question, step, deadline):
        original = QuestionQualityReview.model_validate(disputed).validate_evidence(
            payload, for_adjudication=True
        )
        repeats = [issue for issue in original.issues if issue.code == "SEMANTIC_REPEAT"]
        prior = {item["question_id"]: item for item in payload["previous_questions"]}
        response = await run_model_call(
            lambda: self._llm.generate_structured(
                prompt_name="question_repeat_check_v1",
                payload={
                    "current_question": question.text,
                    "current_target": question.information_goal,
                    "comparisons": [prior[issue.comparison_question_id] for issue in repeats],
                    "coverage": payload.get("coverage"),
                },
                response_model=RepeatAdjudication,
            ),
            operation="question_repeat_check",
            question_id=question.question_id,
            step=step,
            timeout_seconds=self._settings.question_agent.quality_timeout_seconds,
            turn_deadline=deadline,
        )
        checked = RepeatAdjudication.model_validate(response.model_dump())
        checks = {item.comparison_question_id: item for item in checked.checks}
        ids = {issue.comparison_question_id for issue in repeats}
        errors = []
        if set(checks) != ids or len(checks) != len(checked.checks):
            errors.append("REPEAT_CHECK_COMPARISONS_MISMATCH")
        retained = [issue for issue in original.issues if issue.code != "SEMANTIC_REPEAT"]
        for issue in repeats:
            check = checks.get(issue.comparison_question_id)
            if check is None:
                continue
            previous = prior[check.comparison_question_id]
            unit_errors = []
            for unit in check.answer_units:
                if unit.request_quote not in question.text:
                    unit_errors.append("INVALID_ANSWER_UNIT_REQUEST_QUOTE")
                if unit.previous_answer_quote and unit.previous_answer_quote not in (
                    previous.get("answer") or ""
                ):
                    unit_errors.append("INVALID_ANSWER_UNIT_ANSWER_QUOTE")
                if bool(unit.previous_answer_quote) == bool(unit.missing_detail):
                    unit_errors.append("ANSWER_UNIT_REQUIRES_COVERAGE_OR_GAP")
            errors.extend(unit_errors)
            # A narrative verdict cannot override a complete mapping to prior evidence.
            if (
                check.answer_units
                and not unit_errors
                and all(u.previous_answer_quote for u in check.answer_units)
            ):
                retained.append(
                    issue.model_copy(
                        update={
                            "repeat_relation": "already_answered",
                            "comparison_answer_id": previous.get("answer_id"),
                            "comparison_answer_quote": check.answer_units[0].previous_answer_quote,
                        }
                    )
                )
                continue
            if (
                not check.current_request_quote
                or check.current_request_quote not in question.text
                or not check.previous_request_quote
                or check.previous_request_quote not in previous["text"]
            ):
                errors.append("INVALID_REPEAT_CHECK_REFERENCE")
            elif check.verdict == "uncertain":
                errors.append("REPEAT_CHECK_UNRESOLVED")
            elif check.verdict == "repeat":
                if check.relation == "already_answered" and (
                    not check.direct_answer_quote
                    or check.direct_answer_quote not in (previous.get("answer") or "")
                ):
                    errors.append("REPEAT_CHECK_NEEDS_DIRECT_ANSWER")
                elif check.relation not in {"already_answered", "same_unanswered_request"}:
                    errors.append("REPEAT_CHECK_VERDICT_CONFLICT")
                else:
                    retained.append(
                        issue.model_copy(
                            update={
                                "repeat_relation": check.relation,
                                "comparison_answer_id": previous.get("answer_id"),
                                "comparison_answer_quote": check.direct_answer_quote or None,
                            }
                        )
                    )
            elif check.relation not in {"narrower_unanswered_request", "different_request"}:
                errors.append("REPEAT_CHECK_VERDICT_CONFLICT")
            elif not check.answer_units or not any(u.missing_detail for u in check.answer_units):
                errors.append("NON_REPEAT_REQUIRES_EXPLICIT_NEW_FACT")
            elif any(
                not unit.new_detail_quote.strip()
                or unit.new_detail_quote not in unit.request_quote
                or unit.new_detail_quote not in unit.requested_fact
                or unit.new_detail_quote not in unit.missing_detail
                or unit.new_detail_quote.casefold() in previous["text"].casefold()
                for unit in check.answer_units
                if unit.missing_detail
            ) or any(unit.previous_answer_quote for unit in check.answer_units):
                # A missing result cannot authorize asking the already answered setup
                # again. Require a draft that explicitly requests ONLY the new detail.
                retained.append(
                    issue.model_copy(
                        update={
                            "instruction": (
                                "Rewrite to ask only the unresolved detail: "
                                + "; ".join(
                                    u.missing_detail for u in check.answer_units if u.missing_detail
                                )
                                + ". The current draft does not isolate this new request; "
                                "do not ask the answered setup or metrics again."
                            )[:1000],
                            "repair_action": "narrow_unanswered_request",
                        }
                    )
                )
        emit_trace(
            "question.repeat_check",
            question_id=question.question_id,
            checks=checked.model_dump(mode="json"),
            errors=errors,
        )
        if errors:
            raise ReviewEvidenceError(errors, conflict=True, disputed_review=disputed)
        return original.model_copy(update={"issues": retained})
