"""Reuse the application's provider and deadline boundary without Agent policy logic."""

import asyncio
import math
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agents.model_calls import run_model_call
from app.providers.llm import LLMError, StructuredLLM

T = TypeVar("T", bound=BaseModel)


class EvaluationStageError(ValueError):
    """Safe failure labels; no provider response or candidate text in the message."""

    def __init__(self, stage: str, reason: str):
        self.stage = stage
        self.reason = reason
        super().__init__(f"{stage}_{reason}")


class EvaluationModelClient:
    def __init__(self, llm: StructuredLLM, *, timeout_seconds: float) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self._llm = llm
        self._timeout_seconds = timeout_seconds

    async def call(self, *, stage: str, prompt: str, payload: dict, schema: type[T]) -> T:
        try:
            result = await run_model_call(
                lambda: asyncio.to_thread(self._llm, prompt, payload, schema),
                operation=f"evaluation_{stage}",
                question_id=payload.get("question", {}).get("question_id"),
                timeout_seconds=self._timeout_seconds,
            )
            # Revalidate even provider-created models (model_construct/copy can bypass checks).
            return schema.model_validate(
                result.model_dump() if isinstance(result, BaseModel) else result
            )
        except TimeoutError as error:
            raise EvaluationStageError(stage, "timeout") from error
        except ValidationError as error:
            raise EvaluationStageError(stage, "invalid_output") from error
        except LLMError as error:
            reason = {
                "timeout": "timeout",
                "invalid_json": "invalid_output",
                "incomplete_output": "invalid_output",
            }.get(error.code, "model_error")
            raise EvaluationStageError(stage, reason) from error


def load_prompt(name: str) -> str:
    return (Path(__file__).with_name("prompts") / f"{name}.md").read_text(encoding="utf-8")
