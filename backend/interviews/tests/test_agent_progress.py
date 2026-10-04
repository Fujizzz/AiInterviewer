"""Responsibilities: Verify preparation events, prepared-result reuse, early scoring, and failure
handling in the Agent protocol.

Implementation: Exercise the real ASGI handler and Agent state machine with FixtureLLM, then use
barriers to assert event ordering and cancellation. The fixtures do not measure a live provider.
Related Modules: interviews.agent_socket, interviews.agent_session, app.parsing.resume,
app.reporting.final_report, and .agent_fixtures.
Constraints: Fixture results cannot establish provider speed, accuracy, or remote cancellation
behavior; assertions cover local protocol events and state transitions only.

Declaration Index:
- connect: Establish native same-origin ASGI connection, precisely validate complete announcement
  including MCP, return unclosed channel.
- read: Read one JSON event; timeout or non-text response causes immediate failure.
- send_command: Send unique command for explicit subscription event, return request ID.
- collect_until: Collect events within request until specified terminal state, verify association
  and reject unexpected errors.
- disconnect: Disconnect and wait for ASGI task completion.
- ProgressTests: Validate pre-parsing, early scoring delivery, and error boundaries, without
  invoking actual supplier.
- ProgressTests.test_prepare_reuse_and_original_result: Pre-parsing does not trigger question
  generation; same text starts parsing only
  once.
- ProgressTests.test_changed_resume_and_separate_connection: Text change and new connection both
  prevent reuse of old materials.
- ProgressTests.test_prepare_duplicate_and_answer_before_start: Pre-parsing stage rejects duplicate
  requests and premature answers.
- ProgressTests.test_score_arrives_before_blocked_report: Verify early scoring and terminal
  consistency via report barrier.
- ProgressTests.test_score_arrives_before_blocked_report.block_report:
  Only blocks text generation with model, numerical computation remains real.
- ProgressTests.test_cancel_during_prepare: Cancellation possible during prepare phase, task cleaned
  up with no prepared result.
- ProgressTests.test_cancel_during_prepare.block_parse: Blocks parsing and records cancellation via
  finally.
- ProgressTests.test_parse_failure_is_explicit: Parsing failure does not emit completion event, no
  success caching, no content leakage.
- ProgressTests.test_parse_failure_is_explicit.fail_parse: Inject sensitive-marked exception for
  desensitization check.
- ProgressTests.test_report_fallback_is_visible: Existing summary fallback explicitly marked,
  numerical values and budget unchanged.
- ProgressTests.test_report_fallback_is_visible.fail_report:
  Only let report model throw error, other schema uses original fixture.

Variable Index:
None
"""

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

from asgiref.testing import ApplicationCommunicator
from django.test import TransactionTestCase

from app.parsing.resume import ResumeExtraction
from app.reporting.final_report import ReportNarrative, build_final_report
from interviews.agent_records import reserve_request
from interviews.agent_session import AgentSession
from interviews.agent_socket import Answer, Start, agent_socket

from .agent_fixtures import ANSWER, RESUME, FixtureLLM, SafetyTestMixin, complete_fixture_request


async def connect():
    """No external parameters required; establish same-origin ASGI connection, precisely validate
    complete announcement including MCP, return unclosed channel.
    This helper function still uses original business commands; MCP handshake/tool execution
    independently verified by test_answer_completion.
    """
    comm = ApplicationCommunicator(
        agent_socket,
        {
            "type": "websocket",
            "path": "/ws/agent/",
            "scheme": "ws",
            "client": ("127.0.0.1", 12345),
            "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")],
        },
    )
    await comm.send_input({"type": "websocket.connect"})
    assert (await comm.receive_output())["type"] == "websocket.accept"
    hello = await read(comm)
    assert hello["capabilities"] == [
        "prepare",
        "progress",
        "assessment",
        "answer_completion_mcp",
        "early_finish",
        "discard",
        "skip",
    ]
    return comm


async def read(comm):
    """Input test channel, return next JSON; 3 seconds is only for testing assertion boundaries,
    does not change production timeout.
    """
    return json.loads((await comm.receive_output(timeout=3))["text"])


async def send_command(comm, kind, **fields):
    """Input channel, command type, and fields; default subscribes to events, allows explicit
    request_id for duplicate request testing.
    """
    payload = {"type": kind, "request_id": str(uuid4()), "progress_events": True, **fields}
    await comm.send_input({"type": "websocket.receive", "text": json.dumps(payload)})
    return payload["request_id"]


async def collect_until(comm, kind, request_id):
    """Read events with the same request_id until specified type and return list; errors,
    interleaved requests, or infinite events all fail.
    """
    events = []
    for _ in range(20):
        event = await read(comm)
        assert event["request_id"] == request_id
        assert event["type"] != "error", event
        events.append(event)
        if event["type"] == kind:
            return events
    raise AssertionError("Expected terminal event was not received")


async def disconnect(comm):
    """Input channel, send disconnect and wait for local cleanup; does not request cancellation from
    real vendor, returns nothing.
    """
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()


class ProgressTests(SafetyTestMixin, TransactionTestCase):
    """Fixed model combined with real state machine forms protocol test; uses isolated test
    database, not equivalent to real API validation.
    """

    async def test_prepare_reuse_and_original_result(self):
        """prepare only parses once; start precisely reuses, generates only first question,
        validates during verification phase with original budget.
        """
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = await connect()
            try:
                rid = await send_command(comm, "prepare", resume_text=RESUME)
                events = await collect_until(comm, "prepared", rid)
                self.assertEqual(
                    [e["type"] for e in events],
                    [
                        "started",
                        "progress",
                        "progress",
                        "prepared",
                    ],
                )
                self.assertEqual(events[1]["stage"], "resume_parsing")
                self.assertEqual(llm.calls, [ResumeExtraction])
                rid = await send_command(comm, "start", resume_text=RESUME, max_questions=1)
                events = await collect_until(comm, "question", rid)
                self.assertEqual(llm.calls.count(ResumeExtraction), 1)
                self.assertEqual(events[1]["stage"], "question_generation")
                self.assertEqual(events[-1]["interview_state"]["elapsed_seconds"], 0)
                await send_command(comm, "prepare", resume_text=RESUME)
                self.assertEqual((await read(comm))["code"], "already_started")
            finally:
                await disconnect(comm)
            self.assertTrue(llm.closed)

    async def test_changed_resume_and_separate_connection(self):
        """Same text reused within current connection; changing text or opening new connection
        requires re-parsing, no cross-caching allowed.
        """
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = await connect()
            try:
                for text in (RESUME, RESUME, RESUME + " A new project."):
                    rid = await send_command(comm, "prepare", resume_text=text)
                    await collect_until(comm, "prepared", rid)
                self.assertEqual(llm.calls.count(ResumeExtraction), 2)
            finally:
                await disconnect(comm)
        fresh = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=fresh):
            comm = await connect()
            try:
                rid = await send_command(comm, "start", resume_text=RESUME)
                await collect_until(comm, "question", rid)
                self.assertEqual(fresh.calls.count(ResumeExtraction), 1)
            finally:
                await disconnect(comm)

    async def test_prepare_duplicate_and_answer_before_start(self):
        """prepared does not mean interview has started; repeated UUIDs and early answers rejected,
        no additional model calls made.
        """
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = await connect()
            try:
                rid = await send_command(comm, "prepare", resume_text=RESUME)
                await collect_until(comm, "prepared", rid)
                await send_command(comm, "prepare", resume_text=RESUME, request_id=rid)
                self.assertEqual((await read(comm))["code"], "duplicate_request")
                await send_command(comm, "answer", answer_text=ANSWER, question_id="missing")
                self.assertEqual((await read(comm))["code"], "not_started")
                self.assertEqual(llm.calls, [ResumeExtraction])
            finally:
                await disconnect(comm)

    async def test_score_arrives_before_blocked_report(self):
        """Artificially block report text, requiring receipt of same deterministic score first;
        after release, full report and budget remain correct.
        """
        release = asyncio.Event()

        async def block_report(context, history, *, llm=None):
            """Input raw report parameters; only wait on barrier if llm is non-empty, then delegate
            real value and text report process.
            """
            if llm is not None:
                await release.wait()
            return await build_final_report(context, history, llm=llm)

        with (
            patch("interviews.agent_session.BackendLLM", return_value=FixtureLLM()),
            patch("interviews.agent_session.build_final_report", side_effect=block_report),
        ):
            comm = await connect()
            try:
                rid = await send_command(comm, "start", resume_text=RESUME, max_questions=1)
                first = (await collect_until(comm, "question", rid))[-1]
                rid = await send_command(
                    comm, "answer", answer_text=ANSWER, question_id=first["question"]["question_id"]
                )
                events = await collect_until(comm, "assessment", rid)
                score = events[-1]["assessment"]
                self.assertAlmostEqual(score["overall_score"], 3.0)
                running = await read(comm)
                self.assertEqual(
                    (running["stage"], running["state"]), ("report_generation", "running")
                )
                self.assertFalse(release.is_set())
                release.set()
                final = (await collect_until(comm, "finished", rid))[-1]["result"]
                self.assertEqual(final["final_report"]["overall_score"], score["overall_score"])
                self.assertEqual(final["final_report"]["competencies"], score["competencies"])
                self.assertLess(final["interview_state"]["elapsed_seconds"], 120)
                self.assertEqual((await comm.receive_output())["code"], 1000)
                await comm.wait()
            finally:
                release.set()
                await disconnect(comm)

    async def test_cancel_during_prepare(self):
        """Cancel connection if parse barrier not completed, verify cancellation passed into parse
        coroutine and no successful prepared response received.
        """
        stopped = asyncio.Event()

        async def block_parse(*args, **kwargs):
            """Input simulated parse parameters; continuously wait until cancelled, finally set
            cleanup flag, no return data.
            """
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        llm = FixtureLLM()
        with (
            patch("interviews.agent_session.BackendLLM", return_value=llm),
            patch("interviews.agent_session.parse_resume_profile", side_effect=block_parse),
        ):
            comm = await connect()
            await send_command(comm, "prepare", resume_text=RESUME)
            self.assertEqual((await read(comm))["type"], "started")
            self.assertEqual((await read(comm))["state"], "running")
            await send_command(comm, "cancel")
            self.assertEqual((await read(comm))["type"], "cancelled")
            self.assertEqual((await comm.receive_output())["code"], 1000)
            await comm.wait()
            self.assertTrue(stopped.is_set())
            self.assertTrue(llm.closed)

    async def test_parse_failure_is_explicit(self):
        """Parse exceptions preserve failure semantics, prohibit sending completion events or
        leaking candidate content, close failed connections.
        """

        async def fail_parse(*args, **kwargs):
            """Input any test parse parameters, raise exception with sensitive marker; no cacheable
            candidate data produced.
            """
            raise ValueError("private-resume-marker")

        with (
            patch("interviews.agent_session.BackendLLM", return_value=FixtureLLM()),
            patch("interviews.agent_session.parse_resume_profile", side_effect=fail_parse),
            self.assertLogs("interviews", level="INFO") as captured,
        ):
            comm = await connect()
            await send_command(comm, "prepare", resume_text=RESUME)
            self.assertEqual((await read(comm))["type"], "started")
            self.assertEqual((await read(comm))["state"], "running")
            error = await read(comm)
            self.assertEqual(error["code"], "agent_failed")
            self.assertNotIn("private-resume-marker", json.dumps(error))
            self.assertEqual((await comm.receive_output())["code"], 1011)
            await comm.wait()
        self.assertNotIn("private-resume-marker", " ".join(captured.output))

    async def test_report_fallback_is_visible(self):
        """Report model errors fall back to original report function; backend explicitly marks
        failure rather than falsely claiming text model success.
        """
        fixture = FixtureLLM()

        def fail_report(prompt, data, schema):
            """Only ReportNarrative raises test exceptions; other calls maintain deterministic data
            and real Agent decisions.
            """
            if schema is ReportNarrative:
                raise RuntimeError("private-report-marker")
            return fixture(prompt, data, schema)

        session = AgentSession(llm=fail_report)
        command = Start(request_id=uuid4(), type="start", resume_text=RESUME, max_questions=1)
        await reserve_request(session.interview_id, command)
        first = await session.start(command)
        await complete_fixture_request(session.interview_id, command.request_id, first)
        with self.assertLogs("interviews.agent_session", level="INFO") as captured:
            command = Answer(
                request_id=uuid4(),
                type="answer",
                question_id=first["question"]["question_id"],
                answer_text=ANSWER,
            )
            await reserve_request(session.interview_id, command)
            result = await session.answer(command)
        self.assertEqual(result["result"]["report_narrative_status"], "fallback")
        self.assertAlmostEqual(result["result"]["final_report"]["overall_score"], 3.0)
        self.assertNotIn("private-report-marker", " ".join(captured.output))
        self.assertLess(result["result"]["interview_state"]["elapsed_seconds"], 120)
