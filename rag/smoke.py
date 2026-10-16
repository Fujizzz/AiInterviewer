"""Live keyword -> pgvector retrieval -> project knowledge question smoke test.

Uses the configured real embedding model, database and question/quality models.
Only the preceding project answer is a synthetic fixture; no resume is uploaded.
Run from the repository root: python -m rag.smoke PagedAttention
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from app.adapters.llm import ProviderLLMAdapter
from app.adapters.repository import InMemoryInterviewRepository
from app.providers.llm import OpenAILLM
from rag.adapter import create_rag_adapter
from rag.index.db import connect
from shared.contracts import (
    AnswerAnalysis,
    CandidateAnswer,
    CandidateProfile,
    CandidateProject,
    Competency,
    EvaluationFeedback,
    InitializeInterviewRequest,
    InterviewStage,
    JobProfile,
)


class RecordingRAG:
    """Record actual adapter requests/responses without changing retrieval."""

    def __init__(self, backend):
        self.backend = backend
        self.calls = []

    async def retrieve(self, request):
        response = await self.backend.retrieve(request)
        self.calls.append(
            {
                "request": request.model_dump(mode="json"),
                "response": response.model_dump(mode="json"),
            }
        )
        return response


def render_report(report: dict) -> str:
    """Keep startup and LLM time separate from measured retrieval latency."""
    total = report["total_seconds"]
    preparation = total - report["question_seconds"]
    retrieval = sum(
        (call["response"].get("latency_ms") or 0) / 1000 for call in report["retrieval_calls"]
    )
    generation = report["decision"]["details"]["generation_latency_ms"] / 1000
    other = max(0, report["question_seconds"] - retrieval - generation)
    sources = report["project_knowledge"]["source_urls"]
    return (
        f"# Live RAG smoke test\n\nKeyword: `{report['keyword']}`\n\n"
        f"Question: **{report['question']['text']}**\n\n"
        f"Model: `{report['provider']}/{report['model']}`\n\n"
        f"Database: {report['database']}\n\n"
        "| 阶段 | 耗时（秒） |\n| --- | ---: |\n"
        f"| 启动准备（含模型加载、预热及测试上下文初始化） | {preparation:.3f} |\n"
        f"| RAG 检索（含查询向量化和数据库检索） | {retrieval:.3f} |\n"
        f"| 问题生成与质检 | {generation:.3f} |\n"
        f"| 其余调度与提交（差值） | {other:.3f} |\n"
        f"| 测试总耗时 | {total:.3f} |\n\n"
        "启动准备耗时由总耗时减去出题回合耗时得到，并非单独测量的模型加载时间。"
        "测试总耗时不能当作 RAG 检索耗时；进程内预热的模型会被后续检索复用。\n\n"
        "The previous project turn is a synthetic fixture. Retrieval, embedding, question "
        "generation, quality review and turn commit use the real implementations. "
        "This exercises the Agent core, not the browser or backend publication gateway.\n\n"
        + "\n".join(f"- Source: {url}" for url in sources)
        + "\n"
    )


async def run(keyword: str, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    provider = OpenAILLM()
    adapter = None
    recording = None
    report = {"keyword": keyword, "provider": provider.provider, "model": provider.model}
    try:
        print("Warming real BGE-M3 and PostgreSQL adapter...", flush=True)
        adapter = await asyncio.to_thread(create_rag_adapter, warmup=True)
        recording = RecordingRAG(adapter)
        with connect() as conn:
            report["database"] = {
                "server_version": conn.execute("SHOW server_version").fetchone()[0],
                "pgvector_version": conn.execute(
                    "SELECT extversion FROM pg_extension WHERE extname='vector'"
                ).fetchone()[0],
                "indexed_chunks": conn.execute("SELECT count(*) FROM chunks").fetchone()[0],
            }
        repository = InMemoryInterviewRepository()
        interview_id = f"rag-smoke-{uuid4()}"
        settings = load_agent_settings()
        # Seed a previous project turn deterministically. The new knowledge question
        # below uses the real service path, actual retrieval and live LLM/quality calls.
        seed = InterviewAgentService(repository=repository, settings=settings)
        initialized = await seed.initialize_interview(
            InitializeInterviewRequest(
                interview_id=interview_id,
                duration_seconds=900,
                enabled_stages=[InterviewStage.PROJECT_DEEP_DIVE],
                candidate_profile=CandidateProfile(
                    candidate_id="synthetic-smoke-candidate",
                    projects=[
                        CandidateProject(
                            project_id="synthetic-smoke-project",
                            name=f"{keyword} smoke test",
                            technologies=[keyword],
                        )
                    ],
                ),
                job_profile=JobProfile(
                    job_id="smoke-job",
                    title="Software Engineer",
                    competency_importance={c: 1 / len(Competency) for c in Competency},
                ),
            )
        )
        first = initialized.first_action.question
        service = InterviewAgentService(
            repository=repository,
            rag=recording,
            llm=ProviderLLMAdapter(provider),
            settings=settings,
        )
        print(f"Retrieving '{keyword}' and generating a real question...", flush=True)
        generation_started = perf_counter()
        action = await service.apply_evaluation_feedback(
            interview_id,
            EvaluationFeedback(
                request_id="synthetic-first-answer",
                question_id=first.question_id,
                answer_relevance=1,
                evidence_strength=0,
                analysis=AnswerAnalysis(status="substantive", new_information=True),
            ),
            answer=CandidateAnswer(
                interview_id=interview_id,
                question_id=first.question_id,
                answer_id="synthetic-first-answer",
                text=f"For this synthetic test project, I worked with {keyword}.",
            ),
        )
        context = await repository.get_interview_context(interview_id)
        attempt = context.project_knowledge.get("synthetic-smoke-project")
        report.update(
            status="passed" if attempt and attempt.status == "asked" else "failed",
            fixture="Synthetic previous project turn; real database, embedding and LLM calls.",
            question=action.question.model_dump(mode="json") if action.question else None,
            project_knowledge=attempt.model_dump(mode="json") if attempt else None,
            decision=action.decision_trace.model_dump(mode="json"),
            question_seconds=round(perf_counter() - generation_started, 3),
        )
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        report["retrieval_calls"] = recording.calls if recording else []
        report["total_seconds"] = round(perf_counter() - started, 3)
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if adapter is not None:
            await adapter.aclose()
        provider.client.close()
    if report["status"] != "passed":
        raise RuntimeError(f"Knowledge question was not committed: {report['project_knowledge']}")
    question = report["question"]["text"]
    (output / "report.md").write_text(render_report(report), encoding="utf-8")
    print(question, flush=True)
    print(f"Evidence: {output / 'result.json'}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("keyword", nargs="?", default="PagedAttention")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / ("rag_live_smoke_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")),
    )
    args = parser.parse_args()
    asyncio.run(run(args.keyword, args.output))


if __name__ == "__main__":
    main()
