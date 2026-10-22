"""Responsibilities: Run a limited real-API preparation and cache reuse smoke test.
Implementation: Exercise the production preparation path with explicit isolated test
boundaries.
Related Modules: question_preparation, resume_profile and InterviewAgentService supply
production behavior.

Declaration Index:
- main: Run one paid CV-topic preparation and reuse check in isolated SQLite; save results and
  close clients.
- main.audited: Bound and record real model calls without credentials, including sanitized
  validation diagnostics.
- main.exercise: Publish cached opening and knowledge questions through the Agent core with
  scripted feedback.

Variable Index:
- ROOT: Repository root for the isolated database, CV fixture and private test reports.
"""

import argparse
import json
import os
import re
import secrets
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]


def main():
    """Run one paid CV-topic preparation and reuse check in isolated SQLite; save results and
    close clients.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reuse-report", type=Path, help="Reuse ready content from an earlier private smoke report"
    )
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(48))
    os.environ["QUESTION_PREPARATION_EXECUTION"] = "disabled"
    os.environ["AI_SECURITY_ENABLED"] = "false"
    os.environ["RAG_ENABLED"] = "true"
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")
    output = (
        ROOT
        / "output"
        / ("prepared_questions_smoke_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    output.mkdir(parents=True)
    (ROOT / "tmp").mkdir(exist_ok=True)
    report = {
        "status": "running",
        "scope": "One ReAct topic projected from Wang_Shunyao_CV.pdf; "
        "real generation, quality review, PostgreSQL retrieval and SQLite preparation rows. "
        "Agent turns use an in-memory repository and scripted answer feedback. "
        "No upload parser, scoring, safety reviewer, browser or full interview.",
        "calls": [],
    }
    with tempfile.TemporaryDirectory(prefix="prepared-smoke-", dir=ROOT / "tmp") as work:
        os.environ["INTERVIEW_DB_PATH"] = str(Path(work) / "db.sqlite3")
        import django

        django.setup()
        from asgiref.sync import async_to_sync
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from django.db import connections
        from interviews.agent_models import AgentInterview
        from interviews.question_preparation import load_bound_questions, prepare_questions
        from interviews.resume_models import (
            INTERVIEW_PROFILE_VERSION,
            PreparedResumeQuestion,
            ResumeVersion,
        )
        from interviews.resume_pdf import extract_pdf
        from interviews.resume_profile import StoredResumeDocument, text_hash

        from agents.model_calls import safe_error_details, unavailable_provider_reason
        from agents.orchestrator import InterviewAgentService
        from agents.question.prepared import profile_fingerprint, review_version
        from agents.tracing import trace_sink
        from app.adapters.llm import ProviderLLMAdapter
        from app.adapters.rag import close_shared_rag, get_shared_rag
        from app.application import DEFAULT_COMPETENCY_IMPORTANCE
        from app.parsing.resume import document_profile
        from app.providers.llm import OpenAILLM
        from shared.contracts import (
            AnswerAnalysis,
            CandidateAnswer,
            EvaluationFeedback,
            InitializeInterviewRequest,
            InterviewStage,
            JobProfile,
        )
        from shared.contracts.prepared import PreparedQuestion
        from shared.resume_contract import ResumeExtraction
        from tests.agent.mocks import InMemoryRepository

        call_command("migrate", verbosity=0, interactive=False)
        owner = get_user_model().objects.create_user(username="prepared-live-smoke")
        text = "\n".join(p.text for p in extract_pdf((ROOT / "Wang_Shunyao_CV.pdf").read_bytes()))
        index = text.casefold().find("react")
        if index < 0:
            raise ValueError("The selected CV does not contain ReAct")
        clean = " ".join(text.split())
        match = re.search(r"Designed.{0,100}ReAct.{0,200}?topics", clean)
        if match is None:
            raise ValueError("The selected CV does not contain the expected ReAct project claim")
        excerpt = match.group(0)
        # This explicitly projected excerpt isolates question preparation, not model extraction.
        document = ResumeExtraction(
            section_lines={"projects": [1]},
            projects=[
                {
                    "name": "IntelliHireAI",
                    "source_lines": [1],
                    "description": excerpt,
                    "technologies": ["ReAct"],
                    "claims": [excerpt],
                }
            ],
        )
        profile, _ = document_profile(document, excerpt, candidate_id="prepared-smoke")
        version = ResumeVersion.objects.create(
            owner=owner,
            text=excerpt,
            status="ready",
            interview_profile_status="ready",
            interview_profile_hash=text_hash(excerpt),
            interview_profile_version=INTERVIEW_PROFILE_VERSION,
            interview_profile=StoredResumeDocument(document=document).model_dump(mode="json"),
        )
        actual = OpenAILLM.__call__
        provider = OpenAILLM()
        report["model"] = provider.model
        report["provider"] = provider.provider

        def audited(client, prompt, data, schema):
            """Bound and record real model calls without credentials, including sanitized
            validation diagnostics.
            """
            if len(report["calls"]) >= 16:
                raise RuntimeError("Small smoke API-call limit reached")
            entry = {"schema": schema.__name__}
            report["calls"].append(entry)
            started = perf_counter()
            try:
                result = actual(client, prompt, data, schema)
                entry["status"] = "success"
                if schema.__name__ == "PreparedUseDecision":
                    entry["verdict"] = result.model_dump(mode="json")
                return result
            except Exception as exc:
                entry.update(
                    status="failed",
                    error=type(exc).__name__,
                    reason=unavailable_provider_reason(exc),
                )
                entry["details"] = safe_error_details(exc)
                raise
            finally:
                entry["seconds"] = round(perf_counter() - started, 3)
                print(json.dumps(entry), flush=True)

        async def exercise(items):
            """Publish cached opening and knowledge questions through the Agent core with scripted
            feedback.
            """
            repository = InMemoryRepository()
            llm = ProviderLLMAdapter(provider)
            service = InterviewAgentService(
                repository=repository, llm=llm, prepared_questions=items
            )
            iid = "prepared-smoke-interview"
            before = len(report["calls"])
            started = perf_counter()
            first = (
                await service.initialize_interview(
                    InitializeInterviewRequest(
                        interview_id=iid,
                        candidate_profile=profile,
                        job_profile=JobProfile(
                            job_id="smoke",
                            title="AI Engineer",
                            competency_importance=DEFAULT_COMPETENCY_IMPORTANCE,
                        ),
                        duration_seconds=600,
                        enabled_stages=[InterviewStage.PROJECT_DEEP_DIVE],
                    )
                )
            ).first_action
            report["opening"] = {
                "question": first.question.text,
                "seconds": round(perf_counter() - started, 3),
                "model_calls": len(report["calls"]) - before,
                "reason": first.decision_trace.details.get("generation_reason"),
            }
            assert first.decision_trace.details["generation_reason"] == "PREPARED_QUESTION"
            assert len(report["calls"]) == before
            # Scripted feedback isolates the new RAG/cached-content path from scoring latency.
            answer = CandidateAnswer(
                interview_id=iid,
                question_id=first.question.question_id,
                answer_id="scripted-answer",
                text="I implemented the backend tool dispatch interface and tested malformed "
                "tool responses. I can explain the theoretical agent mechanism separately.",
            )
            feedback = EvaluationFeedback(
                request_id="scripted-feedback",
                question_id=first.question.question_id,
                answer_relevance=0.8,
                evidence_strength=0.7,
                analysis=AnswerAnalysis(
                    status="substantive",
                    new_information=True,
                    missing_information=["Explain an underlying mechanism"],
                ),
            )
            before = len(report["calls"])
            started = perf_counter()
            action = await service.apply_evaluation_feedback(iid, feedback, answer=answer)
            report["knowledge"] = {
                "question": action.question.text if action.question else None,
                "seconds": round(perf_counter() - started, 3),
                "schemas": [c["schema"] for c in report["calls"][before:]],
                "reason": action.decision_trace.details.get("generation_reason"),
            }
            state = await repository.get_interview_context(iid)
            report["knowledge"]["cache_rejections"] = state.rejected_prepared_questions
            assert action.decision_trace.details["generation_reason"] == "PREPARED_QUESTION"
            assert report["knowledge"]["schemas"] == ["PreparedUseDecision"]
            state = await repository.get_interview_context(iid)
            assert state.project_knowledge[profile.projects[0].project_id].status == "asked"
            report["knowledge"]["references"] = state.project_knowledge[
                profile.projects[0].project_id
            ].model_dump(mode="json")

        try:
            rag = None if args.reuse_report else async_to_sync(get_shared_rag)()
            if rag is None and not args.reuse_report:
                raise RuntimeError("Real RAG unavailable")
            with patch.object(OpenAILLM, "__call__", audited):
                report["traces"] = []
                trace_token = trace_sink.set(
                    lambda event, data: (
                        report["traces"].append({"event": event, **data})
                        if event
                        in {"question.quality", "react.finished", "react.decision", "model.error"}
                        else None
                    )
                )
                started = perf_counter()
                if args.reuse_report:
                    old = json.loads(args.reuse_report.read_text(encoding="utf-8"))
                    for row in old["rows"]:
                        if row["status"] != "ready":
                            continue
                        item = PreparedQuestion.model_validate(row["content"])
                        assert item.review_version == review_version()
                        assert item.project_id == profile.projects[0].project_id
                        PreparedResumeQuestion.objects.create(
                            resume_version=version,
                            project_id=item.project_id,
                            topic_key=item.topic_key,
                            kind=item.kind,
                            status="ready",
                            profile_hash=profile_fingerprint(profile),
                            review_version=item.review_version,
                            content=item.model_dump(mode="json"),
                        )
                    report["preparation_reused_from"] = str(args.reuse_report)
                result = prepare_questions(version.pk, owner.pk, llm=provider, rag=rag)
                if result.question_preparation_status == "partial":
                    report["retried_partial_once"] = True
                    result = prepare_questions(version.pk, owner.pk, llm=provider, rag=rag)
                report["preparation_seconds"] = round(perf_counter() - started, 3)
                report["preparation_status"] = result.question_preparation_status
                report["preparation_error"] = result.question_preparation_error
                report["rows"] = list(
                    result.prepared_questions.values("kind", "status", "error_code", "content")
                )
                assert result.question_preparation_status == "ready", (
                    result.question_preparation_error
                )
                before = len(report["calls"])
                prepare_questions(version.pk, owner.pk, llm=provider, rag=rag)
                report["reuse_model_calls"] = len(report["calls"]) - before
                assert report["reuse_model_calls"] == 0
                binding = AgentInterview.objects.create(
                    id=uuid4(), owner=owner, resume_version=version, resume_text_snapshot=excerpt
                )
                items = async_to_sync(load_bound_questions)(binding.pk)
                assert len(items) == 2
                async_to_sync(exercise)(items)
            report["status"] = "passed"
        except Exception as exc:
            report.update(
                status="failed", error=type(exc).__name__, reason=unavailable_provider_reason(exc)
            )
        finally:
            if "trace_token" in locals():
                trace_sink.reset(trace_token)
            provider.client.close()
            async_to_sync(close_shared_rag)()
            connections.close_all()
            (output / "result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            lines = [
                "# Prepared question small live smoke",
                "",
                "Status: " + report["status"],
                "",
                report["scope"],
                "",
                f"Preparation status: {report.get('preparation_status')}",
                f"Preparation seconds: {report.get('preparation_seconds')}",
                f"Reuse model calls: {report.get('reuse_model_calls')}",
                "",
                "Opening: " + json.dumps(report.get("opening"), ensure_ascii=False),
                "",
                "Knowledge: " + json.dumps(report.get("knowledge"), ensure_ascii=False),
            ]
            (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("REPORT: " + str(output / "report.md"), flush=True)
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
