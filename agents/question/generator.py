"""Provider-neutral final question wording generation."""

from __future__ import annotations

from agents.config import AgentSettings, load_agent_settings
from agents.domain.errors import LLMTimeout
from agents.ports import LLMPort
from agents.timeouts import call_with_timeout
from shared.contracts import PlannedQuestion


class QuestionGenerator:
    """Ask the LLM for text and preserve every structured plan field."""

    prompt_name = "question_generator_v1"

    def __init__(
        self,
        llm: LLMPort,
        settings: AgentSettings | None = None,
    ) -> None:
        self._llm = llm
        self._settings = settings or load_agent_settings()

    async def generate(
        self,
        question_plan: PlannedQuestion,
        context: str,
        *,
        repair_errors: list[str] | None = None,
    ) -> PlannedQuestion:
        text = await call_with_timeout(
            self._llm.generate_text(
                prompt_name=self.prompt_name,
                payload={
                    "question_plan": question_plan.model_dump(mode="json"),
                    "context": context,
                    "repair_errors": list(repair_errors or ()),
                },
            ),
            timeout_seconds=self._settings.timeouts.llm_generation_seconds,
            error_factory=lambda: LLMTimeout("LLM question generation timed out"),
        )
        return question_plan.model_copy(update={"text": text})
