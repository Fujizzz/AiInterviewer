"""RAG resources are optional and shared across simultaneous sessions."""

import asyncio
from unittest.mock import Mock

import pytest

from app.adapters import rag as runtime


@pytest.fixture(autouse=True)
def isolated_runtime():
    runtime._close_shared_rag()
    yield
    runtime._close_shared_rag()


@pytest.mark.asyncio
async def test_rag_is_warmed_once_shared_and_closed(monkeypatch):
    resource = Mock()
    resource.wait_ready.return_value = True
    factory = Mock(return_value=resource)
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setattr(runtime, "ProcessRAG", factory)
    first, second = await asyncio.gather(runtime.get_shared_rag(), runtime.get_shared_rag())
    assert first is second is resource
    factory.assert_called_once_with()
    await runtime.close_shared_rag()
    resource.close.assert_called_once()


@pytest.mark.asyncio
async def test_missing_rag_resources_degrade_once(monkeypatch):
    factory = Mock(side_effect=RuntimeError("optional stack missing"))
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setattr(runtime, "ProcessRAG", factory)
    assert await runtime.get_shared_rag() is None
    assert await runtime.get_shared_rag() is None
    factory.assert_called_once()


@pytest.mark.asyncio
async def test_disabled_rag_never_loads_models(monkeypatch):
    factory = Mock()
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setattr(runtime, "ProcessRAG", factory)
    assert await runtime.get_shared_rag() is None
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_cli_application_injects_rag_and_reports_committed_question():
    from agents.question.react import QuestionAgentDecision
    from app.adapters.assessment import AnswerDecision
    from app.application import MVPInterviewApplication
    from tests.agent.mocks import MockRAGAdapter
    from tests.app.fixtures import FixtureLLM
    from tests.app.test_interview import RESUME

    class Provider(FixtureLLM):
        def __call__(self, prompt, data, schema):
            if schema is AnswerDecision:
                decision = super().__call__(prompt, data, schema)
                decision.analysis.thread_complete = False
                decision.analysis.missing_information = ["Object lifetime management"]
                return decision
            if schema is QuestionAgentDecision and data.get("retrieved_evidence"):
                return schema(
                    action="final",
                    text="How does Python manage the lifetime of an object?",
                    intent_id=data["locked_intent"]["intent_id"],
                )
            return super().__call__(prompt, data, schema)

    rag = MockRAGAdapter()
    result = await MVPInterviewApplication(Provider(), rag=rag).run(
        RESUME,
        max_questions=4,
        read_answer=lambda _: "I implemented the component and measured the results.",
        write=lambda _: None,
    )
    attempts = result["project_knowledge"].values()
    asked = [attempt for attempt in attempts if attempt["status"] == "asked"]
    assert asked
    assert asked[0]["question_id"]
    assert rag.requests


@pytest.mark.asyncio
async def test_cli_can_finish_while_optional_rag_is_still_loading(monkeypatch):
    from app.application import MVPInterviewApplication
    from tests.app.fixtures import FixtureLLM
    from tests.app.test_interview import RESUME

    resource = Mock(is_ready=False)
    resource.wait_ready.side_effect = AssertionError("Interview must not await RAG warmup")
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setattr(runtime, "ProcessRAG", Mock(return_value=resource))
    result = await MVPInterviewApplication(FixtureLLM()).run(
        RESUME,
        max_questions=2,
        read_answer=lambda _: "I implemented the parser.",
        write=lambda _: None,
    )
    assert result["interview_finished"]
    resource.wait_ready.assert_not_called()
    resource.retrieve.assert_not_called()
    resource.close.assert_called_once()
