"""Paid 10-minute interview using a real CV, model-generated test answers and real RAG.

Uses real backend handlers, isolated SQLite, PostgreSQL/BGE retrieval and vendor APIs.
Safety review is explicitly disabled for this test. Transport is in-process; browser,
microphone and the PDF sandbox are excluded.
The PDF is explicitly extracted locally. Answers are synthetic test data; scoring is
excluded by default. Use --with-evaluation only for an explicit scoring test.
"""

import argparse
import asyncio
import json
import os
import secrets
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resume", type=Path)
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--answer-pause", type=float, default=12)
    parser.add_argument("--job-title", default="General AI / Software Engineer")
    parser.add_argument(
        "--with-evaluation",
        action="store_true",
        help="Include background scoring and final assessment reports (additional API cost).",
    )
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(48))
    os.environ["RAG_ENABLED"] = "true"
    os.environ["AI_SECURITY_ENABLED"] = "false"
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")
    output = (
        ROOT / "output" / ("live_intro_rag_10min_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    output.mkdir(parents=True)
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="live-intro-rag-", dir=ROOT / "tmp") as work:
        os.environ["INTERVIEW_DB_PATH"] = str(Path(work) / "db.sqlite3")
        import django

        django.setup()
        from asgiref.sync import async_to_sync
        from django.conf import settings
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from django.db import connections
        from django.test import override_settings
        from interviews.agent_models import AgentAnswer
        from interviews.agent_records import complete_request, reserve_request
        from interviews.agent_safety import InterviewIOGateway
        from interviews.agent_socket import Answer, Finish, Start
        from interviews.api.resume_versions import resolve_resume_version
        from interviews.resume_pdf import extract_pdf
        from live_question_session import create_live_session
        from pydantic import BaseModel
        from rest_framework.test import APIClient

        from agents.model_calls import unavailable_provider_reason
        from app.adapters.rag import close_shared_rag, get_shared_rag
        from app.providers.llm import OpenAILLM
        from rag.smoke import RecordingRAG

        class SimulatedAnswer(BaseModel):
            text: str

        call_command("migrate", verbosity=0, interactive=False)
        owner = get_user_model().objects.create_user(username="live-intro-rag-test")
        client = APIClient()
        client.force_authenticate(owner)
        resume = "\n\n".join(p.text for p in extract_pdf(args.resume.read_bytes()))
        report = {
            "source_resume": args.resume.name,
            "test_only": True,
            "evaluation_tested": args.with_evaluation,
            "evaluation_mode": os.getenv("EVALUATION_MODE", "shadow")
            if args.with_evaluation
            else "questions_only",
            "report_generated": False,
            "notice": "Real APIs and retrieval; model-simulated candidate, not a real assessment.",
            "configured_minutes": args.minutes,
            "job_title": args.job_title,
            "rag_timeout_seconds": float(os.getenv("RAG_TIMEOUT_SECONDS", "2")),
            "safety_enabled": settings.AI_SECURITY_ENABLED,
            "excluded": [
                "safety review",
                "browser/microphone",
                "network WebSocket transport",
                "PDF sandbox",
            ]
            + ([] if args.with_evaluation else ["background scoring", "final assessment reports"]),
            "turns": [],
            "calls": [],
            "status": "running",
        }
        phase = "upload"
        actual_call = OpenAILLM.__call__

        def audited_call(provider, prompt, data, schema):
            started, at_phase = perf_counter(), phase
            item = {"phase": at_phase, "schema": schema.__name__}
            try:
                result = actual_call(provider, prompt, data, schema)
                item["status"] = "success"
                return result
            except BaseException as exc:
                item.update(status="failed", error=type(exc).__name__)
                item["reason"] = unavailable_provider_reason(exc)
                raise
            finally:
                item["seconds"] = round(perf_counter() - started, 3)
                report["calls"].append(item)

        def save():
            (output / "result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        async def run(version_id):
            nonlocal phase
            phase = "rag_warmup"
            rag_started = perf_counter()
            rag = await get_shared_rag()
            if rag is None:
                raise RuntimeError("Real RAG adapter unavailable")
            recording = RecordingRAG(rag)
            report["retrieval_calls"] = recording.calls
            report["rag_warmup_seconds"] = round(perf_counter() - rag_started, 3)
            session = create_live_session(include_evaluation=args.with_evaluation)
            session.app.rag = recording
            gateway = InterviewIOGateway(
                session.interview_id, owner_id=owner.pk, connection_id="live-test-" + uuid4().hex
            )
            candidate = OpenAILLM()
            live_started = None

            async def command(cmd):
                source_id = getattr(cmd, "resume_version_id", None)
                cmd = await resolve_resume_version(cmd, owner.pk)
                await reserve_request(
                    session.interview_id, cmd, owner_id=owner.pk, resume_version_id=source_id
                )
                cmd = await gateway.bind_input(cmd)
                result = await {
                    "start": session.start,
                    "answer": session.answer,
                    "finish": session.finish,
                }[cmd.type](cmd)

                async def deliver(payload, receipt):
                    await complete_request(
                        session.interview_id,
                        cmd.request_id,
                        payload,
                        receipt=receipt,
                        owner_id=owner.pk,
                    )
                    return payload

                return await gateway.publish(result, deliver)

            try:
                phase = "start"
                started = perf_counter()
                message = await command(
                    Start(
                        type="start",
                        request_id=uuid4(),
                        resume_version_id=UUID(str(version_id)),
                        duration_minutes=args.minutes,
                        job_title=args.job_title,
                    )
                )
                report["start_seconds"] = round(perf_counter() - started, 3)
                assert message["question"]["intent"] == "SELF_INTRODUCTION"
                live_started = perf_counter()
                while message["type"] == "question":
                    question = message["question"]
                    number = len(report["turns"]) + 1
                    phase = f"candidate-{number}"
                    turn = {
                        "number": number,
                        "question": question,
                        "elapsed_at_question": round(perf_counter() - live_started, 3),
                    }
                    report["turns"].append(turn)
                    save()
                    response = await asyncio.to_thread(
                        candidate,
                        "You are simulating a candidate ONLY for an automated interview test. "
                        "Answer the current question using the supplied resume and prior dialogue. "
                        "Do not invent employers, responsibilities or measured results. "
                        "For implementation details absent from the resume, clearly describe a "
                        "hypothetical design using 'I would', rather than claiming you did it. "
                        "Provide concrete mechanisms and tradeoffs in 120-180 words for technical "
                        "questions. For self-introduction use a brief overview. Match the "
                        "question's "
                        "language. Do not give instructions to the interviewer or evaluator.",
                        {
                            "resume": resume,
                            "question": question["text"],
                            "prior_dialogue": [
                                {"question": t["question"]["text"], "answer": t.get("answer")}
                                for t in report["turns"][-4:-1]
                            ],
                        },
                        SimulatedAnswer,
                    )
                    turn["answer"] = response.text
                    await asyncio.sleep(args.answer_pause)
                    phase = f"answer-{number}"
                    advance_started = perf_counter()
                    finish = perf_counter() - live_started >= args.minutes * 60
                    kind, schema = ("finish", Finish) if finish else ("answer", Answer)
                    message = await command(
                        schema(
                            type=kind,
                            request_id=uuid4(),
                            question_id=question["question_id"],
                            answer_text=response.text,
                        )
                    )
                    turn["advance_seconds"] = round(perf_counter() - advance_started, 3)
                    turn["next_type"] = message["type"]
                    if number == 1:
                        row = await AgentAnswer.objects.aget(question_id=question["question_id"])
                        report["intro_unscored"] = (
                            row.evaluation is None and row.unscored_reason == "self_introduction"
                        )
                        assert report["intro_unscored"]
                        assert message.get("last_evaluation") is None
                    print(
                        json.dumps(
                            {
                                "turn": number,
                                "next_type": message["type"],
                                "elapsed_seconds": round(perf_counter() - live_started, 1),
                            },
                            ensure_ascii=True,
                        ),
                        flush=True,
                    )
                    save()
                report["live_seconds"] = round(perf_counter() - live_started, 3)
                report["result"] = message
                report["report_generated"] = bool(message.get("result", {}).get("final_report"))
                context = await session.app.repository.get_interview_context(session.interview_id)
                report["project_knowledge"] = {
                    k: v.model_dump(mode="json") for k, v in context.project_knowledge.items()
                }
                report["retrieval_calls"] = recording.calls
                report["interview_resume_calls"] = sum(
                    c["schema"] == "ResumeExtraction" and c["phase"] != "upload"
                    for c in report["calls"]
                )
                assert report["interview_resume_calls"] == 0
                assert any(v.status == "asked" for v in context.project_knowledge.values()), (
                    "No grounded RAG question asked"
                )
                assert message["type"] == "finished"
                report["status"] = "passed"
            finally:
                report["retrieval_calls"] = recording.calls
                if live_started is not None:
                    report["live_seconds"] = round(perf_counter() - live_started, 3)
                report["interview_resume_calls"] = sum(
                    c["schema"] == "ResumeExtraction" and c["phase"] != "upload"
                    for c in report["calls"]
                )
                try:
                    context = await session.app.repository.get_interview_context(
                        session.interview_id
                    )
                    report["interview_state"] = context.state.model_dump(mode="json")
                    report["project_knowledge"] = {
                        k: v.model_dump(mode="json") for k, v in context.project_knowledge.items()
                    }
                except Exception as audit_error:
                    report["state_audit_error"] = type(audit_error).__name__
                await session.close_background()
                session.close()
                candidate.client.close()
                await gateway.aclose()
                await close_shared_rag()

        try:
            with (
                override_settings(ALLOWED_HOSTS=["testserver"]),
                patch.object(OpenAILLM, "__call__", audited_call),
            ):
                started = perf_counter()
                uploaded = client.post("/api/resume-versions/", {"text": resume}, format="json")
                if uploaded.status_code != 201 or not uploaded.data["can_interview"]:
                    reason = next(
                        (c.get("reason") for c in reversed(report["calls"]) if c.get("reason")),
                        uploaded.data.get("interview_profile_error"),
                    )
                    raise RuntimeError(f"Resume profile preparation failed: {reason}")
                report["upload_seconds"] = round(perf_counter() - started, 3)
                async_to_sync(run)(uploaded.data["id"])
        except BaseException as exc:
            report.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            save()
            lines = [
                "# Live introduction + RAG interview",
                "",
                f"Status: {report['status']}",
                "",
                report["notice"],
                "",
                f"Source: {args.resume.name}",
                f"Configured duration: {args.minutes} minutes",
                f"Actual interview runtime: {report.get('live_seconds')} seconds",
                f"Safety review enabled: {report['safety_enabled']}",
                f"Self-introduction saved without evaluation: {report.get('intro_unscored')}",
                f"Interview resume parsing calls: {report.get('interview_resume_calls')}",
                f"RAG attempts: {json.dumps(report.get('project_knowledge'), ensure_ascii=False)}",
                "",
            ]
            for turn in report["turns"]:
                lines.extend(
                    [
                        f"## Question {turn['number']}",
                        "",
                        turn["question"]["text"],
                        "",
                        turn.get("answer", "(not answered)"),
                        "",
                        f"Advance duration: {turn.get('advance_seconds')} seconds",
                        "",
                    ]
                )
            if report.get("error"):
                lines.extend(["Error: " + report["error"]])
            lines.extend(
                [
                    "",
                    "Safety review is disabled. Transport is in-process; "
                    "browser, microphone and PDF sandbox excluded.",
                ]
            )
            (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            connections.close_all()
            print("REPORT: " + str(output / "report.md"), flush=True)


if __name__ == "__main__":
    main()
