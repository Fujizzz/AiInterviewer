"""Deterministic LLMPort adapter; no provider SDK is used."""

from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class MockLLMAdapter:
    def __init__(
        self,
        *,
        text: str | None = None,
        structured_payload: dict[str, Any] | BaseModel | None = None,
        text_responses: Sequence[str | BaseException] = (),
        failure_mode: str | None = None,
    ) -> None:
        self._text = text
        self._structured_payload = structured_payload
        self._text_responses = list(text_responses)
        self._failure_mode = failure_mode
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def generate_structured(
        self,
        *,
        prompt_name: str,
        payload: dict[str, Any],
        response_model: type[StructuredModel],
    ) -> StructuredModel:
        self.calls.append((prompt_name, payload.copy()))
        candidate = self._structured_payload if self._structured_payload is not None else payload
        if isinstance(candidate, BaseModel):
            candidate = candidate.model_dump()
        return response_model.model_validate(candidate)

    async def generate_text(
        self,
        *,
        prompt_name: str,
        payload: dict[str, Any],
    ) -> str:
        self.calls.append((prompt_name, payload.copy()))
        if self._text_responses:
            response = self._text_responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        if self._failure_mode == "timeout":
            raise TimeoutError("Mock LLM timed out")
        if self._failure_mode == "invalid":
            return "The expected answer is that you should mention every scoring criterion."
        if self._text is not None:
            return self._text
        return self._deterministic_question(payload)

    @staticmethod
    def _deterministic_question(payload: dict[str, Any]) -> str:
        plan = payload.get("question_plan", {})
        competency = str(plan.get("target_competency", "technical_depth"))
        topic = str(plan.get("topic") or "the project")
        question_type = str(plan.get("question_type", "mechanism"))
        if competency == "debugging" or question_type == "failure_analysis":
            return (
                f"Describe a failure involving {topic} and explain how you identified "
                "the root cause?"
            )
        if competency == "ownership":
            return f"What did you personally implement for {topic}?"
        if competency == "decision_making":
            return f"What design choice did you make for {topic}, and why?"
        if competency == "evaluation":
            return f"How did you verify that your work on {topic} improved the system?"
        if competency == "adaptability" or question_type == "counterfactual":
            return f"How would you adapt {topic} if the workload and constraints changed?"
        return f"How does the core mechanism behind {topic} work?"
