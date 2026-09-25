"""One semantic review boundary for candidate-facing ReAct questions."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    @model_validator(mode="after")
    def require_overload_basis(self):
        if any(issue.code == "OVERLOADED_QUESTION" for issue in self.issues):
            if not self.answer_requests or any(not item.strip() for item in self.answer_requests):
                raise ValueError("OVERLOADED_QUESTION requires nonempty answer_requests")
        return self

    def consistent_verdict(self):
        """A single required answer cannot support the overloaded-question code.

        The model still identifies semantic requests; this checks its verdict against
        its own structured basis, without parsing advice for English keywords.
        """
        count = len({" ".join(item.casefold().split()) for item in self.answer_requests or []})
        issues = [
            issue for issue in self.issues if issue.code != "OVERLOADED_QUESTION" or count >= 2
        ]
        return self.model_copy(update={"issues": issues})


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
    analysis = latest.feedback.analysis
    return {
        "scope": "Only when continuing the active thread; ignore on topic/project switches",
        "answer_scope": analysis.answer_scope,
        "status": analysis.status,
        "latest_answer": latest.answer.text[:text_limit] if latest.answer else None,
        "missing_information": [item[:300] for item in analysis.missing_information[:4]],
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
        return {
            "candidate_question": question.text,
            "information_goal": question.information_goal,
            "topic": question.topic,
            "scope_rule": "Both the information goal and question must address this selected topic",
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
                {"text": q.text, "information_goal": q.information_goal}
                for q in list(previous.values())[-self._settings.question_agent.history_retention :]
            ],
            "current_thread": [
                {
                    "question": entry.question.text,
                    "information_goal": entry.question.information_goal,
                    "answer": entry.answer.text[:limit] if entry.answer else None,
                    "thread_complete": entry.feedback.analysis.thread_complete,
                }
                for entry in thread[-self._settings.question_agent.history_tool_limit :]
            ],
            "answer_scope": thread[-1].feedback.analysis.answer_scope if thread else None,
        }

    async def review(self, question, interview, *, previous_questions=(), step=None, deadline=None):
        review = await run_model_call(
            lambda: self._llm.generate_structured(
                prompt_name=self.prompt_name,
                payload=self.payload(question, interview, previous_questions),
                response_model=QuestionQualityReview,
            ),
            operation="question_quality",
            question_id=question.question_id,
            step=step,
            timeout_seconds=self._settings.question_agent.quality_timeout_seconds,
            turn_deadline=deadline,
        )
        raw_review = QuestionQualityReview.model_validate(review.model_dump())
        review = raw_review.consistent_verdict()
        emit_trace(
            "question.quality",
            question_id=question.question_id,
            status="REVISE" if review.issues else "PASS",
            issues=[issue.code for issue in review.issues],
            draft=question.text if review.issues else None,
            guidance="; ".join(issue.instruction for issue in review.issues)[:700],
            overload_discarded=len(raw_review.issues) != len(review.issues),
            answer_requests=[item[:200] for item in review.answer_requests or []],
        )
        return review
