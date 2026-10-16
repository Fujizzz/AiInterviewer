"""Live paid smoke: text upload -> persisted canonical profile -> two parse-free first questions.

Run from repository root: .venv/Scripts/python backend/tests/run_resume_profile_smoke.py
By default uses a synthetic resume; --resume-pdf explicitly reads a supplied PDF locally with the
backend extraction algorithm before text upload (it does not test the Linux/WSL sandbox endpoint).
Uses an isolated temporary SQLite database. It calls the configured real
LLM, writes an audit report, and never writes user accounts/resumes to the normal backend database.
The HTTP API, ORM, parser, Agent and question quality checks are real. WebSocket transport, safety
review, PDF sandbox and RAG are outside this focused smoke (covered by their own tests).
"""

import argparse
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resume-pdf", type=Path, help="PDF to extract locally and test with real LLM"
    )
    parser.add_argument("--output-dir", type=Path, help="Directory for report.md and result.json")
    args = parser.parse_args()
    if args.resume_pdf is not None and not args.resume_pdf.is_file():
        parser.error("--resume-pdf must be an existing file")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(48))
    os.environ["RAG_ENABLED"] = "false"
    (ROOT / "tmp").mkdir(exist_ok=True)
    output = args.output_dir or ROOT / "output" / (
        "resume_profile_live_" + args.resume_pdf.stem
        if args.resume_pdf
        else "resume_profile_live_smoke"
    )
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="resume-profile-live-", dir=ROOT / "tmp") as work:
        os.environ["INTERVIEW_DB_PATH"] = str(Path(work) / "db.sqlite3")
        import django

        django.setup()
        from asgiref.sync import async_to_sync
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from django.db import connections
        from django.test import override_settings
        from interviews.agent_provider import BackendLLM
        from interviews.agent_records import reserve_request
        from interviews.agent_session import AgentSession
        from interviews.agent_socket import Start
        from interviews.api.resume_versions import resolve_resume_version
        from interviews.resume_models import ResumeVersion
        from rest_framework.test import APIClient

        from app.providers.llm import OpenAILLM

        call_command("migrate", verbosity=0, interactive=False)
        owner = get_user_model().objects.create_user(username="synthetic-resume-profile-smoke")
        client = APIClient()
        client.force_authenticate(owner)
        calls = []
        phase = "upload"
        original_call = OpenAILLM.__call__

        def audited_call(provider, prompt, data, schema):
            started = perf_counter()
            result = original_call(provider, prompt, data, schema)
            calls.append(
                {
                    "phase": phase,
                    "schema": schema.__name__,
                    "seconds": round(perf_counter() - started, 3),
                }
            )
            return result

        resume = (
            "Alex Chen\nSkills: Python, FastAPI, Redis, PostgreSQL, pytest.\n"
            "Projects\nLog Analysis Pipeline\n"
            "Built a Python log ingestion API using FastAPI. Implemented streaming parsing "
            "and bounded queues for backpressure. Stored normalized events in PostgreSQL "
            "and used Redis to cache frequent queries. Added pytest tests for malformed "
            "records and measured peak memory and p95 query latency on a synthetic workload."
        )
        report = {
            "scope": "real text-upload API, database and two Agent first questions",
            "excluded": ["WebSocket transport", "safety reviewer", "PDF extraction", "RAG"],
        }
        if args.resume_pdf is not None:
            from interviews.resume_pdf import extract_pdf

            started = perf_counter()
            pages = extract_pdf(args.resume_pdf.read_bytes())
            resume = "\n\n".join(page.text for page in pages)
            report["source_file"] = args.resume_pdf.name
            report["local_pdf_extraction"] = {
                "module": "interviews.resume_pdf.extract_pdf",
                "seconds": round(perf_counter() - started, 3),
                "pages": len(pages),
                "characters": len(resume),
            }
            report["scope"] = (
                "local real-PDF extraction, real text-upload API, DB and two Agent first questions"
            )
            report["excluded"] = [
                "WebSocket transport",
                "safety reviewer",
                "PDF sandbox endpoint",
                "RAG",
            ]
        try:
            with (
                override_settings(ALLOWED_HOSTS=["testserver"]),
                patch.object(OpenAILLM, "__call__", audited_call),
            ):
                started = perf_counter()
                response = client.post("/api/resume-versions/", {"text": resume}, format="json")
                assert response.status_code == 201, response.status_code
                assert response.data["can_interview"], response.data["interview_profile_error"]
                version_id = response.data["id"]
                report["upload_seconds"] = round(perf_counter() - started, 3)
                version = ResumeVersion.objects.get(pk=version_id)
                report["stored_profile"] = version.interview_profile
                report["interviews"] = []

                async def interview():
                    session = AgentSession(llm=BackendLLM())
                    try:
                        command = Start(
                            type="start", request_id=uuid4(), resume_version_id=version.pk
                        )
                        resolved = await resolve_resume_version(command, owner.pk)
                        await reserve_request(
                            session.interview_id,
                            resolved,
                            owner_id=owner.pk,
                            resume_version_id=version.pk,
                        )
                        started = perf_counter()
                        with patch(
                            "interviews.agent_session.parse_resume_profile",
                            side_effect=AssertionError("Interview must load database profile"),
                        ):
                            result = await session.start(resolved)
                        assert result["type"] == "question"
                        return {
                            "interview_id": session.interview_id,
                            "candidate_id": session.profile.candidate_id,
                            "seconds": round(perf_counter() - started, 3),
                            "question": result["question"]["text"],
                        }
                    finally:
                        session.close()

                for number in range(2):
                    phase = f"interview-{number + 1}"
                    report["interviews"].append(async_to_sync(interview)())
            report["calls"] = calls
            report["upload_resume_calls"] = sum(
                c["phase"] == "upload" and c["schema"] == "ResumeExtraction" for c in calls
            )
            report["interview_resume_calls"] = sum(
                c["phase"] != "upload" and c["schema"] == "ResumeExtraction" for c in calls
            )
            assert report["upload_resume_calls"] >= 1
            assert report["interview_resume_calls"] == 0
            report["passed"] = True
            (output / "result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            lines = [
                "# Resume profile live smoke",
                "",
                "Profile reuse flow passed: true",
                "",
                f"Upload + real semantic parsing: {report['upload_seconds']} seconds",
                f"Upload ResumeExtraction calls: {report['upload_resume_calls']}",
                "ResumeExtraction calls across two interviews: 0",
                "",
            ]
            if args.resume_pdf is not None:
                extracted = report["local_pdf_extraction"]
                lines.extend(
                    [
                        f"Input PDF: {args.resume_pdf.name}",
                        f"Local extraction: {extracted['pages']} pages, "
                        f"{extracted['characters']} characters, "
                        f"{extracted['seconds']} seconds",
                        "",
                        "This smoke explicitly uses the real local extraction algorithm followed "
                        "by the text-upload API. The PDF upload/sandbox endpoint is outside "
                        "its scope.",
                        "",
                    ]
                )
            profile = report["stored_profile"]["candidate_profile"]
            lines.extend(
                [
                    f"Stored skills: {len(profile['skills'])}",
                    f"Stored projects/experiences: {len(profile['projects'])}",
                    "",
                ]
            )
            for project in profile["projects"]:
                lines.extend([f"- {project['name']} ({project['domain']})"])
            lines.append("")
            for index, item in enumerate(report["interviews"], 1):
                lines.extend(
                    [
                        f"## Interview {index}",
                        "",
                        f"First question: {item['question']}",
                        f"Startup + generation + quality: {item['seconds']} seconds",
                        "",
                    ]
                )
            lines.extend(
                [
                    ("User-supplied PDF" if args.resume_pdf else "Synthetic resume")
                    + "; isolated temporary SQLite database.",
                    "Real configured LLM; no model-output stubs.",
                    "Excluded: " + ", ".join(report["excluded"]) + ".",
                ]
            )
            (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(
                json.dumps(
                    {
                        "passed": True,
                        "upload_seconds": report["upload_seconds"],
                        "interview_resume_calls": 0,
                        "report": str(output / "report.md"),
                    }
                )
            )
        finally:
            connections.close_all()


if __name__ == "__main__":
    main()
