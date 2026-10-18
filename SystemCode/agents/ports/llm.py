"""Provider-neutral LLM boundary for later question phases."""

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class LLMPort(Protocol):
    async def generate_structured(
        self,
        *,
        prompt_name: str,
        payload: dict[str, Any],
        response_model: type[StructuredModel],
    ) -> StructuredModel: ...

    async def generate_text(
        self,
        *,
        prompt_name: str,
        payload: dict[str, Any],
    ) -> str: ...
