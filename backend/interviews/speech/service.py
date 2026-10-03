"""Bailian regional adapters and bounded, temporary in-memory WAV storage.

Resolve explicit speech geography independently of the LLM HTTP endpoint, reusing
the existing key. Missing SPEECH_REGION preserves Singapore; invalid values fail
before SDK construction. Model, voice, audio and timeout defaults remain unchanged.

目录：
- SpeechError：
  Carry a safe error code and public explanation, never provider credentials.
- SpeechError.__init__：
  Save the public error contract.
- SpeechConfig：
  Shared credentials and region-selected inference/realtime model configuration.
- SpeechConfig.load：
  Validate settings before any paid or network operation can start.
- provider_error：
  Translate provider failures without reflecting raw exception messages.
- configure_sdk：
  Set the speech endpoint independently of the existing OpenAI-compatible LLM URL.
- synthesize：
  Collect Qwen realtime PCM into one bounded WAV compatible with the UE bridge.
- synthesize.Callback：
  Bound streamed audio and distinguish completion, provider failure and disconnect.
- synthesize.Callback.__init__：
  Create thread-safe collection state for one synthesis request.
- synthesize.Callback.fail：
  Preserve the first sanitised error and release the waiting request.
- synthesize.Callback.on_event：
  Decode bounded PCM deltas and accept only the final session completion boundary.
- synthesize.Callback.on_close：
  Reject connections closed before successful session completion.
- AudioStore：
  Thread-safe WAV cache with TTL, entry and byte limits; no disk writes.
- AudioStore.__init__：
  Construct an isolated bounded cache.
- AudioStore._expire：
  Remove expired entries; called only under the cache lock.
- AudioStore.put：
  Return a random capability ID, evicting oldest audio when capacity is reached.
- AudioStore.get：
  Retrieve unexpired WAV bytes, otherwise report an absent capability.
- RecognitionSession：
  Wrap one SDK recognition task; callbacks hand data to the ASGI event loop.
- RecognitionSession.__init__：
  Validate credentials and construct an English PCM recognition task.
- RecognitionSession.__init__.Callback：
  Forward provider events without sending audio or secrets to logs.
- RecognitionSession.__init__.Callback.on_event：
  Forward text and sentence-final markers.
- RecognitionSession.__init__.Callback.on_error：
  Report a sanitised service error.
- RecognitionSession.__init__.Callback.on_complete：
  Mark the final provider output boundary.
- RecognitionSession.start：
  Start SDK recognition on a worker thread.
- RecognitionSession.feed：
  Queue validated raw PCM bytes for the provider.
- RecognitionSession.stop：
  Signal end-of-input once and wait for provider completion.

关键变量：
- logger：
  Record resolved geography and model names without credentials or question text.
- SPEECH_REGION_ENDPOINTS：
  Supported regions mapped to public hosts and ASR workspace domain regions.
- MAX_AUDIO_BYTES：
  Maximum PCM bytes for a 120-second TTS utterance.
- TTS_TIMEOUT_SECONDS：
  Total synthesis deadline including WebSocket connection and audio collection.
- audio_store：
  Process-local bounded WAV cache shared by synthesis and download routes.
"""

import base64
import binascii
import io
import logging
import os
import re
import threading
import time
import wave
from collections import OrderedDict
from dataclasses import dataclass
from uuid import uuid4

logger = logging.getLogger(__name__)
SPEECH_REGION_ENDPOINTS = {
    "singapore": ("dashscope-intl.aliyuncs.com", "ap-southeast-1"),
    "beijing": ("dashscope.aliyuncs.com", "cn-beijing"),
}
MAX_AUDIO_BYTES = 24000 * 2 * 120
TTS_TIMEOUT_SECONDS = 45


class SpeechError(Exception):
    """Carry a safe error code and public explanation, never provider credentials."""

    def __init__(self, code, detail, status=503):
        """Save the public error contract."""
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


@dataclass(frozen=True)
class SpeechConfig:
    """Resolve shared credentials and separate ASR/TTS URLs for an explicit region.

    Frozen configuration carries SDK inputs; it neither authenticates the key nor
    alters the LLM endpoint. A workspace changes ASR routing only, in the same region.
    """

    api_key: str
    endpoint: str
    tts_endpoint: str
    tts_model: str
    tts_voice: str
    stt_model: str

    @classmethod
    def load(cls):
        """Read process speech/key settings and return validated SDK configuration.

        Disabled speech fails first. An absent region means Singapore for existing
        collaborators; blank/unknown regions fail without inference or retry. Key,
        workspace and model constraints retain their existing checks. Only resolved
        geography/model names are logged; no settings or credentials are written.
        """
        if os.getenv("SPEECH_ENABLED", "false").lower() != "true":
            raise SpeechError(
                "speech_disabled", "Set SPEECH_ENABLED=true in the repository-root .env."
            )
        region = os.getenv("SPEECH_REGION", "singapore").strip().lower()
        if region not in SPEECH_REGION_ENDPOINTS:
            logger.warning("Speech configuration rejected: unsupported SPEECH_REGION")
            raise SpeechError("speech_not_configured", "Set SPEECH_REGION to singapore or beijing.")
        host, workspace_region = SPEECH_REGION_ENDPOINTS[region]
        key = os.getenv("DASHSCOPE_API_KEY", "")
        workspace = os.getenv("DASHSCOPE_SPEECH_WORKSPACE_ID", "").strip()
        if not key or (workspace and not re.fullmatch(r"[A-Za-z0-9_-]+", workspace)):
            raise SpeechError(
                "speech_not_configured",
                "Configure DASHSCOPE_API_KEY for SPEECH_REGION and a valid optional workspace.",
            )
        tts_model = os.getenv("SPEECH_TTS_MODEL", "qwen3-tts-flash-realtime")
        stt_model = os.getenv("SPEECH_STT_MODEL", "qwen-audio-3.1-asr-flash-streaming")
        if not tts_model.startswith("qwen3-tts-flash-realtime") or stt_model not in {
            "qwen-audio-3.1-asr-flash-streaming",
            "qwen-audio-3.0-asr-flash-streaming",
        }:
            raise SpeechError(
                "unsupported_speech_model",
                "Use Qwen3-TTS-Flash-Realtime and Qwen-Audio-3.x-ASR-Flash-Streaming.",
            )
        logger.info(
            "Speech configuration resolved region=%s tts_model=%s stt_model=%s workspace=%s",
            region,
            tts_model,
            stt_model,
            bool(workspace),
        )
        return cls(
            key,
            f"wss://{workspace}.{workspace_region}.maas.aliyuncs.com/api-ws/v1/inference"
            if workspace
            else f"wss://{host}/api-ws/v1/inference",
            f"wss://{host}/api-ws/v1/realtime",
            tts_model,
            os.getenv("SPEECH_TTS_VOICE", "Cherry"),
            stt_model,
        )


def provider_error(exc):
    """Translate provider failures without reflecting raw exception messages."""
    if "freetieronly" in str(exc).lower():
        return SpeechError(
            "quota_exhausted",
            "The model's free quota is exhausted; ask the maintainer to check speech billing.",
        )
    return SpeechError(
        "speech_provider_error", "Speech service failed; check model access and quota."
    )


def configure_sdk(config):
    """Set the speech endpoint independently of the existing OpenAI-compatible LLM URL."""
    import dashscope

    # QwenTtsRealtime snapshots the process-wide SDK key in its constructor.
    # This single-account backend uses one credential; recognition still receives
    # its key explicitly, and the LLM HTTP endpoint remains separate.
    dashscope.api_key = config.api_key
    dashscope.base_websocket_api_url = config.endpoint


def synthesize(text):
    """Collect Qwen realtime PCM into one bounded WAV compatible with the UE bridge."""
    config = SpeechConfig.load()
    from dashscope.audio.qwen_tts_realtime import (
        AudioFormat,
        QwenTtsRealtime,
        QwenTtsRealtimeCallback,
    )

    class Callback(QwenTtsRealtimeCallback):
        """Bound streamed audio and distinguish completion, provider failure and disconnect."""

        def __init__(self):
            """Create thread-safe collection state for one synthesis request."""
            self.done = threading.Event()
            self.lock = threading.RLock()
            self.pcm = bytearray()
            self.error = None
            self.finished = False

        def fail(self, error):
            """Preserve the first sanitised error and release the waiting request."""
            with self.lock:
                if not self.done.is_set():
                    self.error = error
                    self.done.set()

        def on_event(self, event):
            """Decode bounded PCM deltas and accept only the final session completion boundary."""
            with self.lock:
                if self.done.is_set():
                    return
                if not isinstance(event, dict):
                    self.fail(SpeechError("invalid_speech_audio", "Invalid synthesis event."))
                    return
                kind = event.get("type")
                if kind == "error":
                    self.fail(provider_error(event.get("error", "")))
                elif kind == "response.audio.delta":
                    encoded = event.get("delta", "")
                    if (
                        not isinstance(encoded, str)
                        or len(encoded) > (MAX_AUDIO_BYTES + 2) // 3 * 4
                    ):
                        self.fail(SpeechError("invalid_speech_audio", "Oversized synthesis audio."))
                        return
                    try:
                        chunk = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error):
                        self.fail(SpeechError("invalid_speech_audio", "Invalid synthesis PCM."))
                        return
                    if len(self.pcm) + len(chunk) > MAX_AUDIO_BYTES:
                        self.fail(SpeechError("invalid_speech_audio", "Oversized synthesis audio."))
                        return
                    self.pcm.extend(chunk)
                elif kind == "response.done":
                    response = event.get("response") or {}
                    if not isinstance(response, dict):
                        self.fail(
                            SpeechError("invalid_speech_audio", "Invalid synthesis response.")
                        )
                    elif response.get("status") in {"failed", "cancelled", "incomplete"}:
                        self.fail(provider_error(response.get("status_details", "")))
                elif kind == "session.finished":
                    self.finished = True
                    self.done.set()

        def on_close(self, close_status_code, close_msg):
            """Reject connections closed before successful session completion."""
            self.fail(SpeechError("speech_connection_closed", "Speech connection closed early."))

    configure_sdk(config)
    collector = Callback()
    deadline = time.monotonic() + TTS_TIMEOUT_SECONDS
    synthesizer = None
    try:
        synthesizer = QwenTtsRealtime(
            model=config.tts_model,
            url=config.tts_endpoint,
            callback=collector,
        )
        synthesizer.connect()
        if collector.error:
            raise collector.error
        synthesizer.update_session(
            voice=config.tts_voice,
            response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            language_type="English",
            mode="server_commit",
        )
        synthesizer.append_text(text)
        synthesizer.finish()
        if not collector.done.wait(max(0, deadline - time.monotonic())):
            raise SpeechError("speech_timeout", "Speech synthesis timed out; retry or use text.")
        if collector.error:
            raise collector.error
        pcm = bytes(collector.pcm)
    except SpeechError:
        raise
    except Exception as exc:
        # The server may emit a quota error and close while the main thread is
        # sending input. Preserve that actionable error over a send exception.
        if collector.error:
            raise collector.error from exc
        raise provider_error(exc) from exc
    finally:
        if synthesizer is not None:
            try:
                synthesizer.close()
            except Exception:
                # Cleanup failures cannot replace an already collected provider error.
                pass
    if not pcm or len(pcm) % 2 or len(pcm) > MAX_AUDIO_BYTES:
        raise SpeechError("invalid_speech_audio", "Speech returned empty or oversized PCM audio.")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)
    return output.getvalue()


class AudioStore:
    """Thread-safe WAV cache with TTL, entry and byte limits; no disk writes."""

    def __init__(self, ttl=600, max_bytes=32 * 1024 * 1024, max_items=16, clock=time.monotonic):
        """Construct an isolated bounded cache."""
        self.ttl, self.max_bytes, self.max_items, self.clock = ttl, max_bytes, max_items, clock
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def _expire(self):
        """Remove expired entries; called only under the cache lock."""
        now = self.clock()
        for key, (expires, _) in list(self.items.items()):
            if expires <= now:
                del self.items[key]

    def put(self, wav):
        """Return a random capability ID, evicting oldest audio when capacity is reached."""
        if len(wav) > self.max_bytes:
            raise SpeechError("audio_too_large", "Audio exceeds temporary storage capacity.")
        with self.lock:
            self._expire()
            while self.items and (
                len(self.items) >= self.max_items
                or sum(len(item[1]) for item in self.items.values()) + len(wav) > self.max_bytes
            ):
                self.items.popitem(last=False)
            key = str(uuid4())
            self.items[key] = (self.clock() + self.ttl, wav)
            return key

    def get(self, key):
        """Retrieve unexpired WAV bytes, otherwise report an absent capability."""
        with self.lock:
            self._expire()
            item = self.items.get(key)
            return item[1] if item else None


audio_store = AudioStore()


class RecognitionSession:
    """Wrap one SDK recognition task; callbacks hand data to the ASGI event loop."""

    def __init__(self, emit):
        """Validate credentials and construct an English PCM recognition task."""
        config = SpeechConfig.load()
        from dashscope.audio.asr import Recognition, RecognitionCallback

        configure_sdk(config)

        class Callback(RecognitionCallback):
            """Forward provider events without sending audio or secrets to logs."""

            def on_event(self, result):
                """Forward text and sentence-final markers."""
                sentence = result.get_sentence()
                if sentence and "text" in sentence:
                    emit(
                        {
                            "type": "sentence",
                            "text": sentence["text"],
                            "segment": str(
                                sentence.get("sentence_id", sentence.get("begin_time", 0))
                            ),
                            "is_final": RecognitionResult.is_sentence_end(sentence),
                        }
                    )

            def on_error(self, result):
                """Report a sanitised service error."""
                error = provider_error(getattr(result, "message", ""))
                emit({"type": "error", "code": error.code, "detail": error.detail})

            def on_complete(self):
                """Mark the final provider output boundary."""
                emit({"type": "complete"})

        from dashscope.audio.asr import RecognitionResult

        self.recognition = Recognition(
            model=config.stt_model,
            format="pcm",
            sample_rate=16000,
            api_key=config.api_key,
            language_hints=["en"],
            request_timeout=140,
            callback=Callback(),
        )
        self.stopped = False

    def start(self):
        """Start SDK recognition on a worker thread."""
        self.recognition.start()

    def feed(self, pcm):
        """Queue validated raw PCM bytes for the provider."""
        self.recognition.send_audio_frame(pcm)

    def stop(self):
        """Signal end-of-input once and wait for provider completion."""
        if not self.stopped:
            self.stopped = True
            self.recognition.stop()
