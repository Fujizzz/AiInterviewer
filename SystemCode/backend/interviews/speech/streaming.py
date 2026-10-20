"""Responsibilities: Stream authenticated synthesis to browser PCM playback.
Implementation: Bridge the existing synchronous adapter to an asynchronous NDJSON
response using a bounded queue. Preserve one provider call, explicit terminal
success/error, and disconnect cancellation; never retry or replay partial audio.
Related Modules: service owns provider/model/audio validation; config.urls routes
tts_stream; accounts and CSRF middleware retain the existing write protections.
Declaration Index:
- tts_stream: Validate the request before constructing the asynchronous response.
- speech_events: Deliver ordered start/audio/end or error records with cancellation.
- speech_events.offer: Apply bounded backpressure to the provider callback thread.
- speech_events.deliver: Align PCM samples and split them into bounded fragments.
- speech_events.complete: Resolve the worker outcome only on the event-loop thread.
- speech_events.produce: Run the existing adapter and record a sanitized outcome.
- speech_events.observe_worker: Surface unexpected executor failures in the log.
- encode_event: Encode one bounded protocol record without logging its payload.
Variable Index:
- logger: Stage timings and safe failure/cancellation context, never speech or keys.
- STREAM_CHUNK_BYTES: Maximum aligned PCM bytes in each browser event.
- STREAM_QUEUE_ITEMS: Bound buffered events when the browser reads slowly.
"""

import asyncio
import base64
import concurrent.futures
import json
import logging
import threading
import time
from uuid import uuid4

from django.http import JsonResponse, StreamingHttpResponse

from .service import SpeechConfig, SpeechError, TTS_TIMEOUT_SECONDS, synthesize


logger = logging.getLogger(__name__)
STREAM_CHUNK_BYTES = 8192
STREAM_QUEUE_ITEMS = 32


def encode_event(event):
    """Functionality: Serialize a single NDJSON protocol record.
    Inputs: An internally constructed JSON-compatible event dictionary.
    Outputs: UTF-8 bytes terminated by exactly one newline.
    Logic: Compact JSON escapes any strings, preserving line framing.
    Constraints: Audio is bounded and base64-encoded by the producer; no logs.
    """
    return (json.dumps(event, separators=(",", ":")) + "\n").encode()


async def tts_stream(request):
    """Functionality: Start browser speech streaming behind normal authentication.
    Inputs: Django request containing POST JSON with 1–1200 text characters.
    Outputs: A 4xx/503 JSON error or an asynchronous NDJSON response.
    Logic: Validate method/body/config before headers; defer paid synthesis until
    the server iterates the stream. Outer middleware enforces account and CSRF.
    Constraints: No database writes, cached audio capabilities or fallback calls;
    errors after headers are explicit stream records rather than HTTP status changes.
    """
    if request.method != "POST":
        return JsonResponse({"error": {"code": "method_not_allowed"}}, status=405)
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeError):
        return JsonResponse({"error": {"code": "invalid_text", "detail": "Provide valid JSON text."}}, status=400)
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 1200:
        return JsonResponse(
            {"error": {"code": "invalid_text", "detail": "Provide 1-1200 characters of question text."}},
            status=400,
        )
    try:
        SpeechConfig.load()
    except SpeechError as exc:
        return JsonResponse({"error": {"code": exc.code, "detail": exc.detail}}, status=exc.status)
    response = StreamingHttpResponse(speech_events(text.strip()), content_type="application/x-ndjson")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    response["X-Content-Type-Options"] = "nosniff"
    return response


async def speech_events(text):
    """Functionality: Yield incremental PCM while preserving terminal validation.
    Inputs: Validated question text; current asyncio event loop is implicit input.
    Outputs: start, sequential audio, and one end/error event as UTF-8 NDJSON.
    Logic: One executor worker invokes synthesize; its callback waits for bounded
    queue space, while a separate completion future cannot be blocked by a full
    audio queue. Any failed outcome takes precedence over buffered partial audio.
    Constraints: 24 kHz mono signed little-endian PCM, original 45-second deadline,
    and 120-second audio bound. Cancellation discards pending fragments and signals
    the adapter; an in-progress SDK connect closes once its bounded call returns.
    """
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=STREAM_QUEUE_ITEMS)
    completed = loop.create_future()
    cancelled = threading.Event()
    utterance_id = str(uuid4())
    started = time.monotonic()
    deadline = started + TTS_TIMEOUT_SECONDS
    pending = bytearray()
    byte_count = 0
    first_audio_ms = None

    def offer(chunk):
        """Functionality: Transfer one fragment with bounded producer backpressure.
        Inputs: Aligned bounded PCM bytes; queue, cancellation and deadline closure.
        Outputs: None or SpeechError, which the adapter treats as a terminal failure.
        Logic: Schedule one queue insertion, then wait in cancellable slices.
        Constraints: Repeated waits observe the same insertion, not network retries.
        """
        future = asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
        try:
            while True:
                if cancelled.is_set():
                    raise SpeechError("speech_cancelled", "Speech playback was cancelled.", status=499)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SpeechError("speech_timeout", "Speech delivery timed out.")
                try:
                    future.result(timeout=min(0.1, remaining))
                    return
                except concurrent.futures.TimeoutError:
                    continue
        finally:
            if not future.done():
                future.cancel()

    def deliver(chunk):
        """Functionality: Preserve PCM sample alignment across provider fragments.
        Inputs: Adapter-validated bytes; pending partial sample and counters.
        Outputs: Aligned fragments offered to the queue; first-audio timing log.
        Logic: Hold at most one unmatched byte, splitting complete samples at 8 KiB.
        Constraints: The adapter validates final alignment; this function never
        pads, resamples, persists or prints user audio.
        """
        nonlocal byte_count, first_audio_ms
        pending.extend(chunk)
        while len(pending) >= 2:
            size = min(STREAM_CHUNK_BYTES, len(pending) // 2 * 2)
            fragment = bytes(pending[:size])
            del pending[:size]
            offer(fragment)
            byte_count += size
            if first_audio_ms is None:
                first_audio_ms = round((time.monotonic() - started) * 1000)
                logger.info("Speech stream first PCM utterance=%s first_audio_ms=%s",
                            utterance_id, first_audio_ms)

    def complete(outcome):
        """Functionality: Publish a producer outcome on its owning event loop.
        Inputs: Safe terminal dictionary; completed future from the closure.
        Outputs: None; resolves the future at most once.
        Logic: Ignore a previously completed/cancelled future.
        Constraints: Called only via call_soon_threadsafe by the worker.
        """
        if not completed.done():
            completed.set_result(outcome)

    def produce():
        """Functionality: Execute one real synthesis and record its terminal result.
        Inputs: Text, cancellation event and callback from the enclosing request.
        Outputs: Completion future receives success metadata or a safe error.
        Logic: Call the unchanged model protocol once; successful WAV validation
        confirms the delivered PCM. Discard WAV bytes after deriving final timing.
        Constraints: No fallback, retry or text/audio logging. Partial speech is
        never marked successful if provider completion or WAV validation fails.
        """
        logger.info("Speech stream started utterance=%s characters=%d", utterance_id, len(text))
        try:
            synthesize(text, on_pcm=deliver, cancelled=cancelled)
            outcome = {"type": "end", "utterance_id": utterance_id,
                       "audio_bytes": byte_count, "duration_ms": round(byte_count / 48),
                       "generation_ms": round((time.monotonic() - started) * 1000),
                       "first_audio_ms": first_audio_ms}
            logger.info("Speech stream completed utterance=%s audio_bytes=%d generation_ms=%d",
                        utterance_id, byte_count, outcome["generation_ms"])
        except SpeechError as exc:
            outcome = {"type": "error", "error": {"code": exc.code, "detail": exc.detail}}
            logger.warning("Speech stream failed utterance=%s code=%s elapsed_ms=%d",
                           utterance_id, exc.code, round((time.monotonic()-started)*1000))
        except Exception as exc:
            outcome = {"type": "error", "error": {
                "code": "speech_stream_failed", "detail": "Speech streaming failed."}}
            logger.error("Speech stream worker failed utterance=%s exception_type=%s",
                         utterance_id, type(exc).__name__)
        loop.call_soon_threadsafe(complete, outcome)

    def observe_worker(future):
        """Functionality: Consume unexpected executor failure without hiding it.
        Inputs: Completed worker future and safe request identifier.
        Outputs: None; logs exception type only if produce failed unexpectedly.
        Logic: Normal adapter failures were already converted into protocol errors.
        Constraints: Does not retry, change an outcome or print raw exceptions.
        """
        if not future.cancelled() and future.exception() is not None:
            logger.error("Speech executor failed utterance=%s exception_type=%s",
                         utterance_id, type(future.exception()).__name__)

    worker = loop.run_in_executor(None, produce)
    worker.add_done_callback(observe_worker)
    get_next = None
    sequence = 0
    terminal = False
    try:
        yield encode_event({"type": "start", "utterance_id": utterance_id,
                            "sample_rate": 24000, "channels": 1, "sample_width": 2})
        while True:
            if completed.done():
                outcome = completed.result()
                if outcome["type"] == "error" or queue.empty():
                    terminal = True
                    yield encode_event(outcome)
                    return
                chunk = queue.get_nowait()
                yield encode_event({"type": "audio", "sequence": sequence,
                                    "pcm": base64.b64encode(chunk).decode("ascii")})
                sequence += 1
                continue
            get_next = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({get_next, completed}, return_when=asyncio.FIRST_COMPLETED)
            if completed.done() and completed.result()["type"] == "error":
                get_next.cancel()
                await asyncio.gather(get_next, return_exceptions=True)
                get_next = None
                continue
            if get_next in done:
                chunk = get_next.result()
                get_next = None
                yield encode_event({"type": "audio", "sequence": sequence,
                                    "pcm": base64.b64encode(chunk).decode("ascii")})
                sequence += 1
            else:
                get_next.cancel()
                await asyncio.gather(get_next, return_exceptions=True)
                get_next = None
    finally:
        cancelled.set()
        if get_next is not None:
            get_next.cancel()
            await asyncio.gather(get_next, return_exceptions=True)
        if not terminal:
            logger.info("Speech stream consumer disconnected utterance=%s sent_chunks=%d",
                        utterance_id, sequence)
