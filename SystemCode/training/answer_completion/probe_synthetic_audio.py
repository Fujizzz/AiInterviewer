"""Responsibilities: exercise real production completion using fixed fictional TTS/ASR cases.

Implementation: generate checksum-bound bilingual WAV/PCM fixtures with the configured provider,
stream each to authenticated STT, check automatic notification/receipt expectations and silence,
submit one positive case through real MCP and inspect stored evaluation/next-step evidence.
Related Modules: speech.service supplies configuration; production stt_socket/answer_mcp execute
the actual detection; this operator probe does not import or substitute a classifier.
Declaration Index:
- write_report: atomically persist progress and final evidence without credentials.
- synthesize_fixture: obtain bounded real TTS PCM for an explicit fixture language.
- synthesize_fixture.Collector: synchronize provider audio/completion/errors.
- synthesize_fixture.Collector.__init__: initialize one audio collector.
- synthesize_fixture.Collector.on_event: validate and collect PCM and completion events.
- synthesize_fixture.Collector.on_close: reject a connection closed before completion.
- browser_pcm: reproduce the browser area-average PCM16 conversion at 24k -> 16k.
- capture_case: stream one fixed case and collect observable completion/receipt behavior.
- capture_case.receive_events: read events concurrently while audio is sent at real time.
- capture_case.stream: send a bounded PCM interval, recording premature end notifications.
- start_interview: initialize MCP and a fictional interview for one real answer submission.
- submit_answer: use the signed STT receipt and inspect real Agent persistence/next-step.
- main: validate fixtures, initialize temporary authentication, execute cases and clean records.
Variable Index: FRAME_BYTES/FRAME_SECONDS control 20ms PCM transmission; OBSERVATION_SECONDS
is a 15s test observation interval after audio (does not alter server deadlines or thresholds).
Constraints: no retries, model/region fallbacks, test-set fitting or production changes. Provider
failures remain recorded and fail the suite; synthetic transcripts are test artifacts, never logs.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import threading
import time
import wave
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

FRAME_BYTES = 640
FRAME_SECONDS = 0.02
OBSERVATION_SECONDS = 15


def write_report(path, report):
    """Functionality: preserve reproducible progress before the next external operation.
    Inputs: report path and JSON-safe evidence. Outputs: atomically replaced UTF-8 JSON.
    Logic: write an adjacent temporary file, then replace; credentials/receipt tokens are excluded.
    Constraints: filesystem errors propagate; this is not an API retry or a success substitute.
    """
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def synthesize_fixture(text, language, config):
    """Functionality: convert one fictional Chinese/English text into real provider WAV.
    Inputs: fixed text/language and validated production SpeechConfig; returns PCM24k and WAV.
    Logic: use existing TTS model/voice/format/45s budget; only fixture language is explicit.
    Constraints: synchronous SDK calls retain existing behavior; no alternate model or retry.
    Configuration changes occur only in this probe process, not the production service.
    """
    from dashscope.audio.qwen_tts_realtime import (
        AudioFormat,
        QwenTtsRealtime,
        QwenTtsRealtimeCallback,
    )
    from interviews.speech.service import MAX_AUDIO_BYTES, TTS_TIMEOUT_SECONDS, configure_sdk

    class Collector(QwenTtsRealtimeCallback):
        """Functionality: bound real TTS callback collection. Logic: lock protects byte/error state.
        Constraints: provider errors and early closure are terminal; no synthetic audio replacement.
        """

        def __init__(self):
            """Inputs: none. Outputs: fresh completion event/lock, bounded PCM and safe error state.
            Logic: callbacks share this state; finished distinguishes success from disconnection.
            """
            self.lock, self.done = threading.RLock(), threading.Event()
            self.pcm, self.error, self.finished = bytearray(), None, False

        def on_event(self, event):
            """Inputs: provider event dict. Outputs: collected audio or safe terminal error.
            Logic: validate event shape/base64/byte limits and wait for session.finished.
            Constraints: raw provider errors are never reflected in metadata or console output.
            """
            with self.lock:
                if self.done.is_set():
                    return
                try:
                    if not isinstance(event, dict):
                        raise ValueError("invalid_tts_event")
                    kind = event.get("type")
                    if kind == "error":
                        raise ValueError("tts_provider_error")
                    if kind == "response.audio.delta":
                        encoded = event.get("delta", "")
                        if (
                            not isinstance(encoded, str)
                            or len(encoded) > (MAX_AUDIO_BYTES + 2) // 3 * 4
                        ):
                            raise ValueError("invalid_tts_delta")
                        chunk = base64.b64decode(encoded, validate=True)
                        if len(self.pcm) + len(chunk) > MAX_AUDIO_BYTES:
                            raise ValueError("tts_audio_limit")
                        self.pcm.extend(chunk)
                    if kind == "response.done" and event.get("response", {}).get("status") in {
                        "failed",
                        "cancelled",
                        "incomplete",
                    }:
                        raise ValueError("tts_incomplete_response")
                    if kind == "session.finished":
                        self.finished = True
                        self.done.set()
                except Exception as exc:
                    self.error = type(exc).__name__
                    self.done.set()

        def on_close(self, close_status_code, close_msg):
            """Inputs: SDK close status/reason; outputs: safe early-close error when not finished.
            Logic: successful completion remains unchanged; provider reason text is not retained.
            """
            with self.lock:
                if not self.done.is_set():
                    self.error = "TTSClosedBeforeCompletion"
                    self.done.set()

    configure_sdk(config)
    collector = Collector()
    client = QwenTtsRealtime(model=config.tts_model, url=config.tts_endpoint, callback=collector)
    deadline = time.monotonic() + TTS_TIMEOUT_SECONDS
    try:
        client.connect()
        client.update_session(
            voice=config.tts_voice,
            response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            language_type={"en": "English", "zh": "Chinese"}[language],
            mode="server_commit",
        )
        client.append_text(text)
        client.finish()
        if not collector.done.wait(max(0, deadline - time.monotonic())):
            raise TimeoutError("TTS fixture did not finish within the existing budget")
        if collector.error or not collector.finished:
            raise RuntimeError(f"TTS fixture failed: {collector.error}")
        pcm = bytes(collector.pcm)
        if not pcm or len(pcm) % 2 or len(pcm) > MAX_AUDIO_BYTES:
            raise ValueError("TTS fixture PCM contract failed")
    finally:
        client.close()
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(24000)
        target.writeframes(pcm)
    return pcm, output.getvalue()


def browser_pcm(pcm24k):
    """Functionality: reproduce PCM16Resampler area-average sampling for generated PCM.
    Inputs: little-endian 24kHz signed PCM16. Outputs: little-endian 16kHz signed PCM16 bytes.
    Logic: retain partial input-sample area across output boundaries, clip and JS-round quantize.
    Constraints: mirrors float32 AudioWorklet inputs; resampling is test preparation, not VAD.
    """
    import math

    import numpy as np

    samples = np.frombuffer(pcm24k, dtype="<i2").astype(np.float32) / 32768
    output, weight, area = [], 0.0, 0.0
    for sample in samples:
        remaining = 1.0
        while remaining > 1e-9:
            width = min(remaining, 1.5 - weight)
            area += float(sample) * width
            weight += width
            remaining -= width
            if weight >= 1.5 - 1e-9:
                value = max(-1.0, min(1.0, area / 1.5))
                output.append(math.floor(value * (32768 if value < 0 else 32767) + 0.5))
                weight = area = 0.0
    return np.asarray(output, dtype="<i2").tobytes()


def capture_case(case, clips, cookie, question_id, websocket_url, owner_id):
    """Functionality: exercise one real STT capture and evaluate fixed notification expectations.
    Inputs: authored case, PCM clips, temporary session Cookie, question/owner and endpoint.
    Outputs: metadata/synthetic transcript result plus in-memory final event for optional MCP.
    Logic: stream 20ms PCM, observe at most 15s silence, stop once, validate final signing;
    gap mode adds exactly 1s silence, after_notice injects voiced supplement after first notice.
    Constraints: a negative result is bounded evidence, not proof it will never trigger later.
    Reader/provider failures remain explicit; receipt tokens and Cookie never enter the report.
    """
    from interviews.answer_mcp import verify_completion_receipt
    from websockets.sync.client import connect

    events, reader_errors = [], []
    lock = threading.Lock()
    done, notice = threading.Event(), threading.Event()
    result = {
        "id": case["id"],
        "language": case["language"],
        "category": case["category"],
        "expected_events": case["expected_events"],
        "expected_receipt": case["expected_receipt"],
        "mode": case["mode"],
        "premature_notice": False,
        "errors": [],
    }
    stop_sent = False
    started = time.monotonic()
    origin = websocket_url.replace("wss://", "https://").split("/ws/")[0]
    with connect(
        websocket_url,
        origin=origin,
        additional_headers={"Cookie": cookie},
        open_timeout=10,
        close_timeout=5,
    ) as socket:
        if json.loads(socket.recv(timeout=10))["type"] != "hello":
            raise RuntimeError("STT hello contract failed")
        socket.send(
            json.dumps({"type": "start", "completion_detection": True, "question_id": question_id})
        )
        if json.loads(socket.recv(timeout=20))["type"] != "started":
            raise RuntimeError("STT start contract failed")

        def receive_events():
            """Inputs: open STT socket; outputs: synchronized events and terminal/notice flags.
            Logic: timestamp real wire events while the main thread streams audio.
            Constraints: only this fictional capture is collected; errors are safe types/codes.
            """
            try:
                while True:
                    event = json.loads(socket.recv(timeout=35))
                    with lock:
                        events.append((time.monotonic(), event))
                    if event["type"] == "answer_completion":
                        if event.get("question_id") != question_id:
                            raise ValueError("Unexpected completion question binding")
                        notice.set()
                    if event["type"] in {"final", "error"}:
                        done.set()
                        return
            except Exception as exc:
                reader_errors.append(type(exc).__name__)
                done.set()

        reader = threading.Thread(target=receive_events, daemon=True)
        reader.start()

        def stream(pcm, primary_audio=False):
            """Inputs: a bounded PCM interval and whether it is the first voiced clip.
            Outputs: sent PCM and a premature-notice audit flag. Logic: use 20ms pacing.
            Constraints: stop on terminal provider/reader failure; do not modify server timers.
            """
            deadline = time.monotonic()
            for offset in range(0, len(pcm), FRAME_BYTES):
                if done.is_set():
                    raise RuntimeError("STT terminated during input audio")
                if primary_audio and notice.is_set():
                    result["premature_notice"] = True
                socket.send(pcm[offset : offset + FRAME_BYTES])
                deadline += FRAME_SECONDS
                time.sleep(max(0, deadline - time.monotonic()))

        try:
            stream(clips[0], primary_audio=True)
            if case["mode"] == "gap":
                stream(bytes(round(case["gap_seconds"] * 16000) * 2))
                result["notice_before_supplement"] = notice.is_set()
                stream(clips[1])
            elif case["mode"] == "after_notice":
                limit = time.monotonic() + OBSERVATION_SECONDS
                while not notice.is_set() and not done.is_set() and time.monotonic() < limit:
                    stream(bytes(FRAME_BYTES))
                result["notice_before_supplement"] = notice.is_set()
                if not notice.is_set():
                    result["errors"].append("MissingInitialNoticeForRevocationTest")
                else:
                    # Deliberately delay client stop to simulate supplementary PCM during flush.
                    stream(clips[1])
            audio_ended = time.monotonic()
            observation_limit = audio_ended + OBSERVATION_SECONDS
            observe_full_interval = case["mode"] != "single" or not case["expected_receipt"]
            while not done.is_set() and time.monotonic() < observation_limit:
                if notice.is_set() and not observe_full_interval:
                    break
                stream(bytes(FRAME_BYTES))
            if not done.is_set():
                socket.send(json.dumps({"type": "stop"}))
                stop_sent = True
                if not done.wait(20):
                    result["errors"].append("FinalTranscriptTimeout")
            reader.join(timeout=2)
        except Exception as exc:
            result["errors"].append(type(exc).__name__)
            # Error audit ends this capture; no reconnect, alternate inference or answer submission.
        with lock:
            snapshot = list(events)
        final_events = [event for _, event in snapshot if event["type"] == "final"]
        final = final_events[-1] if final_events else {}
        notifications = [when for when, event in snapshot if event["type"] == "answer_completion"]
        partials = [(when, event["text"]) for when, event in snapshot if event["type"] == "partial"]
        result.update(
            {
                "observed_events": len(notifications),
                "final_received": bool(final),
                "receipt_present": bool(final.get("completion_receipt")),
                "synthetic_asr_text": final.get("text", ""),
                "partial_event_count": len(partials),
                "reader_errors": list(reader_errors),
                "stop_sent_once": stop_sent,
                "provider_error_codes": [
                    event["code"] for _, event in snapshot if event["type"] == "error"
                ],
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
        if notifications:
            preceding = [when for when, _ in partials if when <= notifications[0]]
            if preceding:
                result["notice_seconds_after_preceding_partial"] = round(
                    notifications[0] - max(preceding), 3
                )
            result["notice_after_audio_end_seconds"] = (
                round(notifications[0] - audio_ended, 3) if "audio_ended" in locals() else None
            )
        if final.get("completion_receipt"):
            try:
                verify_completion_receipt(
                    final["completion_receipt"], owner_id, question_id, final["text"]
                )
                result["receipt_binding_verified"] = True
            except Exception as exc:
                result["errors"].append(type(exc).__name__)
        result["passed"] = (
            not result["errors"]
            and not reader_errors
            and not result["provider_error_codes"]
            and not result["premature_notice"]
            and bool(final.get("text", "").strip())
            and result["observed_events"] == case["expected_events"]
            and result["receipt_present"] == case["expected_receipt"]
            and (
                not notifications or result.get("notice_seconds_after_preceding_partial", 0) >= 2.9
            )
            and (not case["expected_receipt"] or result.get("receipt_binding_verified", False))
        )
        return result, final


def start_interview(stack, cookie, base_url):
    """Functionality: create one actual fictional interview for the first positive audio case.
    Inputs: resource ExitStack, temporary session Cookie and production HTTPS URL.
    Outputs: open initialized Agent socket and real current question ID.
    Logic: MCP handshake then original start; defaults/LLM parameters remain production values.
    Constraints: no request retries, resume ingestion, human records or modified server budgets.
    """
    from websockets.sync.client import connect

    socket = stack.enter_context(
        connect(
            base_url.replace("https://", "wss://") + "/ws/agent/",
            origin=base_url,
            additional_headers={"Cookie": cookie},
            open_timeout=10,
            close_timeout=5,
        )
    )
    if "answer_completion_mcp" not in json.loads(socket.recv(timeout=10))["capabilities"]:
        raise ValueError("Production MCP capability missing")
    socket.send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "synthetic-audio-suite", "version": "1"},
                },
            }
        )
    )
    if "result" not in json.loads(socket.recv(timeout=10)):
        raise ValueError("MCP initialization failed")
    socket.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    socket.send(
        json.dumps(
            {
                "type": "start",
                "request_id": str(uuid4()),
                "resume_text": (
                    "Fictional candidate. Software engineer, Python and distributed systems. "
                    "Built a background task queue with isolated workers, idempotency keys, "
                    "bounded retries and failure monitoring."
                ),
                "job_title": "Software Engineer",
                "max_questions": 3,
                "duration_minutes": 3,
            }
        )
    )
    if json.loads(socket.recv(timeout=120))["type"] != "started":
        raise ValueError("Fictional interview start failed")
    response = json.loads(socket.recv(timeout=120))
    if response["type"] != "question":
        raise ValueError("Fictional interview question missing")
    socket.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}))
    if "result" not in json.loads(socket.recv(timeout=10)):
        raise ValueError("Post-question Agent ping failed")
    return socket, response["question"]["question_id"]


def submit_answer(socket, question_id, final):
    """Functionality: validate the real MCP answer workflow for the first positive case.
    Inputs: Agent socket, real current question and signed STT final kept only in memory.
    Outputs: safe scoring/persistence/next-step evidence; failures propagate without replay.
    Logic: invoke finish_current_answer once, then inspect its successful original DB response.
    Constraints: compare text using the documented edge-whitespace normalization contract.
    """
    from interviews.agent_models import AgentAnswer, AgentRequest

    identifier = str(uuid4())
    socket.send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": "tools/call",
                "params": {
                    "name": "finish_current_answer",
                    "arguments": {
                        "question_id": question_id,
                        "answer_text": final["text"],
                        "completion_receipt": final["completion_receipt"],
                    },
                },
            }
        )
    )
    while True:
        response = json.loads(socket.recv(timeout=120))
        if response.get("type") == "error" or "error" in response:
            raise RuntimeError("Real MCP answer failed")
        if response.get("id") == identifier:
            break
    payload = response["result"]["structuredContent"]
    answer, request = (
        AgentAnswer.objects.get(request_id=identifier),
        AgentRequest.objects.get(pk=identifier),
    )
    if (
        request.status != "succeeded"
        or answer.text != final["text"].strip()
        or answer.evaluation is None
        or answer.committed_state_version is None
    ):
        raise ValueError("MCP answer persistence contract failed")
    if payload["type"] == "question" and (
        payload["question"]["question_id"] == question_id or payload["last_evaluation"] is None
    ):
        raise ValueError("MCP did not advance with evaluation")
    if payload["type"] not in {"question", "finished"}:
        raise ValueError("Unexpected MCP next step")
    return {
        "succeeded": True,
        "evaluation_persisted": True,
        "answer_matches_trim_contract": True,
        "next_step_type": payload["type"],
    }


def main():
    """Functionality: generate and execute the immutable authored synthetic-audio suite.
    Inputs: CLI project/case/output paths, explicit HTTPS endpoint and production environment.
    Outputs: progress/report JSON, WAV/PCM fixtures and safe case-level console summaries.
    Logic: checksum source/cases/artifact, create one temporary authenticated owner, cache identical
    fixture clips within this run, execute sequentially, verify one MCP case and clean owned rows.
    Constraints: requires a fresh output directory; no server configuration changes or retries.
    Case-level failures are accumulated as separate experiments, then cause nonzero suite exit.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="https://47.239.50.129")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(args.project_root), str(args.project_root / "backend")]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.production")
    import django

    django.setup()
    from django.contrib.auth import (
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
        SESSION_KEY,
        get_user_model,
    )
    from django.contrib.sessions.backends.db import SessionStore
    from django.db import transaction
    from interviews.agent_models import AgentInterview, AgentTurn
    from interviews.speech.service import SpeechConfig

    source = args.cases.read_bytes()
    suite = json.loads(source)
    cases = suite["cases"]
    if len({case["id"] for case in cases}) != len(cases) or any(
        case["language"] not in {"en", "zh"}
        or case["mode"] not in {"single", "gap", "after_notice"}
        for case in cases
    ):
        raise ValueError("Synthetic case contract failed")
    config = SpeechConfig.load()
    model_path = Path(os.environ["ANSWER_COMPLETION_GATE_PATH"])
    report = {
        "suite_id": suite["suite_id"],
        "description": suite["description"],
        "case_sha256": hashlib.sha256(source).hexdigest(),
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model_manifest_sha256": hashlib.sha256(
            (model_path / "manifest.json").read_bytes()
        ).hexdigest(),
        "tts_model": config.tts_model,
        "tts_voice": config.tts_voice,
        "speech_host": urlsplit(config.tts_endpoint).hostname,
        "tts_languages": {"zh": "Chinese", "en": "English"},
        "tts_pcm_sample_rate": 24000,
        "stt_pcm_sample_rate": 16000,
        "resampler": "Browser area-average PCM16Resampler logic, JS rounding",
        "frame_bytes": FRAME_BYTES,
        "observation_seconds_after_audio": OBSERVATION_SECONDS,
        "automatic_retries": 0,
        "stage": "synthesis",
        "fixtures": [],
        "results": [],
    }
    report_path = args.output_dir / "report.json"
    write_report(report_path, report)
    fixtures = {}
    for case in cases:
        for text in case["clips"]:
            key = hashlib.sha256((case["language"] + "\0" + text).encode()).hexdigest()
            if key in fixtures:
                continue
            pcm24k, wav = synthesize_fixture(text, case["language"], config)
            pcm = browser_pcm(pcm24k)
            (args.output_dir / f"{key}.wav").write_bytes(wav)
            (args.output_dir / f"{key}.pcm16").write_bytes(pcm)
            fixtures[key] = pcm
            report["fixtures"].append(
                {
                    "id": key,
                    "language": case["language"],
                    "seconds": round(len(pcm) / 32000, 3),
                    "wav_sha256": hashlib.sha256(wav).hexdigest(),
                    "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                }
            )
            write_report(report_path, report)
            print(
                json.dumps(
                    {
                        "stage": "synthesized",
                        "language": case["language"],
                        "fixture_number": len(fixtures),
                    }
                ),
                flush=True,
            )
    user = get_user_model().objects.create_user(username=f"audio-suite-{uuid4().hex}")
    session = SessionStore()
    try:
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.save()
        cookie = f"sessionid={session.session_key}"
        report["stage"] = "capture"
        for index, case in enumerate(cases):
            print(
                json.dumps({"stage": "case_started", "id": case["id"], "index": index + 1}),
                flush=True,
            )
            with ExitStack() as stack:
                agent, question_id = (
                    start_interview(stack, cookie, args.base_url)
                    if index == 0
                    else (None, str(uuid4()))
                )
                clips = [
                    fixtures[hashlib.sha256((case["language"] + "\0" + text).encode()).hexdigest()]
                    for text in case["clips"]
                ]
                try:
                    result, final = capture_case(
                        case,
                        clips,
                        cookie,
                        question_id,
                        args.base_url.replace("https://", "wss://") + "/ws/speech/stt/",
                        user.pk,
                    )
                    if index == 0 and result["passed"]:
                        result["mcp"] = submit_answer(agent, question_id, final)
                except Exception as exc:
                    result = {
                        "id": case["id"],
                        "language": case["language"],
                        "category": case["category"],
                        "passed": False,
                        "exception_type": type(exc).__name__,
                    }
                report["results"].append(result)
                write_report(report_path, report)
                print(
                    json.dumps(
                        {
                            key: result.get(key)
                            for key in (
                                "id",
                                "passed",
                                "observed_events",
                                "receipt_present",
                                "errors",
                                "exception_type",
                            )
                        }
                    ),
                    flush=True,
                )
    finally:
        try:
            with transaction.atomic():
                AgentTurn.objects.filter(interview__owner=user).delete()
                AgentInterview.objects.filter(owner=user).delete()
                user.delete()
        finally:
            if session.session_key:
                session.delete()
        report["temporary_records_cleaned"] = True
        write_report(report_path, report)
    report["stage"] = "complete"
    report["passed_cases"] = sum(result["passed"] for result in report["results"])
    report["total_cases"] = len(cases)
    report["passed"] = report["passed_cases"] == len(cases) and report["results"][0].get(
        "mcp", {}
    ).get("succeeded", False)
    write_report(report_path, report)
    print(
        json.dumps(
            {
                "stage": "complete",
                "passed_cases": report["passed_cases"],
                "total_cases": len(cases),
                "passed": report["passed"],
            }
        ),
        flush=True,
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
