"""Offline STT completion/MCP integration tests; no model or microphone access.

Responsibilities: verify server silence, flush revocation, signed receipts and real Agent dispatch.
Implementation: Replace only ASR, classifier, and business-provider boundaries; run actual ASGI
socket handlers in process, along with completion state, MCP validation, safety gateway, and
isolated database persistence.
Related Modules: speech.socket, answer_mcp, agent_socket and explicit agent_fixtures.

Declaration Index:
- socket_scope: Construct a same-origin local ASGI scope.
- read_json: Read one bounded JSON protocol event.
- send_json: Send one encoded ASGI text message.
- initialize_mcp: Perform the actual connection-level MCP handshake.
- EndRecognition: Offline ASR fixture with independently controlled supplementary text.
- EndRecognition.__init__: Store callback and bilingual hints, never construct SDK.
- EndRecognition.start: Complete offline startup without I/O.
- EndRecognition.feed: Emit one final end-intent utterance, then optional supplemental draft.
- EndRecognition.stop: Optionally append late finalized words before provider completion.
- CompletionSpeechTests: Run real STT socket/observer with an explicit binary classifier double.
- CompletionSpeechTests.capture: Start opt-in ASR and consume its initial final-sentence draft.
- CompletionSpeechTests.test_three_second_silence_issues_bound_final_receipt: Verify real timer and
  text receipt.
- CompletionSpeechTests.test_paraphrase_is_sent_without_keyword_filtering: Verify unrestricted final
  wording reaches
  inference.
- CompletionSpeechTests.test_supplement_revokes_before_and_during_flush: Verify draft and late-final
  revocation.
- CompletionSpeechTests.test_voice_without_transcript_revokes_decision: Verify audible PCM
  independently revokes.
- CompletionSpeechTests.test_classifier_failure_is_explicit: Verify failure terminates capture
  without a receipt.
- CompletionReceiptTests: Check exact owner/question/text bindings and expiry without external I/O.
- CompletionReceiptTests.test_receipt_binding_expiry_and_tampering: Reject misuse of a signed
  boundary.
- CompletionMCPTests: Use real Agent/safety/persistence with offline provider outputs.
- CompletionMCPTests.test_tool_advances_once_and_stale_calls_are_rejected: Verify MCP -> evaluation
  -> next step.
- CompletionMCPTests.test_invalid_receipt_never_starts_business_model: Verify admission before
  billable execution.

Variable Index:
None

Constraints:
Synthetic classifier output does not establish Qwen accuracy. Real 3s waits test timing without
changing production thresholds; temporary Django test database prevents changes to user interviews.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase, TransactionTestCase

from agents.orchestrator import InterviewAgentService
from interviews.agent_socket import agent_socket
from interviews.answer_mcp import (
    InterviewMCP,
    issue_completion_receipt,
    verify_completion_receipt,
)
from interviews.speech.socket import stt_socket
from interviews.tests.agent_fixtures import ANSWER, RESUME, FixtureLLM, SafetyTestMixin


def socket_scope(path):
    """Inputs: route path. Outputs: local same-origin ASGI scope; no user means existing
    dev mode.
    """
    return {
        "type": "websocket",
        "path": path,
        "scheme": "ws",
        "client": ("127.0.0.1", 123),
        "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")],
    }


async def read_json(comm, timeout=4):
    """Inputs: communicator/deadline. Outputs: decoded server text; unexpected close fails
    the test.
    """
    return json.loads((await comm.receive_output(timeout=timeout))["text"])


async def send_json(comm, message):
    """Inputs: communicator/JSON data. Outputs: None; no retries or remote network access."""
    await comm.send_input({"type": "websocket.receive", "text": json.dumps(message)})


async def initialize_mcp(comm):
    """Inputs: accepted socket. Outputs: None; assert negotiation/discovery and send
    initialized.
    """
    identifier = str(uuid4())
    await send_json(
        comm,
        {
            "jsonrpc": "2.0",
            "id": identifier,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
        },
    )
    assert (await read_json(comm))["result"]["capabilities"] == {"tools": {}}
    await send_json(comm, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    await send_json(comm, {"jsonrpc": "2.0", "id": str(uuid4()), "method": "tools/list"})
    assert (await read_json(comm))["result"]["tools"][0]["name"] == "finish_current_answer"


class EndRecognition:
    """Functionality: deterministic ASR callback fixture. Logic: one cue then optional
    supplements.
    Constraints: no provider, audio recognition or synthetic claim about actual accuracy.
    State: late_text inserts a final supplement during stop; draft_on_feed makes later PCM
    produce new words. Both are explicit offline test controls; frames counts feed calls.
    utterance supplies the synthetic first transcript, including unrestricted paraphrases.
    """

    late_text = False
    draft_on_feed = True
    utterance = "I built it. That's all."

    def __init__(self, emit, language_hints=None):
        """Inputs: callback/hints. Outputs: isolated frame counter; verify bilingual opt-in
        hints.
        """
        self.emit, self.frames = emit, 0
        assert language_hints == ["zh", "en"]

    def start(self):
        """Inputs: fixture state. Outputs: None; explicitly avoid SDK/network startup."""

    def feed(self, pcm):
        """Inputs: valid PCM bytes. Outputs: callback events; second frame may reveal new draft
        words.
        """
        self.frames += 1
        if self.frames == 1:
            self.emit(
                {
                    "type": "sentence",
                    "segment": "1",
                    "text": self.utterance,
                    "is_final": True,
                }
            )
        elif self.draft_on_feed:
            self.emit(
                {"type": "sentence", "segment": "2", "text": "Another detail", "is_final": False}
            )

    def stop(self):
        """Inputs: late_text class option. Outputs: optional final supplement then provider
        completion.
        """
        if self.late_text or self.frames > 1 and self.draft_on_feed:
            self.emit(
                {"type": "sentence", "segment": "2", "text": "Another detail.", "is_final": True}
            )
        self.emit({"type": "complete"})


class CompletionSpeechTests(SimpleTestCase):
    """Functionality: real STT/observer protocol tests. Logic: only classifier/ASR are mocked.
    Constraints: no database, paid calls or hardware; production silence interval is
    preserved.
    """

    async def capture(self):
        """Inputs: patched providers. Outputs: running communicator with cue admitted at
        time zero.
        """
        comm = ApplicationCommunicator(stt_socket, socket_scope("/ws/speech/stt/"))
        await comm.send_input({"type": "websocket.connect"})
        await comm.receive_output()
        await read_json(comm)
        await send_json(comm, {"type": "start", "completion_detection": True, "question_id": "q1"})
        self.assertEqual((await read_json(comm))["type"], "started")
        await comm.send_input({"type": "websocket.receive", "bytes": b"\0\0" * 1600})
        self.assertEqual((await read_json(comm))["type"], "partial")
        return comm

    async def test_three_second_silence_issues_bound_final_receipt(self):
        """Verify real silence boundary and finalization receipt, using positive offline
        inference
        only.
        """
        classifier = SimpleNamespace(classify=AsyncMock(return_value=True), close=AsyncMock())
        with (
            patch("interviews.speech.socket.RecognitionSession", EndRecognition),
            patch("interviews.speech.socket.FlashCompletionClassifier", return_value=classifier),
        ):
            started = time.monotonic()
            comm = await self.capture()
            ready = await read_json(comm)
            self.assertEqual(ready["type"], "answer_completion")
            self.assertGreaterEqual(time.monotonic() - started, 3)
            await send_json(comm, {"type": "stop"})
            final = await read_json(comm)
            verify_completion_receipt(final["completion_receipt"], None, "q1", final["text"])
            await comm.receive_output()
            await comm.wait()
        classifier.classify.assert_awaited_once()
        classifier.close.assert_awaited_once()

    async def test_paraphrase_is_sent_without_keyword_filtering(self):
        """Verify a natural end expression reaches the LLM without matching any phrase list.
        Only ASR/inference are mocked; observer timing and receipt signing remain real.
        """
        text = "That covers everything I wanted to share on this question."
        classifier = SimpleNamespace(classify=AsyncMock(return_value=True), close=AsyncMock())
        with (
            patch.object(EndRecognition, "utterance", text),
            patch("interviews.speech.socket.RecognitionSession", EndRecognition),
            patch("interviews.speech.socket.FlashCompletionClassifier", return_value=classifier),
        ):
            comm = await self.capture()
            self.assertEqual((await read_json(comm))["type"], "answer_completion")
            classifier.classify.assert_awaited_once_with(text)
            await send_json(comm, {"type": "stop"})
            final = await read_json(comm)
            verify_completion_receipt(final["completion_receipt"], None, "q1", text)
            await comm.receive_output()
            await comm.wait()

    async def test_supplement_revokes_before_and_during_flush(self):
        """Verify new draft text cancels the 3s decision, and final flush revisions never receive
        receipts.
        """
        classifier = SimpleNamespace(classify=AsyncMock(return_value=True), close=AsyncMock())
        with (
            patch("interviews.speech.socket.RecognitionSession", EndRecognition),
            patch("interviews.speech.socket.FlashCompletionClassifier", return_value=classifier),
        ):
            comm = await self.capture()
            await asyncio.sleep(0.1)
            await comm.send_input({"type": "websocket.receive", "bytes": b"\0\0" * 1600})
            self.assertIn("Another detail", (await read_json(comm))["text"])
            self.assertTrue(await comm.receive_nothing(timeout=3.1))
            await send_json(comm, {"type": "stop"})
            await read_json(comm)
            final = await read_json(comm)
            self.assertNotIn("completion_receipt", final)
            await comm.receive_output()
            await comm.wait()
        classifier = SimpleNamespace(classify=AsyncMock(return_value=True), close=AsyncMock())
        with (
            patch.object(EndRecognition, "late_text", True),
            patch("interviews.speech.socket.RecognitionSession", EndRecognition),
            patch("interviews.speech.socket.FlashCompletionClassifier", return_value=classifier),
        ):
            comm = await self.capture()
            self.assertEqual((await read_json(comm))["type"], "answer_completion")
            await send_json(comm, {"type": "stop"})
            await read_json(comm)
            final = await read_json(comm)
            self.assertNotIn("completion_receipt", final)
            self.assertIn("Another detail", final["text"])
            await comm.receive_output()
            await comm.wait()

    async def test_voice_without_transcript_revokes_decision(self):
        """Verify >=50ms audible PCM cancels a decision even when ASR has not produced new
        text.
        """
        classifier = SimpleNamespace(classify=AsyncMock(return_value=True), close=AsyncMock())
        with (
            patch.object(EndRecognition, "draft_on_feed", False),
            patch("interviews.speech.socket.RecognitionSession", EndRecognition),
            patch("interviews.speech.socket.FlashCompletionClassifier", return_value=classifier),
        ):
            comm = await self.capture()
            await asyncio.sleep(0.1)
            await comm.send_input({"type": "websocket.receive", "bytes": b"\x00\x10" * 1600})
            self.assertTrue(await comm.receive_nothing(timeout=3.1))
            await send_json(comm, {"type": "stop"})
            self.assertNotIn("completion_receipt", await read_json(comm))
            await comm.receive_output()
            await comm.wait()

    async def test_classifier_failure_is_explicit(self):
        """Verify classification timeout returns its own failure code and closes without
        success/fallback.
        """
        classifier = SimpleNamespace(
            classify=AsyncMock(side_effect=TimeoutError), close=AsyncMock()
        )
        with (
            patch("interviews.speech.socket.RecognitionSession", EndRecognition),
            patch("interviews.speech.socket.FlashCompletionClassifier", return_value=classifier),
        ):
            comm = await self.capture()
            self.assertEqual((await read_json(comm))["code"], "completion_detection_failed")
            self.assertEqual((await comm.receive_output())["code"], 1011)
            await comm.wait()


class CompletionReceiptTests(SimpleTestCase):
    """Functionality: receipt verification tests. Logic: real timestamp signing, no
    provider calls.
    """

    def test_receipt_binding_expiry_and_tampering(self):
        """Verify owner/question/text tampering and 60s expiry; trimming follows existing Answer
        contract.
        """
        now = time.time()
        receipt = issue_completion_receipt(7, "q1", "Answer. That's all.")
        verify_completion_receipt(receipt, 7, "q1", " Answer. That's all. ")
        for owner, question, text in [
            (8, "q1", "Answer. That's all."),
            (7, "q2", "Answer. That's all."),
            (7, "q1", "Changed text"),
        ]:
            with self.assertRaises(ValueError):
                verify_completion_receipt(receipt, owner, question, text)
        with self.assertRaises(ValueError):
            verify_completion_receipt(receipt + "bad", 7, "q1", "Answer. That's all.")
        with (
            patch("django.core.signing.time.time", return_value=now + 62),
            self.assertRaises(ValueError),
        ):
            verify_completion_receipt(receipt, 7, "q1", "Answer. That's all.")


class CompletionMCPTests(SafetyTestMixin, TransactionTestCase):
    """Functionality: real MCP-to-Agent integration. Logic: original safety and persistence with
    doubles.
    Constraints: isolated DB and explicit offline model output; no claimed real security/model
    efficacy.
    """

    async def test_rejected_next_target_returns_question_instead_of_finishing(self):
        """A failed target must not short-circuit the MCP interview lifecycle."""
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = ApplicationCommunicator(agent_socket, socket_scope("/ws/agent/"))
            await comm.send_input({"type": "websocket.connect"})
            await comm.receive_output()
            await read_json(comm)
            await initialize_mcp(comm)
            await send_json(
                comm,
                {
                    "type": "start",
                    "request_id": str(uuid4()),
                    "resume_text": RESUME,
                    "max_questions": 3,
                },
            )
            await read_json(comm)
            first = (await read_json(comm))["question"]
            identifier = str(uuid4())
            with patch.object(
                InterviewAgentService,
                "_generate_question",
                new=AsyncMock(return_value=(None, "UNSAFE_INTENT_FALLBACK_BLOCKED")),
            ) as rejected:
                await send_json(
                    comm,
                    {
                        "jsonrpc": "2.0",
                        "id": identifier,
                        "method": "tools/call",
                        "params": {
                            "name": "finish_current_answer",
                            "arguments": {
                                "question_id": first["question_id"],
                                "answer_text": ANSWER,
                                "completion_receipt": issue_completion_receipt(
                                    None, first["question_id"], ANSWER
                                ),
                            },
                        },
                    },
                )
                self.assertEqual((await read_json(comm))["operation"], "answer")
                response = await read_json(comm)
                self.assertEqual(response["id"], identifier)
                content = response["result"]["structuredContent"]
                self.assertEqual(content["type"], "question")
                self.assertNotEqual(content["question"]["question_id"], first["question_id"])
                self.assertEqual(rejected.await_count, 1)
            await comm.send_input({"type": "websocket.disconnect", "code": 1000})
            await comm.wait()
        self.assertTrue(llm.closed)

    async def test_tool_advances_once_and_stale_calls_are_rejected(self):
        """Verify MCP call evaluates the complete answer, returns next question, and rejects
        duplicates/stale IDs.
        """
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = ApplicationCommunicator(agent_socket, socket_scope("/ws/agent/"))
            await comm.send_input({"type": "websocket.connect"})
            await comm.receive_output()
            await read_json(comm)
            await initialize_mcp(comm)
            await send_json(
                comm,
                {
                    "type": "start",
                    "request_id": str(uuid4()),
                    "resume_text": RESUME,
                    "max_questions": 3,
                },
            )
            await read_json(comm)
            question = (await read_json(comm))["question"]["question_id"]
            identifier = str(uuid4())
            payload = {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": "tools/call",
                "params": {
                    "name": "finish_current_answer",
                    "arguments": {
                        "question_id": question,
                        "answer_text": ANSWER,
                        "completion_receipt": issue_completion_receipt(None, question, ANSWER),
                    },
                },
            }
            await send_json(comm, payload)
            self.assertEqual((await read_json(comm))["operation"], "answer")
            result = await read_json(comm)
            self.assertEqual(result["id"], identifier)
            self.assertEqual(result["result"]["structuredContent"]["type"], "question")
            self.assertIsNotNone(result["result"]["structuredContent"]["last_evaluation"])
            calls = len(llm.calls)
            await send_json(comm, payload)
            self.assertEqual((await read_json(comm))["error"]["data"]["code"], "duplicate_request")
            payload["id"] = str(uuid4())
            await send_json(comm, payload)
            self.assertEqual((await read_json(comm))["error"]["data"]["code"], "stale_question")
            self.assertEqual(len(llm.calls), calls)
            await comm.send_input({"type": "websocket.disconnect", "code": 1000})
            await comm.wait()
        self.assertTrue(llm.closed)

    def test_invalid_receipt_never_starts_business_model(self):
        """Verify lifecycle/receipt/extra argument rejection in the pure adapter before Agent
        dispatch.
        """
        mcp = InterviewMCP(None)
        mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {}},
            }
        )
        mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        with self.assertRaises(ValueError):
            mcp.handle(
                {
                    "jsonrpc": "2.0",
                    "id": str(uuid4()),
                    "method": "tools/call",
                    "params": {
                        "name": "finish_current_answer",
                        "arguments": {
                            "question_id": "q1",
                            "answer_text": ANSWER,
                            "completion_receipt": "forged",
                        },
                    },
                }
            )
