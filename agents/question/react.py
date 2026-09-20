"""Bounded, provider-neutral information-gathering loop for question wording."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from agents.config import AgentSettings
from agents.domain.models import InterviewContext, InterviewHistoryEntry
from agents.model_calls import run_model_call, validation_issues
from agents.ports import LLMPort
from agents.question.dialogue import DialogueSelection, dialogue_view, resolve_selection
from agents.question.validator import QuestionValidator
from agents.tracing import emit_trace
from shared.contracts import PlannedQuestion

_REPAIR_INSTRUCTIONS = {
    "PROJECT_QUESTION_LIMIT": (
        "This project's total question budget is exhausted across ALL its topics. "
        "Select new_project and an available topic from a different project."
    ),
    "TOPIC_QUESTION_LIMIT": (
        "This topic's question budget is exhausted. Choose an unused topic with a different "
        "information goal, or another project, within the remaining project budgets."
    ),
    "UNKNOWN_OR_USED_TOPIC": (
        "Copy an available topic_key exactly from dialogue_state.projects[].topics[]. "
        "Do not invent a key or reuse one from latest_turn or a project with no available topics."
    ),
    "REPEATED_INFORMATION_GOAL": (
        "The previous goal has already been asked. Ask for a different concrete detail "
        "supported by the current answer; do not merely rephrase a goal in active_thread.goals."
    ),
    "MULTIPLE_PRIMARY_QUESTIONS": (
        "Write context as statements, then one focused question with a single question mark."
    ),
    "action:literal_error": (
        "Top-level action must be get_project, get_history, get_plan, or final. "
        "For a question use action=final; put clarify/probe/new_topic/new_project "
        "only inside selection.dialogue_action."
    ),
}


class QuestionAgentDecision(BaseModel):
    """One action, with no writable plan fields or free-form reasoning trace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["get_history", "get_plan", "get_project", "final"] = Field(
        description="Tool action or final; dialogue choices belong in selection.dialogue_action"
    )
    topic: str | None = Field(default=None, description="get_history filter only; null for final")
    limit: int | None = Field(default=None, ge=1, le=50, description="get_history limit only")
    text: str | None = Field(default=None, description="Complete interview question for final only")
    project_id: str | None = Field(default=None, description="get_project argument only")
    selection: DialogueSelection | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_final_envelope(cls, value):
        if isinstance(value, dict) and isinstance(value.get("selection"), dict):
            action = value.get("action")
            if (
                action in ("clarify", "probe", "new_topic", "new_project")
                and value["selection"].get("dialogue_action") == action
                and isinstance(value.get("text"), str)
                and value["text"].strip()
            ):
                # Equivalent, explicit final intent; do not infer missing dialogue fields.
                value = {**value, "action": "final"}
        return value

    @model_validator(mode="after")
    def validate_action(self) -> QuestionAgentDecision:
        if self.action == "final" and self.selection is not None:
            # Some JSON-mode providers repeat selection fields in the tool arguments.
            # Selection remains authoritative and still passes all server-side guards.
            if self.project_id is not None and self.project_id != self.selection.project_id:
                raise PydanticCustomError(
                    "conflicting_project_ids", "Use selection.project_id; omit top-level project_id"
                )
            self.topic = self.limit = self.project_id = None
        if self.action != "final" and self.selection is not None:
            raise ValueError("Only final accepts selection")
        if self.action != "get_project" and self.project_id is not None:
            raise ValueError("Only get_project accepts project_id")
        if self.action == "get_project":
            if not self.project_id or self.topic is not None or self.limit is not None:
                raise ValueError("get_project requires project_id and null topic/limit")
        if self.action == "final":
            if not self.text or self.topic is not None or self.limit is not None:
                raise ValueError("final requires text and null tool arguments")
        elif self.text is not None:
            raise ValueError("tool calls must have null text")
        elif self.action == "get_history" and self.limit is None:
            raise ValueError("get_history requires limit")
        elif self.action == "get_plan" and (self.topic is not None or self.limit is not None):
            raise ValueError("get_plan takes no arguments")
        return self


@dataclass
class QuestionAgentResult:
    question: PlannedQuestion | None = None
    stop_reason: str = "NOT_RUN"
    repaired: bool = False
    decision_summary: str = ""
    steps: list[dict[str, str | int]] = field(default_factory=list)


class ReactQuestionAgent:
    """All scratch state is turn-local; tools read the supplied context snapshot."""

    prompt_name = "question_react_v1"

    def __init__(self, llm: LLMPort, settings: AgentSettings) -> None:
        self._llm = llm
        self._settings = settings
        self._validator = QuestionValidator(settings)

    async def generate(
        self,
        question_plan: PlannedQuestion,
        context: str,
        *,
        interview: InterviewContext,
        previous_questions: Sequence[PlannedQuestion] = (),
        autonomous: bool = False,
    ) -> QuestionAgentResult:
        result = QuestionAgentResult()
        started = perf_counter()
        deadline = started + self._settings.question_agent.total_timeout_seconds
        emit_trace(
            "react.started",
            question_id=question_plan.question_id,
            interview_id=interview.interview_id,
        )
        try:
            async with asyncio.timeout(self._settings.question_agent.total_timeout_seconds):
                await self._run(
                    question_plan,
                    context,
                    interview,
                    previous_questions,
                    result,
                    autonomous,
                    deadline,
                )
        except TimeoutError:
            result.stop_reason = "TIMEOUT"
        except asyncio.CancelledError:
            result.stop_reason = "CANCELLED"
            raise
        except Exception as error:
            result.stop_reason = f"ERROR:{type(error).__name__}"
        finally:
            emit_trace(
                "react.finished",
                question_id=question_plan.question_id,
                stop_reason=result.stop_reason,
                steps=result.steps,
                duration_ms=round((perf_counter() - started) * 1000),
            )
        return result

    async def _run(
        self,
        plan: PlannedQuestion,
        context: str,
        interview: InterviewContext,
        previous_questions: Sequence[PlannedQuestion],
        result: QuestionAgentResult,
        autonomous: bool = False,
        deadline: float | None = None,
    ) -> None:
        limits = self._settings.question_agent
        observations: list[dict[str, Any]] = []
        repair_errors: list[str] = []
        seen_calls: set[tuple[str, str | None, int | None]] = set()
        tool_calls = 0
        repairs = 0
        final_only = False
        previous_texts = {
            self._normalized(question.text)
            for question in [
                *previous_questions,
                *(entry.question for entry in interview.question_history),
            ]
            if question.text
        }
        # Each iteration either spends a tool call, spends a repair, or terminates.
        max_steps = limits.max_tool_calls + self._settings.retries.llm_generation_retries + 1
        for _ in range(max_steps):
            payload = {
                "question_plan": plan.model_dump(mode="json"),
                "context": context,
                "state_summary": {
                    "stage": interview.state.stage.value,
                    "remaining_seconds": interview.state.remaining_seconds,
                    "question_index": interview.state.question_index,
                    "consecutive_probes": interview.state.consecutive_probes,
                },
                "latest_turn": (
                    self._history_entry(interview.question_history[-1])
                    if interview.question_history
                    else None
                ),
                "observations": list(observations),
                "tools_remaining": max(0, limits.max_tool_calls - tool_calls),
                "history_tool_limit": limits.history_tool_limit,
                "final_only": final_only or tool_calls >= limits.max_tool_calls,
                "repair_errors": list(repair_errors),
                "repair_instructions": [
                    _REPAIR_INSTRUCTIONS[error]
                    for error in repair_errors
                    if error in _REPAIR_INSTRUCTIONS
                ],
            }
            if autonomous:
                payload.pop("question_plan")
                payload.pop("context")
                payload["dialogue_state"] = dialogue_view(interview, self._settings)
            emit_trace(
                "react.input",
                question_id=plan.question_id,
                step=len(result.steps) + 1,
                prompt_name=self.prompt_name,
                payload=payload,
            )
            try:
                decision = await run_model_call(
                    lambda payload=payload: self._llm.generate_structured(
                        prompt_name=self.prompt_name,
                        payload=payload,
                        response_model=QuestionAgentDecision,
                    ),
                    operation="question",
                    question_id=plan.question_id,
                    step=len(result.steps) + 1,
                    timeout_seconds=self._settings.timeouts.llm_generation_seconds,
                    turn_deadline=deadline,
                )
                # Enforce the boundary even when an adapter returns an unchecked model.
                decision = QuestionAgentDecision.model_validate(decision.model_dump())
            except TimeoutError:
                raise
            except Exception as error:
                repair_errors = [f"INVALID_DECISION:{type(error).__name__}"]
                issues = (
                    validation_issues(error, QuestionAgentDecision)
                    if isinstance(error, ValidationError)
                    else getattr(error, "validation_issues", [])
                )
                repair_errors.extend(issues)
                result.steps.append({"action": "invalid", "status": repair_errors[0]})
                emit_trace(
                    "react.invalid_decision", question_id=plan.question_id, errors=repair_errors
                )
            else:
                emit_trace(
                    "react.decision",
                    question_id=plan.question_id,
                    decision=decision.model_dump(mode="json"),
                )
                if decision.action == "final":
                    selected_plan = plan
                    repair_errors = []
                    if autonomous:
                        try:
                            if decision.selection is None:
                                raise ValueError("FINAL_REQUIRES_DIALOGUE_SELECTION")
                            selected_plan = resolve_selection(
                                decision.selection, interview, self._settings, plan.question_id
                            )
                        except ValueError as error:
                            repair_errors = [str(error)]
                    candidate = selected_plan.model_copy(update={"text": decision.text})
                    validation = self._validator.validate(candidate, selected_plan)
                    repair_errors.extend(validation.errors)
                    if self._normalized(candidate.text) in previous_texts:
                        repair_errors.append("REPEATED_QUESTION")
                    emit_trace(
                        "react.validation",
                        question_id=plan.question_id,
                        text=candidate.text,
                        errors=repair_errors,
                    )
                    result.steps.append(
                        {
                            "action": "final",
                            "status": "INVALID" if repair_errors else "VALID",
                        }
                    )
                    if not repair_errors:
                        result.question = candidate
                        result.decision_summary = (
                            decision.selection.decision_summary if decision.selection else ""
                        )
                        result.repaired = repairs > 0
                        result.stop_reason = "FINAL"
                        return
                    final_only = True
                elif payload["final_only"]:
                    result.steps.append({"action": decision.action, "status": "TOOL_LIMIT"})
                    result.stop_reason = "TOOL_LIMIT"
                    return
                else:
                    tool_calls += 1
                    key = (
                        decision.action,
                        (decision.project_id or decision.topic or "").casefold(),
                        decision.limit,
                    )
                    if key in seen_calls:
                        observation = {"error": "REPEATED_TOOL_CALL"}
                    else:
                        seen_calls.add(key)
                        observation = self._execute_tool(decision, interview)
                    emit_trace(
                        "react.observation",
                        question_id=plan.question_id,
                        action=decision.action,
                        arguments={
                            "topic": decision.topic,
                            "limit": decision.limit,
                            "project_id": decision.project_id,
                        },
                        result=observation,
                    )
                    observations.append(
                        {
                            "action": decision.action,
                            "arguments": {
                                "topic": decision.topic,
                                "limit": decision.limit,
                                "project_id": decision.project_id,
                            },
                            "result": observation,
                        }
                    )
                    result.steps.append(
                        {
                            "action": decision.action,
                            "status": str(observation.get("error", "OK")),
                            "result_count": len(observation.get("entries", [])),
                        }
                    )
                    continue
            if repairs >= self._settings.retries.llm_generation_retries:
                result.stop_reason = "INVALID_OUTPUT"
                return
            repairs += 1
        result.stop_reason = "STEP_LIMIT"

    def _execute_tool(
        self, decision: QuestionAgentDecision, interview: InterviewContext
    ) -> dict[str, Any]:
        if decision.action == "get_project":
            project = next(
                (
                    p
                    for p in interview.candidate_profile.projects
                    if p.project_id == decision.project_id
                ),
                None,
            )
            if project is None:
                return {"error": "UNKNOWN_PROJECT"}
            limit = self._settings.question_agent.text_char_limit
            return {
                "project": {
                    "project_id": project.project_id,
                    "name": project.name,
                    "description": (project.description or "")[:limit],
                    "technologies": project.technologies[:20],
                    "metrics": project.metrics[:20],
                    "claims": [
                        {"claim_id": c.claim_id, "text": c.text[:limit]}
                        for c in project.claims[:20]
                    ],
                },
                "source": "parsed_resume",
            }
        if decision.action == "get_plan":
            return {"plan": interview.plan.model_dump(mode="json")}
        limit = decision.limit or 1
        if limit > self._settings.question_agent.history_tool_limit:
            return {"error": "HISTORY_LIMIT_EXCEEDED"}
        topic = (decision.topic or "").casefold()
        entries = [
            entry
            for entry in interview.question_history
            if topic in (entry.question.topic or "").casefold()
        ]
        return {
            "entries": [self._history_entry(entry) for entry in entries[-limit:]],
            "retained_entries": len(interview.question_history),
            "scope": "retained evaluated turns in this interview only",
        }

    def _history_entry(self, entry: InterviewHistoryEntry) -> dict[str, Any]:
        limit = self._settings.question_agent.text_char_limit
        question = entry.question.model_dump(mode="json")
        question["text"] = (entry.question.text or "")[:limit]
        answer = entry.answer.model_dump(mode="json") if entry.answer is not None else None
        if answer is not None:
            answer["text"] = entry.answer.text[:limit]
        return {
            "question": question,
            "answer": answer,
            "question_truncated": len(entry.question.text or "") > limit,
            "answer_truncated": entry.answer is not None and len(entry.answer.text) > limit,
            "analysis": entry.feedback.analysis.model_dump(mode="json"),
        }

    @staticmethod
    def _normalized(text: str | None) -> str:
        return " ".join((text or "").casefold().split()).rstrip("?？.!。！")
