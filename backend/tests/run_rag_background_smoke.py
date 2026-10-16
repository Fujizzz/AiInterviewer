"""Responsibilities: Measure real background RAG startup and a read-only keyword retrieval.
Implementation: Start the shared subprocess, observe readiness, query once and close it.
Related Modules: app.adapters.rag and app.adapters.rag_process.
Declaration Index:
- run: Record startup return time, warmup time, retrieval result and worker cleanup.
Variable Index:
- ROOT: Repository root used for environment loading and local smoke artifacts.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def run():
    """Load real local RAG resources only; no LLM calls or database writes."""
    from dotenv import load_dotenv

    from app.adapters.rag import close_shared_rag, start_shared_rag
    from shared.contracts import RetrievalRequest, RetrievalSource

    load_dotenv(ROOT / ".env", override=False)
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%dT%H%M%S")
    output = ROOT / "output" / f"rag_background_smoke_{timestamp}"
    output.mkdir(parents=True)
    report = {"scope": "Real subprocess warmup and one read-only retrieval; no LLM calls"}
    port = None
    started = perf_counter()
    try:
        port = start_shared_rag()
        report["startup_return_seconds"] = round(perf_counter() - started, 4)
        if port is None:
            raise RuntimeError("RAG disabled or process could not start")
        report["ready_at_return"] = port.is_ready
        report["worker_pid"] = port._process.pid
        print(json.dumps(report), flush=True)
        if not await asyncio.wait_for(asyncio.to_thread(port.wait_ready), 120):
            raise RuntimeError("RAG worker initialization failed")
        report["warmup_seconds"] = round(perf_counter() - started, 3)
        retrieved_at = perf_counter()
        response = await asyncio.wait_for(
            port.retrieve(
                RetrievalRequest(
                    request_id="background-smoke",
                    interview_id="offline-background-smoke",
                    source=RetrievalSource.TECHNICAL,
                    intent="Technical principle question",
                    difficulty=2,
                    query="PagedAttention",
                    top_k=3,
                )
            ),
            15,
        )
        report.update(
            retrieval_seconds=round(perf_counter() - retrieved_at, 3),
            chunks=len(response.chunks),
            titles=[chunk.title for chunk in response.chunks],
            status="passed" if response.chunks else "no_evidence",
        )
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        await close_shared_rag()
        report["worker_stopped"] = port is None or not port._process.is_alive()
        report["total_seconds"] = round(perf_counter() - started, 3)
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output / "report.md").write_text(
            "# Background RAG smoke\n\n```json\n"
            + json.dumps(report, ensure_ascii=False, indent=2)
            + "\n```\n",
            encoding="utf-8",
        )
        print(json.dumps({"output": str(output), **report}, ensure_ascii=True), flush=True)
    return report["status"] == "passed"


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(run()) else 1)
