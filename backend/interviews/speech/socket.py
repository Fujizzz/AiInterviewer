"""Responsibilities: Run one bounded speech-to-text task with an opt-in independent completion
observer.

Implementation: copy transcripts to an observer which coalesces settled final snapshots;
Model semantics determine end intent; PCM/text changes revoke it. After three silent seconds
ask the browser to flush; issue an MCP receipt only if the final transcript remains unchanged.
Related Modules: answer_completion classifies intent; answer_mcp signs the final transcript
boundary.

Declaration Index:
- stt_socket:
  Bridge PCM and SDK callbacks, returning final text after stop; the UI owns answer confirmation.
- stt_socket.output:
  Send one UTF-8 JSON event, with no original audio payload.
- stt_socket.enqueue:
  Run on the loop thread and detect callback backlog explicitly.
- stt_socket.emit:
  Safely dispatch provider-thread callbacks to the ASGI loop.
- pcm_has_voice: Detect audible activity in existing signed 16-bit mono PCM frames.

Variable Index:
- logger: Speech/observer failures and receipt lifecycle metadata, never audio or text.
- VOICE_RMS_FLOOR: New completion-only activity floor (0.015 full scale); does not alter ASR audio.
"""

import asyncio
import contextlib
import json
import logging
import math
import sys
from array import array
from collections import OrderedDict

from agents.answer_completion import AnswerCompletionAgent, FlashCompletionClassifier
from interviews.access import websocket_allowed
from interviews.answer_mcp import issue_completion_receipt

from .service import RecognitionSession, SpeechError, provider_error

logger = logging.getLogger(__name__)
VOICE_RMS_FLOOR = 0.015


def pcm_has_voice(pcm):
    """Inputs: validated little-endian PCM16 bytes. Outputs: RMS above the activity floor.
    Logic: normalize energy by full-scale amplitude; no audio mutation, model call or log.
    Constraints: coarse conservative activity guard, not a learned VAD or a transcription claim.
    """
    samples = array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return (
        math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768 >= VOICE_RMS_FLOOR
    )


async def stt_socket(scope, receive, send):
    """Inputs: authenticated ASGI scope/receive/send. Outputs: drafts/final/optional end events.
    Logic: retain original ASR limits; opt-in start with question_id creates an independent
    classifier. Transcript changes or >=50ms audible PCM revoke completion; final receipt
    requires unchanged text after flush. Manual stop still returns the original final contract.
    Constraints: no answer evaluation here; observer/configuration failure is explicit and terminal
    for this capture. Disconnect cancels classifier/ASR resources without restarting either.
    """
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
    completion = None
    question_id = None
    voiced_bytes = 0
    completion_text = None

    async def output(message):
        """Send one UTF-8 JSON event, with no original audio payload.
        """
        await send({"type": "websocket.send", "text": json.dumps(message)})

    def enqueue(message):
        """Run on the loop thread and detect callback backlog explicitly.
        """
        if events.full():
            overflow.set()
        else:
            events.put_nowait(message)

    def emit(message):
        """Safely dispatch provider-thread callbacks to the ASGI loop.
        """
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
        if (
            not isinstance(command, dict)
            or command.get("type") != "start"
            or set(command) - {"type", "completion_detection", "question_id"}
        ):
            raise SpeechError("invalid_start", 'Send {"type":"start"} before audio.', 400)
        if type(command.get("completion_detection", False)) is not bool:
            raise SpeechError("invalid_start", "completion_detection must be boolean.", 400)
        if command.get("completion_detection"):
            question_id = command.get("question_id")
            if not isinstance(question_id, str) or not 0 < len(question_id.strip()) <= 128:
                raise SpeechError("invalid_start", "Provide the current question_id.", 400)
            try:
                completion = AnswerCompletionAgent(FlashCompletionClassifier(), loop.time)
            except Exception as exc:
                logger.error(
                    "Completion setup failed exception=%s; check completion configuration",
                    type(exc).__name__,
                )
                raise SpeechError(
                    "completion_configuration_error",
                    "Check answer completion model/key configuration.",
                ) from exc
        elif "question_id" in command:
            raise SpeechError("invalid_start", "question_id requires completion_detection.", 400)
        session = (
            RecognitionSession(emit, language_hints=["zh", "en"])
            if completion is not None
            else RecognitionSession(emit)
        )
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
                timeout=0.1 if completion is not None else 1,
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
                    if completion is not None:
                        voiced_bytes = voiced_bytes + len(pcm) if pcm_has_voice(pcm) else 0
                        if voiced_bytes >= 16000 * 2 * 0.05:
                            completion.activity()
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
                    if completion is not None:
                        completion.stop_admission()
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
                    text = " ".join(texts)
                    if completion is not None:
                        # Copy text; settling/coalescing controls calls without keyword filtering.
                        completion.observe(text, event["is_final"])
                    await output({"type": "partial", "text": text})
                if event["type"] == "complete":
                    if stopping is None:
                        raise SpeechError(
                            "recognition_ended",
                            "Recognition ended before stop; start a new recording.",
                        )
                    final_text = " ".join(segments.values())
                    final = {
                        "type": "final",
                        "text": final_text,
                        "finalization_ms": round((loop.time() - stopped_at) * 1000),
                    }
                    if (
                        completion is not None
                        and completion.announced
                        and completion_text == final_text
                        and final_text.strip()
                    ):
                        final["completion_receipt"] = issue_completion_receipt(
                            getattr(scope.get("user"), "pk", None), question_id, final_text
                        )
                        logger.info(
                            "Answer completion receipt issued question=%s chars=%d",
                            question_id,
                            len(final_text),
                        )
                    await output(final)
                    await send({"type": "websocket.close", "code": 1000})
                    return
                event_task = asyncio.create_task(events.get())
            if stopping is not None and stopping.done():
                stopping.result()
            if completion is not None:
                try:
                    completed = completion.poll()
                except Exception as exc:
                    raise SpeechError(
                        "completion_detection_failed",
                        "Answer completion detection failed; check model logs.",
                    ) from exc
                if completed and stopping is None:
                    completion_text = completion.text
                    await output({"type": "answer_completion", "question_id": question_id})
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
        logger.error(
            "STT failed stage=capture_or_finalize exception=%s; inspect speech/model configuration",
            type(exc).__name__,
        )
        error = provider_error(exc)
        await output({"type": "error", "code": error.code, "detail": error.detail})
        await send({"type": "websocket.close", "code": 1011})
    finally:
        for task in (receive_task, event_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        try:
            if completion is not None:
                await completion.close()
        finally:
            # Observer cleanup cannot prevent release of the independently owned ASR task.
            if session is not None:
                with contextlib.suppress(Exception):
                    if stopping is None:
                        await asyncio.wait_for(asyncio.to_thread(session.stop), 10)
                    else:
                        await asyncio.wait_for(asyncio.shield(stopping), 10)
