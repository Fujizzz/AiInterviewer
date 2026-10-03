"""One bounded English STT task per local WebSocket; transcripts never submit answers.

目录：
- stt_socket：
  Bridge PCM and SDK callbacks, returning final text after stop; the UI owns answer confirmation.
- stt_socket.output：
  Send one UTF-8 JSON event, with no original audio payload.
- stt_socket.enqueue：
  Run on the loop thread and detect callback backlog explicitly.
- stt_socket.emit：
  Safely dispatch provider-thread callbacks to the ASGI loop.

关键变量：
（无模块级变量。）
"""

import asyncio
import contextlib
import json
from collections import OrderedDict

from interviews.access import websocket_allowed

from .service import RecognitionSession, SpeechError, provider_error


async def stt_socket(scope, receive, send):
    """Bridge PCM/SDK callbacks; return final text after stop. The UI owns answer confirmation."""
    if (await receive())["type"] != "websocket.connect":
        return
    if not websocket_allowed(scope):
        await send({"type": "websocket.close", "code": 1008})
        return
    await send({"type": "websocket.accept"})
    loop = asyncio.get_running_loop()
    events = asyncio.Queue(maxsize=64)
    overflow = asyncio.Event()
    session = None
    receive_task = None
    event_task = None
    segments = OrderedDict()
    total = 0
    stopping = None
    stopped_at = None
    started_at = loop.time()

    async def output(message):
        """Send one UTF-8 JSON event, with no original audio payload."""
        await send({"type": "websocket.send", "text": json.dumps(message)})

    def enqueue(message):
        """Run on the loop thread and detect callback backlog explicitly."""
        if events.full():
            overflow.set()
        else:
            events.put_nowait(message)

    def emit(message):
        """Safely dispatch provider-thread callbacks to the ASGI loop."""
        if not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(enqueue, message)

    try:
        await output(
            {
                "type": "hello",
                "sample_rate": 16000,
                "channels": 1,
                "sample_bits": 16,
                "max_seconds": 120,
                "max_chunk_bytes": 8192,
            }
        )
        first = await asyncio.wait_for(receive(), 15)
        if first["type"] == "websocket.disconnect":
            return
        first_text = first.get("text")
        if not isinstance(first_text, str) or len(first_text) > 1024:
            raise SpeechError(
                "invalid_start", "Send a JSON start command up to 1024 characters.", 400
            )
        try:
            command = json.loads(first_text)
        except ValueError as exc:
            raise SpeechError("invalid_start", "Send a JSON start command.", 400) from exc
        if not isinstance(command, dict) or command != {"type": "start"}:
            raise SpeechError("invalid_start", 'Send {"type":"start"} before audio.', 400)
        session = RecognitionSession(emit)
        await asyncio.wait_for(asyncio.to_thread(session.start), 15)
        await output({"type": "started"})
        receive_task = asyncio.create_task(receive())
        event_task = asyncio.create_task(events.get())
        while True:
            if overflow.is_set():
                raise SpeechError("speech_backpressure", "Recognition callbacks exceeded capacity.")
            if loop.time() - started_at > 135:
                raise SpeechError("speech_timeout", "Answer exceeded the 120-second limit.", 400)
            if stopped_at is not None and loop.time() - stopped_at > 15:
                raise SpeechError(
                    "speech_timeout", "Final transcription timed out; start a new recording."
                )
            done, _ = await asyncio.wait(
                [receive_task, event_task],
                timeout=1,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                incoming = receive_task.result()
                if incoming["type"] == "websocket.disconnect":
                    return
                pcm = incoming.get("bytes")
                if pcm is not None:
                    if stopping is not None:
                        raise SpeechError("audio_after_stop", "Audio cannot follow stop.", 400)
                    if not pcm or len(pcm) % 2 or len(pcm) > 8192:
                        raise SpeechError(
                            "invalid_pcm", "Send even-sized PCM frames up to 8192 bytes.", 400
                        )
                    total += len(pcm)
                    if total > 16000 * 2 * 120:
                        raise SpeechError("audio_limit", "Answer exceeded 120 seconds of PCM.", 400)
                    session.feed(pcm)
                else:
                    control_text = incoming.get("text")
                    if not isinstance(control_text, str) or len(control_text) > 1024:
                        raise SpeechError(
                            "invalid_control", "Send a bounded JSON stop command.", 400
                        )
                    try:
                        control = json.loads(control_text)
                    except ValueError as exc:
                        raise SpeechError(
                            "invalid_control", "Expected a stop command.", 400
                        ) from exc
                    if control != {"type": "stop"} or stopping is not None:
                        raise SpeechError("invalid_control", "Send stop only once.", 400)
                    stopping = asyncio.create_task(asyncio.to_thread(session.stop))
                    stopped_at = loop.time()
                receive_task = asyncio.create_task(receive())
            if event_task in done:
                event = event_task.result()
                if event["type"] == "error":
                    await output(event)
                    await send({"type": "websocket.close", "code": 1011})
                    return
                if event["type"] == "sentence":
                    if event["is_final"]:
                        segments[event["segment"]] = event["text"]
                    texts = list(segments.values())
                    if not event["is_final"]:
                        texts.append(event["text"])
                    await output({"type": "partial", "text": " ".join(texts)})
                if event["type"] == "complete":
                    if stopping is None:
                        raise SpeechError(
                            "recognition_ended",
                            "Recognition ended before stop; start a new recording.",
                        )
                    await output(
                        {
                            "type": "final",
                            "text": " ".join(segments.values()),
                            "finalization_ms": round((loop.time() - stopped_at) * 1000),
                        }
                    )
                    await send({"type": "websocket.close", "code": 1000})
                    return
                event_task = asyncio.create_task(events.get())
            if stopping is not None and stopping.done():
                stopping.result()
    except (SpeechError, TimeoutError) as exc:
        error = (
            exc
            if isinstance(exc, SpeechError)
            else SpeechError("speech_timeout", "Speech service timed out.")
        )
        await output({"type": "error", "code": error.code, "detail": error.detail})
        await send({"type": "websocket.close", "code": 1011})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = provider_error(exc)
        await output({"type": "error", "code": error.code, "detail": error.detail})
        await send({"type": "websocket.close", "code": 1011})
    finally:
        for task in (receive_task, event_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if session is not None:
            with contextlib.suppress(Exception):
                if stopping is None:
                    await asyncio.wait_for(asyncio.to_thread(session.stop), 10)
                else:
                    await asyncio.wait_for(asyncio.shield(stopping), 10)
