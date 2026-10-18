"""Responsibilities: Measure real full-CV parsing and preparation, including provider token usage.
Implementation: Extract the supplied PDF locally, use the real text-upload API and preparation
service in private SQLite, and observe SDK requests without changing prompts or retry policies.
Related Modules: resume_pdf, resume_versions and question_preparation implement the measured flow.
The PDF sandbox, browser, safety reviewer, scoring and interview dialogue are outside this test.

Declaration Index:
- Audit: Record logical calls, actual SDK requests, topic attempts and retrieval diagnostics.
- Audit.__init__: Initialize private report state and execution-local call attribution.
- Audit.emit: Append a durable event and print bounded progress without credentials.
- Audit.labels: Read the current phase and optional topic attribution.
- Audit.logical_call: Observe a production structured call and preserve its result or exception.
- Audit.api_request: Read actual response usage, including later JSON validation failures.
- Audit.prepare_one: Measure one complete question attempt with built-in quality repairs intact.
- Audit.retrieve: Observe router deadlines and actual retrieval query outcomes.
- Audit.warmup: Measure production lazy RAG initialization separately from retrieval.
- Audit.trace: Retain bounded production quality and completion diagnostics.
- usage_totals: Sum reported token usage while retaining the count of unknown-usage requests.
- write_report: Persist measured denominators, phase totals, topic results and readable findings.
- main: Run fresh parsing and all-topic preparation with real APIs in an isolated database.

Variable Index:
- ROOT: Repository root containing the input CV, environment and private output directory.
"""

import argparse
import json
import os
import secrets
import sys
from collections import Counter
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]


class Audit:
    """Recording only: preserve all production outputs, limits, errors and retries."""

    def __init__(self, output):
        """Own a private JSONL sink and report; context variables propagate to model workers."""
        self.output = output
        self.phase = "setup"
        self.scope = ContextVar("benchmark_scope", default=None)
        self.logical = ContextVar("benchmark_logical", default=None)
        self.lock = Lock()
        self.report = {
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "scope": "Full supplied CV; local PDF extraction -> real text-upload API -> fresh "
            "canonical profile -> every topic opening and one RAG question per project. "
            "No full interview, PDF sandbox endpoint, browser, safety reviewer or scoring. "
            "No explicit second preparation pass; production bounded repairs remain enabled.",
            "logical_calls": [],
            "api_requests": [],
            "question_attempts": [],
            "retrievals": [],
            "rag_initialization": [],
            "traces": [],
        }

    def emit(self, event, data):
        """Save completed measurement events under a lock; print metadata without model inputs."""
        entry = {"event": event, **data}
        with self.lock:
            with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            visible = {k: v for k, v in entry.items() if k not in {"output", "chunks", "topic"}}
            print(json.dumps(visible, ensure_ascii=False, default=str), flush=True)

    def labels(self):
        """Attribute nested provider workers to the original phase and standalone topic."""
        return {"phase": self.phase, **(self.scope.get() or {})}

    def logical_call(self, actual, provider, prompt, data, schema):
        """Record validated structured calls separately from provider-internal JSON retries."""
        from agents.model_calls import safe_error_details, unavailable_provider_reason

        entry = {
            "id": len(self.report["logical_calls"]) + 1,
            **self.labels(),
            "schema": schema.__name__,
            "status": "running",
        }
        self.report["logical_calls"].append(entry)
        token = self.logical.set(entry["id"])
        started = perf_counter()
        try:
            result = actual(provider, prompt, data, schema)
            entry["status"] = "success"
            if schema.__name__ != "ResumeExtraction":
                entry["output"] = result.model_dump(mode="json")
            return result
        except BaseException as exc:
            entry.update(status="failed", details=safe_error_details(exc))
            entry["provider_unavailable"] = unavailable_provider_reason(exc)
            raise
        finally:
            entry["seconds"] = round(perf_counter() - started, 3)
            self.logical.reset(token)
            self.emit("logical_call", entry)

    def api_request(self, actual, client, *args, **kwargs):
        """Count SDK requests and returned usage, never estimate tokens for missing responses."""
        from agents.model_calls import current_model_call, safe_error_details

        call = current_model_call.get()
        entry = {
            "id": len(self.report["api_requests"]) + 1,
            **self.labels(),
            "logical_call_id": self.logical.get(),
            "operation": call.operation if call else None,
            "model": kwargs.get("model"),
            "status": "running",
            "usage": None,
        }
        self.report["api_requests"].append(entry)
        started = perf_counter()
        try:
            response = actual(client, *args, **kwargs)
            entry["status"] = "success"
            entry["response_id"] = getattr(response, "id", None)
            if response.usage is not None:
                entry["usage"] = response.usage.model_dump(mode="json")
            choices = getattr(response, "choices", [])
            entry["finish_reasons"] = [choice.finish_reason for choice in choices]
            return response
        except BaseException as exc:
            entry.update(status="failed", details=safe_error_details(exc))
            raise
        finally:
            entry["seconds"] = round(perf_counter() - started, 3)
            self.emit("api_request", entry)

    async def prepare_one(self, actual, plan, *args, **kwargs):
        """Measure the full bounded draft/review/repair path for an individual prepared question."""
        entry = {
            "project_id": plan.project_id,
            "topic_key": plan.topic_key,
            "topic": plan.topic,
            "kind": kwargs.get("kind", "opening"),
        }
        token = self.scope.set(entry.copy())
        started = perf_counter()
        before = len(self.report["api_requests"])
        self.report["question_attempts"].append(entry)
        try:
            item = await actual(plan, *args, **kwargs)
            entry.update(status="ready", question=item.text)
            return item
        except BaseException as exc:
            entry.update(
                status="failed", error_code=getattr(exc, "reason_code", type(exc).__name__)
            )
            raise
        finally:
            entry["seconds"] = round(perf_counter() - started, 3)
            requests = self.report["api_requests"][before:]
            entry["api_requests"] = len(requests)
            entry["tokens"] = usage_totals(requests)
            self.scope.reset(token)
            self.emit("question_attempt", entry)

    async def retrieve(self, actual, router, request):
        """Observe the real router, including its deadline and low-confidence result metadata."""
        entry = {
            "project_id": request.project_id,
            "query": request.query,
            "source": request.source.value,
        }
        self.report["retrievals"].append(entry)
        started = perf_counter()
        try:
            response = await actual(router, request)
            entry["status"] = "success"
            entry["chunks"] = [
                {
                    "chunk_id": chunk.chunk_id,
                    "title": chunk.title,
                    "score": chunk.retrieval_score,
                    "metadata": chunk.metadata,
                }
                for chunk in response.chunks
            ]
            entry["usable_chunks"] = sum(
                bool(chunk.content.strip())
                and not chunk.metadata.get("low_confidence")
                and chunk.metadata.get("confidence") != "low"
                for chunk in response.chunks
            )
            return response
        except BaseException as exc:
            entry.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            entry["seconds"] = round(perf_counter() - started, 3)
            self.emit("retrieval", entry)

    async def warmup(self, actual):
        """Keep production lazy initialization in place and measure its cold-start cost."""
        started = perf_counter()
        result = await actual()
        entry = {"seconds": round(perf_counter() - started, 3), "available": result is not None}
        self.report["rag_initialization"].append(entry)
        self.emit("rag_initialization", entry)
        return result

    def trace(self, event, data):
        """Retain relevant bounded traces for explaining failures without changing decisions."""
        if event in {"question.quality", "react.finished", "react.decision", "model.error"}:
            self.report["traces"].append({"event": event, **self.labels(), **data})


def usage_totals(requests):
    """Sum provider-reported input/output/total tokens; count missing usage independently."""
    result = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "unknown_requests": 0}
    for request in requests:
        usage = request.get("usage")
        if usage is None:
            result["unknown_requests"] += 1
            continue
        result["input_tokens"] += usage.get("prompt_tokens", usage.get("input_tokens", 0))
        result["output_tokens"] += usage.get("completion_tokens", usage.get("output_tokens", 0))
        result["total_tokens"] += usage.get("total_tokens", 0)
    return result


def write_report(audit):
    """Write exact call and question-success denominators alongside detailed private artifacts."""
    report = audit.report
    requests = report["api_requests"]
    rows = report.get("rows", [])
    projects = report.get("projects", [])
    topics = sum(len(project["topics"]) for project in projects)
    knowledge = sum(bool(project["topics"]) for project in projects)
    expected = topics + knowledge
    ready = Counter(row["kind"] for row in rows if row["status"] == "ready")
    phases = {}
    for phase in dict.fromkeys(request["phase"] for request in requests):
        selected = [request for request in requests if request["phase"] == phase]
        phases[phase] = {
            "api_requests": len(selected),
            "logical_calls": sum(c["phase"] == phase for c in report["logical_calls"]),
            "tokens": usage_totals(selected),
        }
    report["summary"] = {
        "projects": len(projects),
        "topics": topics,
        "expected_openings": topics,
        "expected_knowledge": knowledge,
        "expected_questions": expected,
        "ready_openings": ready["opening"],
        "ready_knowledge": ready["knowledge"],
        "opening_success_rate": ready["opening"] / topics if topics else None,
        "knowledge_success_rate": ready["knowledge"] / knowledge if knowledge else None,
        "combined_success_rate": sum(ready.values()) / expected if expected else None,
        "api_requests": len(requests),
        "successful_api_responses": sum(r["status"] == "success" for r in requests),
        "logical_calls": len(report["logical_calls"]),
        "schema_calls": dict(Counter(call["schema"] for call in report["logical_calls"])),
        "tokens": usage_totals(requests),
        "phases": phases,
        "retrievals": len(report["retrievals"]),
        "retrievals_with_usable_chunks": sum(
            r.get("usable_chunks", 0) > 0 for r in report["retrievals"]
        ),
        "row_failures": dict(Counter(r["error_code"] for r in rows if r["status"] != "ready")),
        "unattempted_questions": max(0, expected - len(rows)),
    }
    (audit.output / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    summary = report["summary"]
    tokens = summary["tokens"]
    lines = [
        "# Full resume question preparation benchmark",
        "",
        f"Status: {report['status']}",
        f"Model: {report.get('model')} ({report.get('provider')})",
        f"Input: {report.get('source_file')}",
        "",
        report["scope"],
        "",
        f"Projects/experiences: {len(projects)}; topics: {topics}.",
        f"Opening questions ready: {ready['opening']}/{topics}.",
        f"RAG knowledge questions ready: {ready['knowledge']}/{knowledge}.",
        f"All questions ready: {sum(ready.values())}/{expected}.",
        "",
        f"Logical model calls: {summary['logical_calls']}.",
        f"Actual API requests: {len(requests)}; successful responses: "
        f"{summary['successful_api_responses']}.",
        f"Reported tokens: {tokens['total_tokens']} "
        f"(input {tokens['input_tokens']}; output {tokens['output_tokens']}).",
        f"Requests with unknown token usage: {tokens['unknown_requests']}.",
        f"Retrieval queries: {summary['retrievals']}; with usable chunks: "
        f"{summary['retrievals_with_usable_chunks']}.",
        "",
        f"Stage seconds: {json.dumps(report.get('timings', {}))}",
        "",
        "| Project/experience | Topics | Openings ready | RAG ready |",
        "| --- | ---: | ---: | ---: |",
    ]
    for project in projects:
        own = [row for row in rows if row["project_id"] == project["project_id"]]
        counts = Counter(row["kind"] for row in own if row["status"] == "ready")
        lines.append(
            f"| {project['name']} | {len(project['topics'])} | "
            f"{counts['opening']} | {counts['knowledge']} |"
        )
    lines.extend(
        [
            "",
            "## Calls and tokens by phase",
            "",
            "```json",
            json.dumps(phases, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Individual topics and questions",
            "",
        ]
    )
    for project in projects:
        lines.extend([f"### {project['name']}", ""])
        for topic in project["topics"]:
            own = next(
                (
                    r
                    for r in rows
                    if r["project_id"] == project["project_id"]
                    and r["topic_key"] == topic["topic_key"]
                    and r["kind"] == "opening"
                ),
                {},
            )
            lines.extend(
                [
                    f"- Topic: {topic['topic']}",
                    f"  - Result: {own.get('status', 'unattempted')} {own.get('error_code', '')}",
                    f"  - Question: {(own.get('content') or {}).get('text', '')}",
                ]
            )
        for row in rows:
            if row["project_id"] == project["project_id"] and row["kind"] == "knowledge":
                lines.extend(
                    [
                        f"- RAG: {row['status']} {row['error_code']}",
                        f"  - Question: {(row.get('content') or {}).get('text', '')}",
                    ]
                )
        lines.append("")
    lines.extend(
        [
            "## Measurement boundaries",
            "",
            "Private SQLite stores this run only; normal application resumes are unchanged.",
            "Local PDF extraction and the text-upload API are real. The Linux/WSL PDF "
            "sandbox endpoint is not exercised.",
            "Success means a question passed the production full quality gate and was "
            "persisted as ready. Built-in bounded repairs count toward calls and tokens.",
            "A successful HTTP response may still fail JSON validation or question quality. "
            "Such returned usage is included. Missing usage is unknown, not estimated.",
            "RAG embeddings run locally; their tokens are not remote LLM API usage.",
        ]
    )
    if report.get("manual_findings"):
        lines.extend(["", "## Log analysis and manual evidence review", ""])
        lines.extend(f"- {finding}" for finding in report["manual_findings"])
    if report.get("rag_corpus"):
        lines.extend(
            [
                "",
                "## Read-only RAG corpus snapshot",
                "",
                "```json",
                json.dumps(report["rag_corpus"], ensure_ascii=False, indent=2),
                "```",
            ]
        )
    (audit.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    """Run the authorized real-API benchmark without output stubs or a full interview."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-pdf", type=Path, default=ROOT / "Wang_Shunyao_CV.pdf")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not args.resume_pdf.is_file():
        parser.error("--resume-pdf must name an existing PDF")
    output = args.output_dir or ROOT / "output" / (
        "full_preparation_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(48))
    os.environ["INTERVIEW_DB_PATH"] = str(output / "benchmark.sqlite3")
    os.environ["QUESTION_PREPARATION_EXECUTION"] = "disabled"
    os.environ["AI_SECURITY_ENABLED"] = "false"
    os.environ["RAG_ENABLED"] = "true"
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")
    audit = Audit(output)
    report = audit.report
    report["source_file"] = args.resume_pdf.name
    report["timings"] = {}
    total_started = perf_counter()
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
    from app.adapters.rag import close_shared_rag
    from app.providers.llm import OpenAILLM

    call_command("migrate", verbosity=0, interactive=False)
    owner = get_user_model().objects.create_user(username="full-preparation-benchmark")
    client = APIClient()
    client.force_authenticate(owner)
    provider = OpenAILLM()
    report.update(model=provider.model, provider=provider.provider)
    actual_call = OpenAILLM.__call__
    chat_type = type(provider.client.chat.completions)
    actual_chat = chat_type.create
    responses_type = type(provider.client.responses)
    actual_parse = responses_type.parse
    actual_prepare = question_preparation.prepare_one
    actual_retrieve = RAGRouter._retrieve_one
    actual_warmup = question_preparation.get_shared_rag
    version = None
    trace_token = trace_sink.set(audit.trace)
    try:
        with (
            override_settings(ALLOWED_HOSTS=["testserver"]),
            patch.object(
                OpenAILLM,
                "__call__",
                lambda p, prompt, data, schema: audit.logical_call(
                    actual_call, p, prompt, data, schema
                ),
            ),
            patch.object(
                chat_type, "create", lambda c, *a, **kw: audit.api_request(actual_chat, c, *a, **kw)
            ),
            patch.object(
                responses_type,
                "parse",
                lambda c, *a, **kw: audit.api_request(actual_parse, c, *a, **kw),
            ),
            patch.object(
                question_preparation,
                "prepare_one",
                lambda *a, **kw: audit.prepare_one(actual_prepare, *a, **kw),
            ),
            patch.object(
                RAGRouter, "_retrieve_one", lambda r, req: audit.retrieve(actual_retrieve, r, req)
            ),
            patch.object(
                question_preparation, "get_shared_rag", lambda: audit.warmup(actual_warmup)
            ),
        ):
            started = perf_counter()
            pages = extract_pdf(args.resume_pdf.read_bytes())
            text = "\n\n".join(page.text for page in pages)
            report["pdf"] = {"pages": len(pages), "characters": len(text)}
            report["timings"]["pdf_extraction"] = round(perf_counter() - started, 3)
            flow_started = perf_counter()
            audit.phase = "resume_upload_and_parsing"
            started = perf_counter()
            response = client.post("/api/resume-versions/", {"text": text}, format="json")
            report["timings"]["resume_upload_and_parsing"] = round(perf_counter() - started, 3)
            report["upload_status_code"] = response.status_code
            if response.status_code != 201:
                raise RuntimeError("Resume text-upload API did not return 201")
            version = ResumeVersion.objects.get(pk=response.data["id"])
            report["resume_version_id"] = str(version.pk)
            report["profile_status"] = version.interview_profile_status
            report["profile_error"] = version.interview_profile_error
            profile, _ = read_profile(version, candidate_id=f"resume-{version.pk}")
            report["stored_profile"] = version.interview_profile
            report["projects"] = [
                {
                    "project_id": p.project_id,
                    "name": p.name,
                    "technologies": p.technologies,
                    "topics": [t.model_dump(mode="json") for t in TopicSelector().candidates(p)],
                }
                for p in profile.projects
            ]
            audit.emit(
                "profile_ready",
                {
                    "projects": len(profile.projects),
                    "topics": sum(len(p["topics"]) for p in report["projects"]),
                    "seconds": report["timings"]["resume_upload_and_parsing"],
                },
            )
            write_report(audit)
            audit.phase = "question_preparation"
            started = perf_counter()
            version = question_preparation.prepare_questions(version.pk, owner.pk, llm=provider)
            report["timings"]["question_preparation"] = round(perf_counter() - started, 3)
            report["timings"]["upload_to_preparation_complete"] = round(
                perf_counter() - flow_started, 3
            )
            report["preparation_status"] = version.question_preparation_status
            report["preparation_error"] = version.question_preparation_error
            report["status"] = "completed"
    except BaseException as exc:
        report.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            failure=safe_error_details(exc),
        )
        raise
    finally:
        trace_sink.reset(trace_token)
        provider.client.close()
        async_to_sync(close_shared_rag)()
        if version is not None:
            version.refresh_from_db()
            report["preparation_status"] = version.question_preparation_status
            report["preparation_error"] = version.question_preparation_error
            report["rows"] = list(
                version.prepared_questions.order_by("project_id", "kind", "topic_key").values(
                    "project_id", "topic_key", "kind", "status", "error_code", "content"
                )
            )
        report["timings"]["total_including_setup_and_cleanup"] = round(
            perf_counter() - total_started, 3
        )
        write_report(audit)
        connections.close_all()
        audit.emit(
            "benchmark_finished",
            {
                "status": report["status"],
                "summary": report["summary"],
                "report": str(output / "report.md"),
            },
        )


if __name__ == "__main__":
    main()
