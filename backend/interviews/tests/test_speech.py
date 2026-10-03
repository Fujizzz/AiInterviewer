"""Responsibilities: Verify speech configuration, adapter behavior, WebSocket protocols, HTTP
routes, and account protections.

Implementation: Exercise production speech code with SDK doubles and generated audio fixtures;
provider doubles do not call billable services.
Related Modules: interviews.speech.service, interviews.speech.socket, and Django ASGI/HTTP test
utilities.

Declaration Index:
- SpeechServiceTests:
  Validate region selection, raw audio format, quota errors and cache retention.
- SpeechServiceTests.test_disabled_and_missing_credentials_never_construct_provider:
  Opt-in and complete credentials must precede any SDK construction.
- SpeechServiceTests.test_singapore_wav_and_sanitised_quota_failure:
  The real adapter requests mono PCM and wraps it in a correctly labelled WAV.
- SpeechServiceTests.test_public_endpoints_and_incompatible_models:
  Singapore defaults require no workspace; unsupported protocols fail before SDK use.
- SpeechServiceTests.test_regional_endpoints_preserve_llm_configuration:
  Check both public/workspace regions and shared-key use without changing LLM HTTP settings.
- SpeechServiceTests.test_invalid_region_precedes_provider_construction:
  Reject blank/unknown regions before any provider call, without echoing configuration.
- SpeechServiceTests.test_beijing_synthesis_uses_existing_key:
  Verify Beijing URL, original key and unchanged audio/options through an offline SDK double.
- SpeechServiceTests.test_quota_error_survives_connection_close:
  Preserve a server quota error when sending input races with its disconnect.
- SpeechServiceTests.test_quota_error_survives_connection_close.reject:
  Emit a quota failure before simulating a failed client send.
- SpeechServiceTests.test_qwen_incomplete_invalid_oversized_and_timeout:
  Reject truncated streams, malformed PCM, excessive output and missing completion.
- SpeechServiceTests.test_qwen_incomplete_invalid_oversized_and_timeout.deliver:
  Deliver chosen provider events on finish without a network connection.
- SpeechServiceTests.test_cache_evicts_by_bytes_count_and_ttl:
  Audio capabilities expire and cannot grow memory without a fixed bound.
- SpeechServiceTests.test_recognition_adapter_english_format_and_callback:
  Verify the real SDK boundary without creating a remote task.
- SpeechHTTPTests:
  Exercise actual Django routes and loopback access with generated fixture bytes.
- SpeechHTTPTests.test_tts_url_audio_and_unknown_capability:
  A synthesis response resolves through the public UUID route and is not cached.
- SpeechHTTPTests.test_invalid_text_disabled_voice_and_static_allowlist:
  Bad text never reaches the provider and disabled service offers explicit errors.
- SpeechAccountTests:
  Ensure the integrated speech route retains account authentication and CSRF protection.
- SpeechAccountTests.test_login_and_csrf_precede_tts:
  Reject anonymous and tokenless writes before calling the offline synthesis provider.
- FixtureRecognition:
  Explicit offline provider used only by protocol tests.
- FixtureRecognition.__init__:
  Store the callback and the PCM received by this isolated session.
- FixtureRecognition.start:
  Allow startup without a network service.
- FixtureRecognition.feed:
  Produce one evolving sentence and mark it final without ending the answer.
- FixtureRecognition.stop:
  Append a final sentence and emit completion only after explicit stop.
- SpeechSocketTests:
  Validate answer boundaries, sentence accumulation and malformed audio rejection.
- SpeechSocketTests.connect:
  Create an in-process ASGI connection with the production loopback policy.
- SpeechSocketTests.read:
  Read one JSON event with a bounded wait.
- SpeechSocketTests.test_partial_text_waits_for_stop_and_final_joins_sentences:
  Sentence-final events remain drafts until explicit answer completion.
- SpeechSocketTests.test_odd_pcm_and_cross_origin_fail_before_recognition:
  PCM byte alignment and browser origins are checked on the server.
- SpeechSocketTests.test_disconnect_stops_provider:
  Closing the page releases the recognition task rather than keeping its stream open.
- SpeechSocketTests.test_production_stt_rejects_anonymous_before_provider:
  Exercise the complete ASGI wrapper and reject anonymous speech without starting ASR.

Variable Index:
None
"""

import asyncio
import base64
import io
import json
import os
import wave
from unittest.mock import Mock, patch

from asgiref.testing import ApplicationCommunicator
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings

from interviews.speech.service import (
    AudioStore,
    RecognitionSession,
    SpeechConfig,
    SpeechError,
    configure_sdk,
    synthesize,
)
from interviews.speech.socket import stt_socket


class SpeechServiceTests(SimpleTestCase):
    """Validate region selection, raw audio format, quota errors and cache retention.
    """

    def test_disabled_and_missing_credentials_never_construct_provider(self):
        """Opt-in and complete credentials must precede any SDK construction.
        """
        with (
            patch.dict(os.environ, {"SPEECH_ENABLED": "false"}),
            patch("dashscope.audio.qwen_tts_realtime.QwenTtsRealtime") as sdk,
        ):
            with self.assertRaises(SpeechError) as error:
                synthesize("Describe your contribution.")
            self.assertEqual(error.exception.code, "speech_disabled")
            sdk.assert_not_called()
        with patch.dict(os.environ, {"SPEECH_ENABLED": "true", "DASHSCOPE_API_KEY": ""}):
            with self.assertRaises(SpeechError):
                SpeechConfig.load()

    def test_singapore_wav_and_sanitised_quota_failure(self):
        """The real adapter requests mono PCM and wraps it in a correctly labelled WAV.
        """
        settings = {
            "SPEECH_ENABLED": "true",
            "DASHSCOPE_API_KEY": "test-key",
            "DASHSCOPE_SPEECH_WORKSPACE_ID": "test-space",
            "SPEECH_REGION": "singapore",
        }
        sdk = Mock()
        events = [
            {"type": "response.audio.delta", "delta": base64.b64encode(b"\x00\x01" * 100).decode()},
            {"type": "response.done", "response": {"status": "completed"}},
            {"type": "response.audio.delta", "delta": base64.b64encode(b"\x00\x01" * 140).decode()},
            {"type": "session.finished"},
        ]
        with (
            patch.dict(os.environ, settings),
            patch("dashscope.audio.qwen_tts_realtime.QwenTtsRealtime", sdk),
        ):
            sdk.return_value.finish.side_effect = lambda: [
                sdk.call_args.kwargs["callback"].on_event(event) for event in events
            ]
            wav = synthesize("Could you describe your main contribution?")
            with wave.open(io.BytesIO(wav)) as audio:
                self.assertEqual(
                    (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()),
                    (1, 24000, 2),
                )
                self.assertEqual(audio.getnframes(), 240)
            self.assertIn("test-space.ap-southeast-1", SpeechConfig.load().endpoint)
            self.assertEqual(sdk.call_args.kwargs["model"], "qwen3-tts-flash-realtime")
            self.assertEqual(
                sdk.call_args.kwargs["url"],
                "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime",
            )
            self.assertEqual(
                sdk.return_value.update_session.call_args.kwargs["language_type"], "English"
            )
            sdk.return_value.append_text.assert_called_once_with(
                "Could you describe your main contribution?"
            )
            sdk.return_value.close.assert_called_once()
            events[:] = [
                {"type": "error", "error": {"code": "AllocationQuota.FreeTierOnly SECRET"}}
            ]
            with self.assertRaises(SpeechError) as error:
                synthesize("Next question")
            self.assertEqual(error.exception.code, "quota_exhausted")
            self.assertNotIn("SECRET", str(error.exception))

    def test_public_endpoints_and_incompatible_models(self):
        """Singapore defaults require no workspace; unsupported protocols fail before SDK use.
        """
        settings = {"SPEECH_ENABLED": "true", "DASHSCOPE_API_KEY": "test-key"}
        with patch.dict(os.environ, settings, clear=True):
            self.assertEqual(
                SpeechConfig.load().endpoint,
                "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference",
            )
            self.assertEqual(SpeechConfig.load().stt_model, "qwen-audio-3.1-asr-flash-streaming")
            with patch.dict(os.environ, {"SPEECH_TTS_MODEL": "qwen3-tts-flash-2025-09-18"}):
                with self.assertRaises(SpeechError) as error:
                    SpeechConfig.load()
                self.assertEqual(error.exception.code, "unsupported_speech_model")
            with patch.dict(os.environ, {"DASHSCOPE_SPEECH_WORKSPACE_ID": "invalid/path"}):
                with self.assertRaises(SpeechError):
                    SpeechConfig.load()

    def test_regional_endpoints_preserve_llm_configuration(self):
        """Verify public/workspace routes and SDK isolation with fake shared credentials.

        Explicit expected regional domains cover both ASR and TTS. SDK globals are
        restored after each subtest; the HTTP endpoint and environment must not change.
        No remote connection or model request is constructed.
        """
        import dashscope

        cases = [
            ("singapore", "dashscope-intl.aliyuncs.com", "ap-southeast-1"),
            ("beijing", "dashscope.aliyuncs.com", "cn-beijing"),
        ]
        for region, host, workspace_region in cases:
            for workspace in ("", "test-space"):
                with (
                    self.subTest(region=region, workspace=workspace),
                    patch.dict(
                        os.environ,
                        {
                            "SPEECH_ENABLED": "true",
                            "SPEECH_REGION": region,
                            "DASHSCOPE_API_KEY": "shared-test-key",
                            "DASHSCOPE_SPEECH_WORKSPACE_ID": workspace,
                            "DASHSCOPE_BASE_URL": "https://llm.invalid/compatible-mode/v1",
                        },
                        clear=True,
                    ),
                    patch.object(dashscope, "api_key", "prior-key"),
                    patch.object(dashscope, "base_websocket_api_url", "prior-url"),
                    patch.object(dashscope, "base_http_api_url", "https://llm.invalid/api/v1"),
                ):
                    before = dict(os.environ)
                    config = SpeechConfig.load()
                    self.assertEqual(config.api_key, "shared-test-key")
                    self.assertEqual(config.tts_endpoint, f"wss://{host}/api-ws/v1/realtime")
                    expected_host = (
                        f"test-space.{workspace_region}.maas.aliyuncs.com" if workspace else host
                    )
                    self.assertEqual(config.endpoint, f"wss://{expected_host}/api-ws/v1/inference")
                    configure_sdk(config)
                    self.assertEqual(dashscope.api_key, "shared-test-key")
                    self.assertEqual(dashscope.base_websocket_api_url, config.endpoint)
                    self.assertEqual(dashscope.base_http_api_url, "https://llm.invalid/api/v1")
                    self.assertEqual(dict(os.environ), before)

    def test_invalid_region_precedes_provider_construction(self):
        """Reject empty/unknown geography using fake settings before SDK construction.

        The same visible configuration error is used without automatic region
        selection; logs and public errors must not echo the untrusted region string.
        """
        for region in ("", "SECRET-unsupported-region"):
            with (
                self.subTest(region=region),
                patch.dict(
                    os.environ,
                    {
                        "SPEECH_ENABLED": "true",
                        "SPEECH_REGION": region,
                        "DASHSCOPE_API_KEY": "test-key",
                    },
                    clear=True,
                ),
                patch("dashscope.audio.qwen_tts_realtime.QwenTtsRealtime") as sdk,
                self.assertLogs("interviews.speech.service", level="WARNING") as logs,
            ):
                with self.assertRaises(SpeechError) as error:
                    synthesize("Describe your contribution.")
                self.assertEqual(error.exception.code, "speech_not_configured")
                self.assertNotIn("SECRET", str(error.exception) + " ".join(logs.output))
                sdk.assert_not_called()

    def test_beijing_synthesis_uses_existing_key(self):
        """Exercise Beijing synthesis through an explicit offline SDK double.

        Generated PCM must retain 24 kHz mono WAV wrapping, the original model/voice
        defaults and English language; the shared key is passed through SDK configuration.
        Mock success verifies adapter behavior only, not actual provider access.
        """
        import dashscope

        events = [
            {"type": "response.audio.delta", "delta": base64.b64encode(b"\x00\x01" * 240).decode()},
            {"type": "session.finished"},
        ]
        with (
            patch.dict(
                os.environ,
                {
                    "SPEECH_ENABLED": "true",
                    "SPEECH_REGION": "beijing",
                    "DASHSCOPE_API_KEY": "existing-test-key",
                },
                clear=True,
            ),
            patch.object(dashscope, "api_key", "prior-key"),
            patch.object(dashscope, "base_websocket_api_url", "prior-url"),
            patch("dashscope.audio.qwen_tts_realtime.QwenTtsRealtime") as sdk,
        ):
            sdk.return_value.finish.side_effect = lambda: [
                sdk.call_args.kwargs["callback"].on_event(event) for event in events
            ]
            wav = synthesize("Please introduce yourself briefly.")
            self.assertEqual(dashscope.api_key, "existing-test-key")
            self.assertEqual(
                sdk.call_args.kwargs["url"], "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
            )
            self.assertEqual(sdk.call_args.kwargs["model"], "qwen3-tts-flash-realtime")
            options = sdk.return_value.update_session.call_args.kwargs
            self.assertEqual(options["voice"], "Cherry")
            self.assertEqual(options["language_type"], "English")
            with wave.open(io.BytesIO(wav)) as audio:
                self.assertEqual((audio.getframerate(), audio.getnchannels()), (24000, 1))
            sdk.return_value.close.assert_called_once()

    def test_quota_error_survives_connection_close(self):
        """Preserve a server quota error when sending input races with its disconnect.
        """
        settings = {"SPEECH_ENABLED": "true", "DASHSCOPE_API_KEY": "test-key"}
        with (
            patch.dict(os.environ, settings, clear=True),
            patch("dashscope.audio.qwen_tts_realtime.QwenTtsRealtime") as sdk,
        ):

            def reject(**kwargs):
                """Emit a quota failure before simulating a failed client send.
                """
                sdk.call_args.kwargs["callback"].on_event(
                    {"type": "error", "error": {"code": "AllocationQuota.FreeTierOnly"}}
                )
                raise ConnectionError("SECRET raw connection error")

            sdk.return_value.update_session.side_effect = reject
            with self.assertRaises(SpeechError) as error:
                synthesize("Question")
            self.assertEqual(error.exception.code, "quota_exhausted")
            self.assertNotIn("SECRET", str(error.exception))
            sdk.return_value.close.assert_called_once()

    def test_qwen_incomplete_invalid_oversized_and_timeout(self):
        """Reject truncated streams, malformed PCM, excessive output and missing completion.
        """
        cases = [
            ("early_close", [], "speech_connection_closed"),
            (
                "invalid",
                [{"type": "response.audio.delta", "delta": "not-base64"}],
                "invalid_speech_audio",
            ),
            (
                "oversized",
                [{"type": "response.audio.delta", "delta": base64.b64encode(b"x" * 10).decode()}],
                "invalid_speech_audio",
            ),
            (
                "timeout",
                [{"type": "response.done", "response": {"status": "completed"}}],
                "speech_timeout",
            ),
        ]
        settings = {"SPEECH_ENABLED": "true", "DASHSCOPE_API_KEY": "test-key"}
        for label, events, expected in cases:
            with (
                self.subTest(label=label),
                patch.dict(os.environ, settings, clear=True),
                patch("interviews.speech.service.MAX_AUDIO_BYTES", 8),
                patch("interviews.speech.service.TTS_TIMEOUT_SECONDS", 0.01),
                patch("dashscope.audio.qwen_tts_realtime.QwenTtsRealtime") as sdk,
            ):

                def deliver(current_events=events, current_label=label, current_sdk=sdk):
                    """Deliver chosen provider events on finish without a network connection.
                    """
                    callback = current_sdk.call_args.kwargs["callback"]
                    for event in current_events:
                        callback.on_event(event)
                    if current_label == "early_close":
                        callback.on_close(1006, "SECRET")

                sdk.return_value.finish.side_effect = deliver
                with self.assertRaises(SpeechError) as error:
                    synthesize("Question")
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn("SECRET", str(error.exception))
                sdk.return_value.close.assert_called_once()

    def test_cache_evicts_by_bytes_count_and_ttl(self):
        """Audio capabilities expire and cannot grow memory without a fixed bound.
        """
        clock = Mock(return_value=10)
        store = AudioStore(ttl=5, max_bytes=6, max_items=2, clock=clock)
        first = store.put(b"1111")
        second = store.put(b"2222")
        self.assertIsNone(store.get(first))
        self.assertEqual(store.get(second), b"2222")
        clock.return_value = 15
        self.assertIsNone(store.get(second))
        with self.assertRaises(SpeechError):
            store.put(b"oversized")

    def test_recognition_adapter_english_format_and_callback(self):
        """Verify the real SDK boundary without creating a remote task.
        """
        settings = {
            "SPEECH_ENABLED": "true",
            "DASHSCOPE_API_KEY": "test-key",
            "DASHSCOPE_SPEECH_WORKSPACE_ID": "test-space",
            "SPEECH_REGION": "singapore",
        }
        emit = Mock()
        with patch.dict(os.environ, settings), patch("dashscope.audio.asr.Recognition") as sdk:
            session = RecognitionSession(emit)
            options = sdk.call_args.kwargs
            self.assertEqual(
                (options["format"], options["sample_rate"], options["language_hints"]),
                ("pcm", 16000, ["en"]),
            )
            self.assertEqual(options["request_timeout"], 140)
            result = Mock()
            result.get_sentence.return_value = {
                "text": "My project",
                "begin_time": 0,
                "end_time": 100,
            }
            options["callback"].on_event(result)
            self.assertEqual(
                emit.call_args.args[0],
                {"type": "sentence", "text": "My project", "segment": "0", "is_final": True},
            )
            session.start()
            session.feed(b"\x00\x00")
            session.stop()
            session.stop()
            sdk.return_value.send_audio_frame.assert_called_once_with(b"\x00\x00")
            sdk.return_value.stop.assert_called_once()


class SpeechHTTPTests(SimpleTestCase):
    """Exercise actual Django routes and loopback access with generated fixture bytes.
    """

    def test_tts_url_audio_and_unknown_capability(self):
        """A synthesis response resolves through the public UUID route and is not cached.
        """
        wav = b"fixture WAV bytes"
        with patch("interviews.speech.views.synthesize", return_value=wav):
            response = self.client.post(
                "/api/speech/tts/",
                {"text": "A question?"},
                content_type="application/json",
                HTTP_HOST="127.0.0.1:8765",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["audio_url"].startswith("http://127.0.0.1:8765/api/speech/audio/"))
        audio = self.client.get(
            f"/api/speech/audio/{data['utterance_id']}/", HTTP_HOST="127.0.0.1:8765"
        )
        self.assertEqual(audio.content, wav)
        self.assertEqual(audio["Cache-Control"], "no-store")
        self.assertEqual(
            self.client.get("/api/speech/audio/00000000-0000-0000-0000-000000000000/").status_code,
            404,
        )

    def test_invalid_text_disabled_voice_and_static_allowlist(self):
        """Bad text never reaches the provider and disabled service offers explicit errors.
        """
        with patch("interviews.speech.views.synthesize") as provider:
            for text in ("", " " * 10, "a" * 1201, 123):
                response = self.client.post(
                    "/api/speech/tts/", {"text": text}, content_type="application/json"
                )
                self.assertEqual(response.status_code, 400)
            provider.assert_not_called()
        with patch.dict(os.environ, {"SPEECH_ENABLED": "false"}):
            response = self.client.post(
                "/api/speech/tts/", {"text": "Question"}, content_type="application/json"
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "speech_disabled")
        self.assertEqual(self.client.get("/stream-demo/speech-worklet.js").status_code, 200)
        self.assertEqual(self.client.get("/stream-demo/.env").status_code, 404)


@override_settings(INTERVIEW_REQUIRE_LOGIN=True)
class SpeechAccountTests(TestCase):
    """Verify authenticated speech using an isolated test database and a local provider double.
    """

    def test_login_and_csrf_precede_tts(self):
        """A logged-in page supplies CSRF; missing login or token never calls synthesis.
        """
        client = Client(enforce_csrf_checks=True)
        with patch("interviews.speech.views.synthesize", return_value=b"fixture WAV") as provider:
            response = client.post(
                "/api/speech/tts/",
                {"text": "Describe one contribution."},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)
            user = get_user_model().objects.create_user(
                "speech-fixture", password="local-test-only"
            )
            client.force_login(user)
            response = client.post(
                "/api/speech/tts/",
                {"text": "Describe one contribution."},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 403)
            provider.assert_not_called()
            page = client.get("/agent/")
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, 'id="start-recording"')
            response = client.post(
                "/api/speech/tts/",
                {"text": "Describe one contribution."},
                content_type="application/json",
                HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
            )
            self.assertEqual(response.status_code, 200)
            provider.assert_called_once_with("Describe one contribution.")


class FixtureRecognition:
    """Explicit offline provider used only by protocol tests.
    """

    def __init__(self, emit):
        """Store the callback and the PCM received by this isolated session.
        """
        self.emit, self.frames = emit, []

    def start(self):
        """Allow startup without a network service.
        """

    def feed(self, pcm):
        """Produce one evolving sentence and mark it final without ending the answer.
        """
        self.frames.append(pcm)
        self.emit({"type": "sentence", "segment": "1", "text": "I built", "is_final": False})
        self.emit(
            {"type": "sentence", "segment": "1", "text": "I built a parser.", "is_final": True}
        )

    def stop(self):
        """Append a final sentence and emit completion only after explicit stop.
        """
        self.emit({"type": "sentence", "segment": "2", "text": "I tested it.", "is_final": True})
        self.emit({"type": "complete"})


class SpeechSocketTests(SimpleTestCase):
    """Validate answer boundaries, sentence accumulation and malformed audio rejection.
    """

    async def connect(self, origin="http://localhost"):
        """Create an in-process ASGI connection with the production loopback policy.
        """
        comm = ApplicationCommunicator(
            stt_socket,
            {
                "type": "websocket",
                "scheme": "ws",
                "client": ("127.0.0.1", 123),
                "headers": [(b"host", b"localhost"), (b"origin", origin.encode())],
            },
        )
        await comm.send_input({"type": "websocket.connect"})
        return comm

    async def read(self, comm):
        """Read one JSON event with a bounded wait.
        """
        return json.loads((await comm.receive_output(timeout=3))["text"])

    async def test_partial_text_waits_for_stop_and_final_joins_sentences(self):
        """Sentence-final events remain drafts until explicit answer completion.
        """
        with patch("interviews.speech.socket.RecognitionSession", FixtureRecognition):
            comm = await self.connect()
            self.assertEqual((await comm.receive_output())["type"], "websocket.accept")
            self.assertEqual((await self.read(comm))["sample_rate"], 16000)
            await comm.send_input({"type": "websocket.receive", "text": '{"type":"start"}'})
            self.assertEqual((await self.read(comm))["type"], "started")
            await comm.send_input({"type": "websocket.receive", "bytes": b"\x00\x00" * 1600})
            self.assertEqual((await self.read(comm))["text"], "I built")
            self.assertEqual((await self.read(comm))["text"], "I built a parser.")
            self.assertTrue(await comm.receive_nothing(timeout=0.05))
            await comm.send_input({"type": "websocket.receive", "text": '{"type":"stop"}'})
            await self.read(comm)
            final = await self.read(comm)
            self.assertEqual(final["type"], "final")
            self.assertEqual(final["text"], "I built a parser. I tested it.")
            self.assertGreaterEqual(final["finalization_ms"], 0)
            self.assertEqual((await comm.receive_output())["code"], 1000)
            await comm.wait(timeout=3)

    async def test_odd_pcm_and_cross_origin_fail_before_recognition(self):
        """PCM byte alignment and browser origins are checked on the server.
        """
        with patch("interviews.speech.socket.RecognitionSession", FixtureRecognition):
            comm = await self.connect()
            await comm.receive_output()
            await self.read(comm)
            await comm.send_input({"type": "websocket.receive", "text": '{"type":"start"}'})
            await self.read(comm)
            await comm.send_input({"type": "websocket.receive", "bytes": b"x"})
            self.assertEqual((await self.read(comm))["code"], "invalid_pcm")
            await comm.receive_output()
            await comm.wait(timeout=3)
        with patch("interviews.speech.socket.RecognitionSession") as provider:
            comm = await self.connect("https://unrelated.example")
            self.assertEqual((await comm.receive_output())["code"], 1008)
            await comm.wait()
            provider.assert_not_called()

    async def test_disconnect_stops_provider(self):
        """Closing the page releases the recognition task rather than keeping its stream open.
        """
        session = Mock()
        with patch("interviews.speech.socket.RecognitionSession", return_value=session):
            comm = await self.connect()
            await comm.receive_output()
            await self.read(comm)
            await comm.send_input({"type": "websocket.receive", "text": '{"type":"start"}'})
            await self.read(comm)
            await comm.send_input({"type": "websocket.disconnect"})
            await asyncio.wait_for(comm.wait(), 3)
            session.stop.assert_called_once()

    async def test_production_stt_rejects_anonymous_before_provider(self):
        """Production session validation runs before the new speech WebSocket handler.
        """
        from config.asgi import application

        with (
            override_settings(INTERVIEW_REQUIRE_LOGIN=True),
            patch("interviews.speech.socket.RecognitionSession") as provider,
        ):
            comm = ApplicationCommunicator(
                application,
                {
                    "type": "websocket",
                    "path": "/ws/speech/stt/",
                    "scheme": "ws",
                    "client": ("127.0.0.1", 123),
                    "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")],
                },
            )
            await comm.send_input({"type": "websocket.connect"})
            self.assertEqual((await comm.receive_output())["code"], 1008)
            await comm.wait(timeout=3)
            provider.assert_not_called()
