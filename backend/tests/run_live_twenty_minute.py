"""Responsibilities: Run a configurable real-time backend interview with Codex-authored answers.
Implementation: Use private SQLite, fresh or reused resume content, real session handlers and
provider SDKs; observe usage without changing interview policy. Read answers from a local queue.
By default retain real-time answer decisions and exclude background scoring/reporting through
a test-only composition. Use --with-evaluation to opt into the full scoring workflow.
Related Modules: run_full_preparation_benchmark supplies preparation instrumentation; AgentSession
and InterviewIOGateway execute normal commands. Browser, voice and PDF sandbox are excluded.

Declaration Index:
- LiveAudit: Add monotonic timing, durable full traces and checkpoints to preparation auditing.
- LiveAudit.__init__: Initialize the run clock and turn-scoped measurement state.
- LiveAudit.logical_call: Add the originating question and operation to structured-call records.
- LiveAudit.api_request: Record actual SDK request wall intervals and original call attribution.
- LiveAudit.trace: Persist every production trace privately without printing its payload.
- LiveAudit.progress: Record normal backend progress events and measured stage completions.
- LiveAudit.save: Atomically checkpoint the current run and readable dialogue.
- LiveAudit.candidate_answer: Await a Codex-written answer and realistic reading/speaking time.
- run_interview: Exercise the normal command lifecycle for the configured session duration.
- run_interview.command: Reserve, bind, execute, publish and complete a real backend command.
- run_interview.command.deliver: Complete the owned request with the gateway receipt.
- load_reused_resume: Read an existing ready profile and reviewed rows without model calls.
- main: Prepare a PDF or reuse a database snapshot and run a configurable real-time interview.
- main.observe_rag_start: Measure immediate RAG startup and attach a nonblocking readiness observer.
- main.observe_rag_start.observe_ready: Observe child readiness independently of interview requests.

Variable Index:
- ROOT: Repository root used for the supplied PDF and private output artifacts.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from time import perf_counter
from unittest.mock import patch
from uuid import UUID, uuid4

from run_full_preparation_benchmark import Audit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]


class LiveAudit(Audit):
    """Keep private evidence and original context, including concurrent background work."""

    def __init__(self, output):
        """Own an elapsed clock, checkpoints, manual answer queue and measured backend stages."""
        super().__init__(output)
        self.started = perf_counter()
        self.stage_starts = {}
        self.report.update(
            turns=[],
            progress=[],
            exceptions=[],
            configured_minutes=20,
            scope="Fresh full CV upload and question preparation, then a 20-minute real backend "
            "interview. Codex writes each candidate answer through a local file queue. "
            "Synthetic candidate details are test fixtures, not claims about the resume owner. "
            "Real answer analysis, RAG, planning, generation and quality checks are retained; "
            "background scoring and final assessment reports are excluded by default. "
            "Safety review, browser/voice, network WebSocket transport and PDF sandbox excluded.",
            evaluation_tested=False,
            report_generated=False,
        )
        (output / "answers").mkdir()

    def logical_call(self, actual, provider, prompt, data, schema):
        """Attach stable model-call context before delegating the original structured invocation."""
        from agents.model_calls import current_model_call

        call = current_model_call.get()
        if (
            self.phase == "interview"
            and not self.report.get("evaluation_tested", False)
            and (
                schema.__module__.startswith("evaluation.")
                or schema.__name__ in {"AnswerAssessment", "ReportNarrative"}
                or (call is not None and call.operation == "assessment")
            )
        ):
            raise AssertionError("Scoring/report model call reached a question-only test")
        token = self.scope.set(
            {
                **(self.scope.get() or {}),
                "model_question_id": call.question_id if call else None,
                "model_operation": call.operation if call else None,
            }
        )
        try:
            return super().logical_call(actual, provider, prompt, data, schema)
        finally:
            self.scope.reset(token)

    def api_request(self, actual, client, *args, **kwargs):
        """Observe real request intervals; worker-local labels survive background overlap."""
        started = perf_counter() - self.started
        logical_id = self.logical.get()
        labels = self.labels()
        try:
            return super().api_request(actual, client, *args, **kwargs)
        finally:
            own = [
                r
                for r in self.report["api_requests"]
                if r["logical_call_id"] == logical_id and "started_elapsed" not in r
            ]
            if own:
                entry = own[-1]
                entry.update(
                    started_elapsed=round(started, 6),
                    finished_elapsed=round(perf_counter() - self.started, 6),
                    **labels,
                )

    def trace(self, event, data):
        """Save full production diagnostics privately, including planning proposals and state."""
        entry = {
            "event": event,
            "elapsed": round(perf_counter() - self.started, 6),
            **self.labels(),
            **data,
        }
        with self.lock:
            self.report["traces"].append(entry)
            with (self.output / "traces.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    async def progress(self, data):
        """Observe backend stage timing without changing request handling or delivery."""
        entry = {**self.labels(), **data, "elapsed": round(perf_counter() - self.started, 6)}
        key = (entry.get("turn"), data.get("stage"))
        if data.get("state") == "running":
            self.stage_starts[key] = perf_counter()
        elif key in self.stage_starts:
            entry["seconds"] = round(perf_counter() - self.stage_starts.pop(key), 6)
        self.report["progress"].append(entry)

    def save(self):
        """Atomically save recoverable metrics and the current question/answer transcript."""
        temporary = self.output / "result.pending.json"
        temporary.write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        temporary.replace(self.output / "result.json")
        lines = [
            f"# Live {self.report['configured_minutes']}-minute interview transcript",
            "",
            self.report["scope"],
            "",
        ]
        for turn in self.report["turns"]:
            lines.extend(
                [
                    f"## Question {turn['number']}",
                    "",
                    turn["question"]["text"],
                    "",
                    turn.get("answer", "Awaiting Codex answer."),
                    "",
                    f"Source: {turn.get('generation_reason')}; "
                    f"action: {turn['question'].get('dialogue_action')}; "
                    f"submit-to-response: {turn.get('advance_seconds')} seconds.",
                    "",
                ]
            )
        (self.output / "transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def candidate_answer(self, turn, shown_at, minimum_seconds):
        """Read one authored answer; waiting counts as actual candidate time on the real clock."""
        target = self.output / "answers" / f"answer_{turn['number']:03d}.json"
        (self.output / "current_question.json").write_text(
            json.dumps({"answer_file": str(target), **turn}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.emit(
            "awaiting_codex_answer",
            {
                "number": turn["number"],
                "question_id": turn["question"]["question_id"],
                "answer_file": str(target),
            },
        )
        heartbeat = perf_counter()
        while not target.exists():
            await asyncio.sleep(0.5)
            if perf_counter() - heartbeat >= 25:
                self.emit(
                    "candidate_wait",
                    {"number": turn["number"], "wait_seconds": round(perf_counter() - shown_at, 1)},
                )
                heartbeat = perf_counter()
            if perf_counter() - shown_at > 240:
                raise TimeoutError("Codex answer queue deadline exceeded")
        answer = json.loads(target.read_text(encoding="utf-8"))
        if answer["question_id"] != turn["question"]["question_id"] or not answer["text"].strip():
            raise ValueError("Answer file does not match the pending question")
        if answer["text"].count("?") > max(10, len(answer["text"]) // 4):
            raise ValueError("Candidate answer appears corrupted by the input encoding")
        while perf_counter() - shown_at < minimum_seconds:
            await asyncio.sleep(min(1, minimum_seconds - (perf_counter() - shown_at)))
        turn["candidate_seconds"] = round(perf_counter() - shown_at, 3)
        return answer["text"]


async def run_interview(audit, owner_id, version_id, minimum_seconds):
    """Run normal backend commands with real time, private state and manually authored answers."""
    from interviews.agent_records import complete_request, fail_request, reserve_request
    from interviews.agent_safety import InterviewIOGateway
    from interviews.agent_socket import Answer, Finish, Start
    from interviews.api.resume_versions import resolve_resume_version
    from live_question_session import create_live_session

    session = create_live_session(include_evaluation=audit.report.get("evaluation_tested", False))
    session.emit_event = audit.progress
    gateway = InterviewIOGateway(
        session.interview_id, owner_id=owner_id, connection_id="live-twenty-" + uuid4().hex
    )
    report = audit.report
    report["interview_id"] = session.interview_id
    audit.phase = "interview"
    audit.scope.set({"phase": "interview", "turn": 0})

    async def command(cmd):
        """Use the same ownership, request, gateway and completion boundaries as real transport."""
        source_id = getattr(cmd, "resume_version_id", None)
        cmd = await resolve_resume_version(cmd, owner_id)
        await reserve_request(
            session.interview_id, cmd, owner_id=owner_id, resume_version_id=source_id
        )
        try:
            cmd = await gateway.bind_input(cmd)
            result = await {
                "start": session.start,
                "answer": session.answer,
                "finish": session.finish,
            }[cmd.type](cmd)

            async def deliver(payload, receipt):
                """Persist the original gateway-approved response and receipt before returning."""
                await complete_request(
                    session.interview_id,
                    cmd.request_id,
                    payload,
                    receipt=receipt,
                    owner_id=owner_id,
                )
                return payload

            return await gateway.publish(result, deliver)
        except BaseException:
            await fail_request(session.interview_id, cmd.request_id)
            raise

    live_started = None
    try:
        started = perf_counter()
        message = await command(
            Start(
                type="start",
                request_id=uuid4(),
                resume_version_id=UUID(str(version_id)),
                duration_minutes=report["configured_minutes"],
                job_title="AI / Software Engineer",
            )
        )
        report["first_question_start_seconds"] = round(perf_counter() - started, 6)
        report["first_question"] = message
        live_started = perf_counter()
        report["live_started_elapsed"] = round(live_started - audit.started, 6)
        while message["type"] == "question":
            shown_at = perf_counter()
            question = message["question"]
            number = len(report["turns"]) + 1
            action = session.action.model_dump(mode="json")
            context = await session.app.repository.get_interview_context(session.interview_id)
            turn = {
                "number": number,
                "question": question,
                "action": action,
                "generation_reason": action.get("decision_trace", {})
                .get("details", {})
                .get("generation_reason", "FIXED_INTRODUCTION"),
                "question_shown_elapsed": round(shown_at - audit.started, 6),
                "interview_elapsed_at_question": round(shown_at - live_started, 3),
                "plan_before": context.plan.model_dump(mode="json"),
            }
            report["turns"].append(turn)
            audit.scope.set({"phase": "interview", "turn": number})
            audit.save()
            turn["answer"] = await audit.candidate_answer(turn, shown_at, minimum_seconds)
            audit.save()
            started = perf_counter()
            turn["submitted_elapsed"] = round(started - audit.started, 6)
            finishing = started - live_started >= report["configured_minutes"] * 60
            schema = Finish if finishing else Answer
            message = await command(
                schema(
                    type="finish" if finishing else "answer",
                    request_id=uuid4(),
                    question_id=question["question_id"],
                    answer_text=turn["answer"],
                )
            )
            turn["advance_seconds"] = round(perf_counter() - started, 6)
            turn["returned_elapsed"] = round(perf_counter() - audit.started, 6)
            turn["next_type"] = message["type"]
            turn["response"] = message
            context = await session.app.repository.get_interview_context(session.interview_id)
            turn["state_after"] = context.state.model_dump(mode="json")
            turn["plan_after"] = context.plan.model_dump(mode="json")
            turn["plan_history_after"] = [p.model_dump(mode="json") for p in context.plan_history]
            report["live_seconds"] = round(perf_counter() - live_started, 3)
            audit.emit(
                "turn_completed",
                {
                    "number": number,
                    "advance_seconds": turn["advance_seconds"],
                    "next_type": message["type"],
                    "live_seconds": report["live_seconds"],
                },
            )
            audit.save()
        report["final_response"] = message
        report["report_generated"] = bool(message.get("result", {}).get("final_report"))
        report["status"] = "completed" if message["type"] == "finished" else "unexpected_response"
    finally:
        if live_started is not None:
            report["live_seconds"] = round(perf_counter() - live_started, 3)
        try:
            context = await session.app.repository.get_interview_context(session.interview_id)
            report["final_context"] = context.model_dump(mode="json")
        except Exception as exc:
            report["context_audit_error"] = type(exc).__name__
        await session.close_background()
        session.close()
        await gateway.aclose()
        audit.save()


def load_reused_resume(audit, source, version_id):
    """Validate immutable cached profile content and expose existing rows without preparation."""
    from interviews.resume_models import ResumeVersion
    from interviews.resume_profile import read_profile

    from agents.policies.topic_selector import TopicSelector

    versions = (
        ResumeVersion.objects.filter(pk=version_id) if version_id else ResumeVersion.objects.all()
    )
    if versions.count() != 1:
        raise ValueError("Select one existing resume with --resume-version-id")
    version = versions.get()
    if not version.can_interview:
        raise ValueError("The reused resume does not have a current ready profile")
    profile, _ = read_profile(version, candidate_id=f"resume-{version.pk}")
    audit.report.update(
        source_database=str(source),
        reused_resume=True,
        resume_version_id=str(version.pk),
        resume_parse_seconds=0,
        preparation_seconds=0,
        preparation_status=version.question_preparation_status,
        projects=[
            {
                "name": p.name,
                "project_id": p.project_id,
                "topics": [t.model_dump(mode="json") for t in TopicSelector().candidates(p)],
            }
            for p in profile.projects
        ],
        prepared_rows=list(
            version.prepared_questions.values(
                "id",
                "project_id",
                "topic_key",
                "kind",
                "status",
                "error_code",
                "content",
            )
        ),
    )
    return version


def main():
    """Run an authorized session with fresh preparation or an isolated copy of existing data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-answer-seconds", type=float, default=40)
    parser.add_argument("--duration-minutes", type=int, default=20)
    parser.add_argument("--reuse-database", type=Path)
    parser.add_argument("--resume-version-id")
    parser.add_argument(
        "--with-evaluation",
        action="store_true",
        help="Also test background scoring and final assessment reports (additional API cost).",
    )
    args = parser.parse_args()
    output = (
        ROOT
        / "output"
        / (f"live_{args.duration_minutes}min_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    output.mkdir(parents=True)
    if args.reuse_database:
        source = args.reuse_database.resolve(strict=True)
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(output / "benchmark.sqlite3") as target:
                original.backup(target)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(48))
    os.environ["INTERVIEW_DB_PATH"] = str(output / "benchmark.sqlite3")
    os.environ["QUESTION_PREPARATION_EXECUTION"] = "disabled"
    os.environ["AI_SECURITY_ENABLED"] = "false"
    os.environ["RAG_ENABLED"] = "true"
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")
    audit = LiveAudit(output)
    report = audit.report
    report["configured_minutes"] = args.duration_minutes
    report["evaluation_tested"] = args.with_evaluation
    report["report_generated"] = False
    report["scope"] = (
        f"Real {args.duration_minutes}-minute backend interview; "
        + (
            "reuse existing canonical resume and prepared questions from a private database copy. "
            if args.reuse_database
            else "fresh full CV upload and question preparation. "
        )
        + "Codex authors each candidate answer. Candidate details are synthetic test fixtures. "
        "Production dialogue, RAG and model policies are unchanged. "
        + (
            "Background scoring and assessment reports enabled. "
            if args.with_evaluation
            else "Question-only test: real answer analysis retained; background scoring, "
            "assessment drain and final score/narrative reports excluded. "
        )
        + "Safety review, browser, voice, network WebSocket transport and PDF sandbox excluded."
    )
    snapshot = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ("agents", "app", "evaluation", "shared", "backend/interviews")
        for p in (ROOT / folder).rglob("*.py")
    }
    (output / "source_snapshot.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    if args.reuse_database:
        report["source_database_sha256_before"] = hashlib.sha256(source.read_bytes()).hexdigest()
    audit.emit(
        "run_started",
        {
            "output": str(output),
            "minutes": args.duration_minutes,
            "reused_resume": bool(args.reuse_database),
        },
    )
    import django

    django.setup()
    from asgiref.sync import async_to_sync
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import connections
    from django.test import override_settings
    from interviews import question_preparation
    from interviews.resume_models import ResumeVersion
    from interviews.resume_pdf import extract_pdf
    from interviews.resume_profile import read_profile
    from rest_framework.test import APIClient

    from agents.model_calls import safe_error_details
    from agents.policies.topic_selector import TopicSelector
    from agents.routing.rag_router import RAGRouter
    from agents.tracing import trace_sink
    from app.adapters import rag as rag_runtime
    from app.adapters.rag import close_shared_rag
    from app.providers.llm import OpenAILLM

    call_command("migrate", verbosity=0, interactive=False)
    version = (
        load_reused_resume(audit, source, args.resume_version_id) if args.reuse_database else None
    )
    owner = (
        version.owner
        if version
        else get_user_model().objects.create_user(username="live-twenty-minute-test")
    )
    client = APIClient()
    client.force_authenticate(owner)
    provider = OpenAILLM()
    report.update(
        model=provider.model,
        provider=provider.provider,
        source_resume="Wang_Shunyao_CV.pdf",
        evaluation_mode=os.getenv("EVALUATION_MODE", "shadow")
        if args.with_evaluation
        else "questions_only",
        minimum_candidate_seconds=args.min_answer_seconds,
    )
    call, chat_type = OpenAILLM.__call__, type(provider.client.chat.completions)
    chat, response_type = chat_type.create, type(provider.client.responses)
    parse = response_type.parse
    prepare, retrieve = question_preparation.prepare_one, RAGRouter._retrieve_one
    warmup = question_preparation.get_shared_rag
    start_rag = rag_runtime.start_shared_rag

    def observe_rag_start():
        """Measure the nonblocking startup hook and observe child readiness independently."""
        started = perf_counter()
        port = start_rag()
        entry = {
            "started_elapsed": round(started - audit.started, 6),
            "startup_return_seconds": round(perf_counter() - started, 6),
            "ready_at_return": port.is_ready if port is not None else False,
            "worker_pid": port._process.pid if port is not None else None,
        }
        report["rag_initialization"].append(entry)
        audit.emit("rag_background_started", entry.copy())

        def observe_ready():
            """A daemon observer cannot hold the interview or server shutdown open."""
            if port is None:
                return
            ready = port._startup_done.wait(120) and port.is_ready
            entry.update(
                available=ready,
                warmup_seconds=round(perf_counter() - started, 3),
            )
            audit.emit("rag_background_ready", entry.copy())

        Thread(target=observe_ready, name="live-rag-observer", daemon=True).start()
        return port

    token = trace_sink.set(audit.trace)
    try:
        with (
            override_settings(ALLOWED_HOSTS=["testserver"]),
            patch.object(
                OpenAILLM,
                "__call__",
                lambda p, prompt, data, schema: audit.logical_call(call, p, prompt, data, schema),
            ),
            patch.object(
                chat_type, "create", lambda c, *a, **kw: audit.api_request(chat, c, *a, **kw)
            ),
            patch.object(
                response_type, "parse", lambda c, *a, **kw: audit.api_request(parse, c, *a, **kw)
            ),
            patch.object(
                question_preparation,
                "prepare_one",
                lambda *a, **kw: audit.prepare_one(prepare, *a, **kw),
            ),
            patch.object(
                RAGRouter, "_retrieve_one", lambda r, req: audit.retrieve(retrieve, r, req)
            ),
            patch.object(question_preparation, "get_shared_rag", lambda: audit.warmup(warmup)),
            patch.object(rag_runtime, "start_shared_rag", observe_rag_start),
        ):
            started = perf_counter()
            text = (
                ""
                if args.reuse_database
                else "\n\n".join(
                    p.text for p in extract_pdf((ROOT / "Wang_Shunyao_CV.pdf").read_bytes())
                )
            )
            audit.phase = "resume_parsing"
            response = (
                None
                if args.reuse_database
                else client.post("/api/resume-versions/", {"text": text}, format="json")
            )
            if response is not None and (
                response.status_code != 201 or not response.data["can_interview"]
            ):
                raise RuntimeError("Fresh resume parsing failed")
            version = (
                version
                if args.reuse_database
                else ResumeVersion.objects.get(pk=response.data["id"])
            )
            report["resume_parse_seconds"] = (
                0 if args.reuse_database else round(perf_counter() - started, 3)
            )
            report["resume_version_id"] = str(version.pk)
            profile, _ = read_profile(version, candidate_id=f"resume-{version.pk}")
            report["projects"] = [
                {
                    "name": p.name,
                    "project_id": p.project_id,
                    "topics": [t.model_dump(mode="json") for t in TopicSelector().candidates(p)],
                }
                for p in profile.projects
            ]
            audit.phase = "preparation"
            started = perf_counter()
            if not args.reuse_database:
                version = question_preparation.prepare_questions(version.pk, owner.pk, llm=provider)
            report["preparation_seconds"] = (
                0 if args.reuse_database else round(perf_counter() - started, 3)
            )
            report["preparation_status"] = version.question_preparation_status
            report["prepared_rows"] = list(
                version.prepared_questions.values(
                    "project_id", "topic_key", "kind", "status", "error_code", "content"
                )
            )
            audit.emit(
                "preparation_completed",
                {
                    "status": report["preparation_status"],
                    "seconds": report["preparation_seconds"],
                    "rows": len(report["prepared_rows"]),
                },
            )
            audit.save()
            async_to_sync(run_interview)(audit, owner.pk, version.pk, args.min_answer_seconds)
    except BaseException as exc:
        report.update(status="failed", failure=safe_error_details(exc))
        report["exceptions"].append(
            {"elapsed": round(perf_counter() - audit.started, 3), **safe_error_details(exc)}
        )
        audit.emit("run_failed", report["failure"])
        raise
    finally:
        trace_sink.reset(token)
        provider.client.close()
        async_to_sync(close_shared_rag)()
        connections.close_all()
        report["total_seconds"] = round(perf_counter() - audit.started, 3)
        if args.reuse_database:
            report["source_database_sha256_after"] = hashlib.sha256(source.read_bytes()).hexdigest()
        audit.save()
        audit.emit(
            "run_finished",
            {
                "status": report["status"],
                "output": str(output),
                "live_seconds": report.get("live_seconds"),
            },
        )


if __name__ == "__main__":
    main()
