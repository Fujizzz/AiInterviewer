"""Responsibilities: Verify ASGI and interview startup do not await optional RAG readiness.
Implementation: Run startup with explicit cold-port doubles and the real session initializer.
Related Modules: config.asgi, interviews.agent_session, and app.adapters.rag.
Declaration Index:
- RAGStartupTests.test_lifespan_acknowledges_before_rag_is_ready: Startup acknowledgement is prompt.
- RAGStartupTests.test_session_returns_intro_before_rag_is_ready: Intro starts with a cold port.
Variable Index: None.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from config.asgi import handle_lifespan
from django.test import SimpleTestCase

from app.adapters.repository import InMemoryInterviewRepository
from interviews.agent_session import AgentSession
from interviews.agent_socket import Start
from tests.agent.integration.test_question_pipeline_integration import pipeline_request

from .agent_fixtures import FixtureLLM


class RAGStartupTests(SimpleTestCase):
    async def test_lifespan_acknowledges_before_rag_is_ready(self):
        """A cold port cannot delay server startup; shutdown still owns its cleanup."""
        port = Mock(is_ready=False)
        port.wait_ready.side_effect = AssertionError("Startup must not wait")
        messages, output = asyncio.Queue(), asyncio.Queue()
        with (
            patch("app.adapters.rag.start_shared_rag", return_value=port) as start,
            patch("app.adapters.rag.close_shared_rag", new_callable=AsyncMock) as close,
        ):
            task = asyncio.create_task(handle_lifespan(messages.get, output.put))
            try:
                await messages.put({"type": "lifespan.startup"})
                result = await asyncio.wait_for(output.get(), 1)
                self.assertEqual(result, {"type": "lifespan.startup.complete"})
                start.assert_called_once()
                port.wait_ready.assert_not_called()
                await messages.put({"type": "lifespan.shutdown"})
                self.assertEqual(await output.get(), {"type": "lifespan.shutdown.complete"})
                await task
                close.assert_awaited_once()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_session_returns_intro_before_rag_is_ready(self):
        """Use in-memory storage only; a real fixed intro must not invoke the cold adapter."""
        port = Mock(is_ready=False)
        port.wait_ready.side_effect = AssertionError("Interview must not wait")
        session = AgentSession(llm=FixtureLLM())
        session.app.repository = InMemoryInterviewRepository()
        logs = session.app.repository.decision_logs_for
        session.app.repository.decision_logs_for = AsyncMock(side_effect=logs)
        session.profile = pipeline_request().candidate_profile
        session.candidate_name = "Offline candidate"
        session.prepare = AsyncMock()
        try:
            with (
                patch("app.adapters.rag.start_shared_rag", return_value=port),
                patch(
                    "interviews.agent_session.load_bound_questions",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
            ):
                result = await asyncio.wait_for(
                    session.start(
                        Start(type="start", request_id=uuid4(), resume_text="Offline CV")
                    ),
                    1,
                )
            self.assertEqual(result["type"], "question")
            self.assertEqual(result["question"]["intent"], "SELF_INTRODUCTION")
            port.wait_ready.assert_not_called()
            port.retrieve.assert_not_called()
        finally:
            if getattr(session, "service", None) is not None:
                await session.service.close_background()
            session.close()
