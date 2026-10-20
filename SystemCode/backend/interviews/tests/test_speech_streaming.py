"""Responsibilities: Verify streaming transport, cancellation and provider delivery contracts.
Implementation: Use controlled thread events, generated PCM and SDK mocks; never call a real provider.
Related Modules: speech.streaming bridges threads; speech.service validates final synthesis;
the existing Django middleware retains account and CSRF enforcement.
Declaration Index:
- SpeechStreamingTests: Verify the async transport independently of network, device and paid synthesis.
- SpeechStreamingTests.test_audio_precedes_completion_and_odd_fragments_are_aligned: Block the worker after initial PCM; audio must arrive before release and end.
- SpeechStreamingTests.test_audio_precedes_completion_and_odd_fragments_are_aligned.provider: Split one PCM sample across callbacks, then await explicit test release.
- SpeechStreamingTests.test_failure_is_terminal_and_does_not_emit_success: A provider error after partial audio must emit one explicit error without retry.
- SpeechStreamingTests.test_failure_is_terminal_and_does_not_emit_success.provider: Deliver a sample before a controlled provider failure.
- SpeechStreamingTests.test_disconnect_releases_backpressure_and_cancels_worker: A full bounded queue must not strand its producer when the consumer disconnects.
- SpeechStreamingTests.test_disconnect_releases_backpressure_and_cancels_worker.provider: Fill beyond queue capacity; cancellation must interrupt the blocked insertion.
- SpeechStreamingTests.test_http_validation_and_stream_headers: Validation must precede provider work; successful responses retain async iteration.
- SpeechStreamingTests.test_authentication_and_csrf_protect_new_route: Exercise real outer middleware checks without opening a database or model connection.
- SpeechStreamingTests.test_adapter_delivers_pcm_before_session_finished_and_preserves_wav: Use the real adapter with an SDK double to prove callback timing and final WAV contract.
- SpeechStreamingTests.test_adapter_delivers_pcm_before_session_finished_and_preserves_wav.finish: Emit one delta, assert immediate delivery, then permit validated completion.
- SpeechStreamingTests.test_precancelled_adapter_never_constructs_provider: Cancellation before synthesis must not initiate a paid connection.
Variable Index:
None
"""

import asyncio
import base64
import json
import os
import threading
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, RequestFactory
from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import CsrfViewMiddleware

from interviews.accounts import AccountRequiredMiddleware
from interviews.speech.service import SpeechError, synthesize
from interviews.speech.streaming import speech_events, tts_stream


class SpeechStreamingTests(SimpleTestCase):
    """Verify the async transport independently of network, device and paid synthesis."""

    async def test_audio_precedes_completion_and_odd_fragments_are_aligned(self):
        """Block the worker after initial PCM; audio must arrive before release and end."""
        release = threading.Event()

        def provider(text, *, on_pcm, cancelled):
            """Split one PCM sample across callbacks, then await explicit test release."""
            on_pcm(b"\x00")
            on_pcm(b"\x01\x02\x03")
            if not release.wait(3):
                raise AssertionError("Test consumer failed to release synthesis")
            on_pcm(b"\x04\x05")
            return b"validated WAV double"

        with patch("interviews.speech.streaming.synthesize", side_effect=provider) as sdk:
            stream = speech_events("Fixed text")
            try:
                start = json.loads(await anext(stream))
                chunk = json.loads(await asyncio.wait_for(anext(stream), 2))
                self.assertEqual(chunk["sequence"], 0)
                self.assertEqual(base64.b64decode(chunk["pcm"]), b"\x00\x01\x02\x03")
                self.assertFalse(release.is_set())
                release.set()
                remaining = [json.loads(event) async for event in stream]
                self.assertEqual([event["type"] for event in remaining], ["audio", "end"])
                self.assertEqual(remaining[-1]["audio_bytes"], 6)
                self.assertEqual(remaining[-1]["utterance_id"], start["utterance_id"])
                sdk.assert_called_once()
            finally:
                release.set()
                await stream.aclose()

    async def test_failure_is_terminal_and_does_not_emit_success(self):
        """A provider error after partial audio must emit one explicit error without retry."""
        release = threading.Event()

        def provider(text, *, on_pcm, cancelled):
            """Deliver a sample before a controlled provider failure."""
            on_pcm(b"\x00\x00")
            release.wait(3)
            raise SpeechError("quota_exhausted", "Synthesis quota exhausted.")

        with patch("interviews.speech.streaming.synthesize", side_effect=provider) as sdk:
            stream = speech_events("Fixed text")
            try:
                await anext(stream)
                self.assertEqual(json.loads(await anext(stream))["type"], "audio")
                release.set()
                records = [json.loads(event) async for event in stream]
                self.assertEqual([event["type"] for event in records], ["error"])
                self.assertEqual(records[0]["error"]["code"], "quota_exhausted")
                sdk.assert_called_once()
            finally:
                release.set()
                await stream.aclose()

    async def test_disconnect_releases_backpressure_and_cancels_worker(self):
        """A full bounded queue must not strand its producer when the consumer disconnects."""
        stopped = threading.Event()
        attempts = []

        def provider(text, *, on_pcm, cancelled):
            """Fill beyond queue capacity; cancellation must interrupt the blocked insertion."""
            try:
                for index in range(100):
                    attempts.append(index)
                    on_pcm(b"\x00\x00")
            finally:
                stopped.set()

        with patch("interviews.speech.streaming.synthesize", side_effect=provider):
            stream = speech_events("Fixed text")
            await anext(stream)
            for _ in range(100):
                if len(attempts) >= 33:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(len(attempts), 33)
            await stream.aclose()
            self.assertTrue(await asyncio.to_thread(stopped.wait, 2))
            self.assertEqual(len(attempts), 33)

    async def test_http_validation_and_stream_headers(self):
        """Validation must precede provider work; successful responses retain async iteration."""
        factory = RequestFactory()
        with patch("interviews.speech.streaming.SpeechConfig.load"), patch(
            "interviews.speech.streaming.synthesize"
        ) as sdk:
            for body in ["[]", "null", '{"text":""}', '{"text":1}', "invalid"]:
                response = await tts_stream(factory.post("/", data=body, content_type="application/json"))
                self.assertEqual(response.status_code, 400)
            response = await tts_stream(factory.get("/"))
            self.assertEqual(response.status_code, 405)
            response = await tts_stream(factory.post("/", data='{"text":"Test"}', content_type="application/json"))
            self.assertTrue(response.is_async)
            self.assertEqual(response["X-Accel-Buffering"], "no")
            self.assertEqual(response["Cache-Control"], "no-store")
            sdk.assert_not_called()
            await response.streaming_content.aclose()

    def test_authentication_and_csrf_protect_new_route(self):
        """Exercise real outer middleware checks without opening a database or model connection."""
        request = RequestFactory().post("/api/speech/tts/stream/", data='{"text":"Test"}',
                                        content_type="application/json")
        request.user = AnonymousUser()
        with self.settings(INTERVIEW_REQUIRE_LOGIN=True):
            response = AccountRequiredMiddleware(Mock())(request)
        self.assertEqual(response.status_code, 401)
        csrf = CsrfViewMiddleware(Mock())
        self.assertEqual(csrf.process_view(request, tts_stream, (), {}).status_code, 403)

    def test_adapter_delivers_pcm_before_session_finished_and_preserves_wav(self):
        """Use the real adapter with an SDK double to prove callback timing and final WAV contract."""
        config = {"SPEECH_ENABLED": "true", "DASHSCOPE_API_KEY": "offline-test-key",
                  "SPEECH_REGION": "beijing", "SPEECH_TTS_MODEL": "qwen3-tts-flash-realtime",
                  "SPEECH_TTS_VOICE": "Cherry"}
        delivered = []
        sdk = Mock()

        def finish():
            """Emit one delta, assert immediate delivery, then permit validated completion."""
            callback = sdk.call_args.kwargs["callback"]
            callback.on_event({"type": "response.audio.delta",
                               "delta": base64.b64encode(b"\x00\x01" * 120).decode()})
            self.assertEqual(delivered, [b"\x00\x01" * 120])
            callback.on_event({"type": "session.finished"})

        with patch.dict(os.environ, config), patch("dashscope.audio.qwen_tts_realtime.QwenTtsRealtime", sdk):
            sdk.return_value.finish.side_effect = finish
            wav = synthesize("Fixed text", on_pcm=delivered.append, cancelled=threading.Event())
            self.assertEqual(wav[:4], b"RIFF")
            sdk.return_value.close.assert_called_once()

    def test_precancelled_adapter_never_constructs_provider(self):
        """Cancellation before synthesis must not initiate a paid connection."""
        cancelled = threading.Event()
        cancelled.set()
        with patch("dashscope.audio.qwen_tts_realtime.QwenTtsRealtime") as sdk:
            with self.assertRaises(SpeechError) as caught:
                synthesize("Fixed text", cancelled=cancelled)
            self.assertEqual(caught.exception.code, "speech_cancelled")
            sdk.assert_not_called()
