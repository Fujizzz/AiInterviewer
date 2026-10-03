"""Responsibilities: Exercise Agent WebSocket behavior and provider setup using offline fixtures and
an isolated test database.

Implementation: Drive the real ASGI handlers and Agent state machine while replacing vendor calls
with deterministic doubles; no external model is contacted.
Related Modules: interviews.agent_socket, interviews.agent_session, interviews.agent_records, and
.agent_fixtures.

Declaration Index:
- AgentTests:
  Use ASGI message-driven full interview, TransactionTestCase isolates persistent data.
- AgentTests.connect:
  Establish test connection under same access policy constraints; caller must wait for terminal
  state or explicitly call disconnect.
- AgentTests.accepted:
  Consume handshake and announcement, validate network timing contract using MVP’s 120 seconds.
- AgentTests.read:
  Read one JSON; timeout implies protocol did not proceed as expected.
- AgentTests.command:
  Send command with unique UUID and read first response; also supports explicit repeated ID.
- AgentTests.disconnect:
  Send disconnect and wait for local coroutine to finish, avoiding test leftovers with hanging
  tasks.
- AgentTests.test_full_interview_matches_terminal_mvp:
  Same input yields consistent question count, evaluation, state budget, and report between network
  and original terminal.
- AgentTests.test_invalid_commands_do_not_create_model:
  Corrupted JSON, unknown fields, empty text, wrong type, and premature answers do not trigger paid
  requests.
- AgentTests.test_duplicate_and_stale_question_rejected_before_model_call:
  Duplicate start/answer and stale questions explicitly rejected; client cannot re-consume same
  round.
- AgentTests.test_busy_cancel_and_disconnect_release_session:
  Reject second command if model not complete; cancel and disconnect both cancel local task and
  clean up session.
- AgentTests.test_busy_cancel_and_disconnect_release_session.slow_start:
  Simulate upstream task not yet returned; finally proves cancellation was delivered.
- AgentTests.test_errors_are_redacted_and_terminal:
  Initialization or upstream failure must not expose exception body, nor produce false success.
- AgentTests.test_errors_are_redacted_and_terminal.fail:
  Generate exception with sensitive markers; verify logs and responses do not echo them.
- AgentTests.test_origin_size_limits_and_static_secret_protection:
  Reject external connections and oversized messages; secret key file cannot be read via static
  resource routing.
- AgentTests.test_connections_keep_candidate_state_isolated:
  Two connections establish separate Agent repositories; questions and interview IDs do not leak
  between them.
- ProviderTests:
  Model client reuses Django-loaded unified configuration, preserves MVP call parameters and
  exception behavior.
- ProviderTests.test_missing_config_does_not_initialize_sdk:
  Missing vendor, model, or key stops initialization explicitly; no default model or mock
  implementation enabled.
- ProviderTests.test_provider_options_without_reloading_dotenv:
  Use only loaded environment; validate generation/resume budget and remaining deadline, maintain
  temperature and disable SDK retries.
- ProviderTests.test_inflight_client_closed_only_after_call_returns:
  Cancellation does not break in-flight sync SDK; client and service slot released only after
  backend returns.
- ProviderTests.test_inflight_client_closed_only_after_call_returns.blocked:
  Wait at controlled barrier, request closure while call still executing.

Variable Index:
None
"""

import asyncio
import json
import threading
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import Mock, patch
from uuid import uuid4

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase, TransactionTestCase

from agents.model_calls import ModelCall, current_model_call
from app.application import MVPInterviewApplication
from app.providers.llm import LLMError, OpenAILLM
from interviews.agent_provider import BackendLLM
from interviews.agent_session import AgentSession
from interviews.agent_socket import MAX_MESSAGE_BYTES, agent_socket
from interviews.capacity import CapacityExceeded, take_slot

from .agent_fixtures import ANSWER, RESUME, FixtureLLM, SafetyTestMixin


class AgentTests(SafetyTestMixin, TransactionTestCase):
    """Use ASGI message-driven full interview, TransactionTestCase isolates persistent data.
    """

    async def connect(self, origin="http://localhost", client="127.0.0.1"):
        """Establish test connection under same access policy constraints; caller must wait for
        terminal state or explicitly call disconnect.
        """
        comm = ApplicationCommunicator(
            agent_socket,
            {
                "type": "websocket",
                "path": "/ws/agent/",
                "scheme": "ws",
                "client": (client, 12345),
                "headers": [(b"host", b"localhost"), (b"origin", origin.encode())],
            },
        )
        await comm.send_input({"type": "websocket.connect"})
        return comm

    async def accepted(self):
        """Consume handshake and announcement, validate network timing contract using MVP’s 120
        seconds.
        """
        comm = await self.connect()
        self.assertEqual((await comm.receive_output())["type"], "websocket.accept")
        self.assertEqual((await self.read(comm))["seconds_per_question"], 120)
        return comm

    async def read(self, comm):
        """Read one JSON; timeout implies protocol did not proceed as expected.
        """
        return json.loads((await comm.receive_output(timeout=3))["text"])

    async def command(self, comm, kind, **fields):
        """Send command with unique UUID and read first response; also supports explicit repeated
        ID.
        """
        payload = {"type": kind, "request_id": str(uuid4()), **fields}
        await comm.send_input({"type": "websocket.receive", "text": json.dumps(payload)})
        return await self.read(comm)

    async def disconnect(self, comm):
        """Send disconnect and wait for local coroutine to finish, avoiding test leftovers with
        hanging tasks.
        """
        await comm.send_input({"type": "websocket.disconnect", "code": 1000})
        await comm.wait()

    async def test_full_interview_matches_terminal_mvp(self):
        """Same input yields consistent question count, evaluation, state budget, and report between
        network and original terminal.
        """
        for count in (1, 3):
            with self.subTest(count=count):
                llm = FixtureLLM()
                with patch("interviews.agent_session.BackendLLM", return_value=llm):
                    comm = await self.accepted()
                    started = await self.command(
                        comm, "start", resume_text=RESUME, max_questions=count
                    )
                    self.assertEqual(started["type"], "started")
                    message = await self.read(comm)
                    while message["type"] == "question":
                        started = await self.command(
                            comm,
                            "answer",
                            answer_text=ANSWER,
                            question_id=message["question"]["question_id"],
                        )
                        self.assertEqual(started["type"], "started")
                        message = await self.read(comm)
                    self.assertEqual(message["type"], "finished")
                    self.assertEqual((await comm.receive_output())["code"], 1000)
                    await comm.wait()
                expected = await MVPInterviewApplication(FixtureLLM()).run(
                    RESUME,
                    max_questions=count,
                    read_answer=lambda _: ANSWER,
                    write=lambda _: None,
                )
                result = message["result"]
                self.assertTrue(result["interview_finished"])
                self.assertEqual(len(result["question_history"]), count)
                self.assertEqual(result["final_report"], expected["final_report"])
                self.assertLess(result["interview_state"]["elapsed_seconds"], 120)
                self.assertEqual(result["interview_plan"]["duration_seconds"], 1800)
                self.assertEqual(
                    result["interview_state"]["competencies"],
                    expected["interview_state"]["competencies"],
                )
                self.assertEqual(
                    [q["question"] for q in result["question_history"]],
                    [q["question"] for q in expected["question_history"]],
                )
                self.assertTrue(llm.closed)

    async def test_invalid_commands_do_not_create_model(self):
        """Corrupted JSON, unknown fields, empty text, wrong type, and premature answers do not
        trigger paid requests.
        """
        with patch("interviews.agent_socket.AgentSession") as factory:
            comm = await self.accepted()
            for raw in (
                "{",
                "[]",
                '{"type":[]}',
                json.dumps({"type": "start", "request_id": str(uuid4()), "resume_text": " "}),
                json.dumps(
                    {
                        "type": "start",
                        "request_id": str(uuid4()),
                        "resume_text": RESUME,
                        "max_questions": True,
                    }
                ),
                json.dumps(
                    {
                        "type": "start",
                        "request_id": str(uuid4()),
                        "resume_text": RESUME,
                        "api_key": "must-not-accept",
                    }
                ),
            ):
                await comm.send_input({"type": "websocket.receive", "text": raw})
                self.assertEqual((await self.read(comm))["code"], "invalid_message")
            reply = await self.command(comm, "answer", question_id="old", answer_text=ANSWER)
            self.assertEqual(reply["code"], "not_started")
            factory.assert_not_called()
            await self.disconnect(comm)

    async def test_duplicate_and_stale_question_rejected_before_model_call(self):
        """Duplicate start/answer and stale questions explicitly rejected; client cannot re-consume
        same round.
        """
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = await self.accepted()
            first = str(uuid4())
            await self.command(comm, "start", request_id=first, resume_text=RESUME)
            question = (await self.read(comm))["question"]["question_id"]
            calls = len(llm.calls)
            self.assertEqual(
                (await self.command(comm, "start", request_id=first, resume_text=RESUME))["code"],
                "duplicate_request",
            )
            self.assertEqual(
                (await self.command(comm, "start", resume_text=RESUME))["code"], "already_started"
            )
            self.assertEqual(
                (await self.command(comm, "answer", question_id="old", answer_text=ANSWER))["code"],
                "stale_question",
            )
            self.assertEqual(len(llm.calls), calls)
            answer_id = str(uuid4())
            await self.command(
                comm, "answer", request_id=answer_id, question_id=question, answer_text=ANSWER
            )
            await self.read(comm)
            calls = len(llm.calls)
            self.assertEqual(
                (
                    await self.command(
                        comm,
                        "answer",
                        request_id=answer_id,
                        question_id=question,
                        answer_text=ANSWER,
                    )
                )["code"],
                "duplicate_request",
            )
            self.assertEqual(
                (await self.command(comm, "answer", question_id=question, answer_text=ANSWER))[
                    "code"
                ],
                "stale_question",
            )
            self.assertEqual(len(llm.calls), calls)
            await self.disconnect(comm)

    async def test_busy_cancel_and_disconnect_release_session(self):
        """Reject second command if model not complete; cancel and disconnect both cancel local task
        and clean up session.
        """
        for cancel in (True, False):
            blocked = asyncio.Event()
            stopped = asyncio.Event()
            entered = asyncio.Event()

            async def slow_start(command, blocked=blocked, stopped=stopped, entered=entered):
                """Input test command and synchronous barrier; mark business entered, finally prove
                cancellation was delivered.
                """
                try:
                    entered.set()
                    await blocked.wait()
                finally:
                    stopped.set()

            fake = Mock(spec=AgentSession)
            fake.interview_id = str(uuid4())
            fake.start = slow_start
            with patch("interviews.agent_socket.AgentSession", return_value=fake):
                comm = await self.accepted()
                await self.command(comm, "start", resume_text=RESUME)
                await asyncio.wait_for(entered.wait(), timeout=3)
                busy = await self.command(comm, "start", resume_text=RESUME)
                self.assertEqual(busy["code"], "busy")
                if cancel:
                    self.assertEqual((await self.command(comm, "cancel"))["type"], "cancelled")
                    self.assertEqual((await comm.receive_output())["code"], 1000)
                    await comm.wait()
                else:
                    await self.disconnect(comm)
                self.assertTrue(stopped.is_set())
                fake.close.assert_called_once()

    async def test_errors_are_redacted_and_terminal(self):
        """Initialization or upstream failure must not expose exception body, nor produce false
        success.
        """
        for setup in (True, False):
            fake = Mock(spec=AgentSession)
            fake.interview_id = str(uuid4())

            async def fail(command):
                """Generate exception with sensitive markers; verify logs and responses do not echo
                them.
                """
                raise RuntimeError("secret-input-must-not-leak")

            fake.start = fail
            kwargs = (
                {"side_effect": LLMError("secret-input-must-not-leak")}
                if setup
                else {"return_value": fake}
            )
            with (
                patch("interviews.agent_socket.AgentSession", **kwargs),
                self.assertLogs("interviews.agent_socket", level="INFO") as logs,
            ):
                comm = await self.accepted()
                reply = await self.command(comm, "start", resume_text=RESUME)
                if not setup:
                    self.assertEqual(reply["type"], "started")
                    reply = await self.read(comm)
                self.assertEqual(reply["code"], "configuration_error" if setup else "agent_failed")
                self.assertNotIn("secret-input", json.dumps(reply))
                self.assertEqual((await comm.receive_output())["code"], 1011)
                await comm.wait()
            self.assertNotIn("secret-input", " ".join(logs.output))

    async def test_origin_size_limits_and_static_secret_protection(self):
        """Reject external connections and excessively large messages; the key file must not be
        accessible via static resource routing.
        """
        for kwargs in ({"origin": "https://example.com"}, {"client": "192.0.2.1"}):
            comm = await self.connect(**kwargs)
            self.assertEqual((await comm.receive_output())["type"], "websocket.close")
            await comm.wait()
        comm = await self.accepted()
        await comm.send_input({"type": "websocket.receive", "text": " " * (MAX_MESSAGE_BYTES + 1)})
        self.assertEqual((await self.read(comm))["code"], "size_limit")
        self.assertEqual((await comm.receive_output())["code"], 1009)
        await comm.wait()
        response = await self.async_client.get(
            "/stream-demo/.env", REMOTE_ADDR="127.0.0.1", HTTP_HOST="localhost"
        )
        self.assertIn(response.status_code, (403, 404))

    async def test_connections_keep_candidate_state_isolated(self):
        """Two connections establish separate Agent repositories, with issues and interview IDs
        strictly isolated from each other.
        """
        with patch("interviews.agent_session.BackendLLM", side_effect=FixtureLLM):
            first, second = await self.accepted(), await self.accepted()
            await self.command(first, "start", resume_text=RESUME)
            a = await self.read(first)
            await self.command(second, "start", resume_text=RESUME)
            b = await self.read(second)
            self.assertNotEqual(a["interview_id"], b["interview_id"])
            reply = await self.command(
                second, "answer", question_id=a["question"]["question_id"], answer_text=ANSWER
            )
            self.assertEqual(reply["code"], "stale_question")
            await self.disconnect(first)
            await self.disconnect(second)


class ProviderTests(SimpleTestCase):
    """The model client reuses Django's already-loaded unified configuration, preserving MVP call
    parameters and exception behavior.
    """

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_config_does_not_initialize_sdk(self):
        """Explicitly halt when supplier, model, or key is missing; do not enable default models or
        mock implementations.
        """
        with patch("interviews.agent_provider.OpenAI") as sdk:
            with self.assertRaises(LLMError):
                BackendLLM()
            sdk.assert_not_called()

    @patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-only",
            "OPENAI_MODEL": "configured-model",
            "OPENAI_TEMPERATURE": "0",
        },
        clear=True,
    )
    def test_provider_options_without_reloading_dotenv(self):
        """Both SDK and dotenv are mocked, avoiding actual model access; verify backend assembly
        compatibility with two timeout budgets from the parent class.

        Input is an isolated environment configuration and explicit model invocation context; assert
        budget for resume, standard generation budget, and remaining deadline constraints.
        Finally restore context to avoid polluting subsequent tests, without validating real
        suppliers.
        """
        with (
            patch("interviews.agent_provider.OpenAI") as sdk,
            patch("app.providers.llm.load_dotenv") as dotenv,
        ):
            provider = BackendLLM()
            sdk.assert_called_once_with(api_key="test-only", timeout=30.0, max_retries=0)
            dotenv.assert_not_called()
            self.assertEqual(provider.options, {"temperature": 0.0})
            call = ModelCall("resume_extraction", None, None, perf_counter() + 120)
            token = current_model_call.set(call)
            try:
                self.assertEqual(provider._remaining_timeout(), 90.0)
                call.operation = "question_generation"
                self.assertEqual(provider._remaining_timeout(), 30.0)
                call.operation = "resume_extraction"
                call.deadline = perf_counter() + 10
                self.assertGreater(provider._remaining_timeout(), 0)
                self.assertLessEqual(provider._remaining_timeout(), 10)
            finally:
                current_model_call.reset(token)
            provider.close()
            sdk.return_value.close.assert_called_once()

    @patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-only",
            "OPENAI_MODEL": "configured-model",
        },
        clear=True,
    )
    def test_inflight_client_closed_only_after_call_returns(self):
        """Cancelation does not disrupt ongoing synchronous SDK operations; release client and
        service slots only after backend response; use barriers to simulate model usage.
        """
        entered, release = threading.Event(), threading.Event()

        def blocked(*args):
            """Wait at a controlled barrier to allow test to request shutdown while calls are still
            executing.
            """
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release model call")
            return "ok"

        with (
            TemporaryDirectory() as directory,
            patch("interviews.agent_provider.OpenAI") as sdk,
            patch.object(OpenAILLM, "__call__", blocked),
        ):
            provider = BackendLLM()
            provider.capacity_lease = take_slot("agent", 1, directory)
            worker = threading.Thread(target=provider, args=("", {}, FixtureLLM))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                provider.close()
                provider.capacity_lease.release()
                with self.assertRaises(CapacityExceeded):
                    take_slot("agent", 1, directory)
                sdk.return_value.close.assert_not_called()
                with self.assertRaises(LLMError):
                    provider("", {}, FixtureLLM)
            finally:
                release.set()
                worker.join(3)
            sdk.return_value.close.assert_called_once()
            take_slot("agent", 1, directory).release()
