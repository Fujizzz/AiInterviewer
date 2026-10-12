"""RAGPort adapter against the real local index (Postgres+pgvector with the P0 corpus + bge-m3).

Skipped unless the rag extra is installed, `docker compose -f rag/docker-compose.yml up -d` is
running, the schema is migrated and the corpus is indexed. No data is written.
"""

from __future__ import annotations

import importlib.util

import pytest

if any(
    importlib.util.find_spec(m) is None
    for m in ("psycopg", "psycopg_pool", "pgvector", "numpy", "sentence_transformers")
):
    pytest.skip("rag extra not installed", allow_module_level=True)

import psycopg  # noqa: E402

from rag.adapter import create_rag_adapter  # noqa: E402
from rag.index import config  # noqa: E402
from rag.index.db import db_reachable  # noqa: E402
from shared.contracts import RetrievalSource  # noqa: E402
from tests.rag.adapter_helpers import make_request  # noqa: E402


def _indexed() -> bool:
    if not db_reachable():
        return False
    try:
        with psycopg.connect(config.get_dsn(), connect_timeout=2) as conn:
            has_owner_col = conn.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'chunks' AND column_name = 'candidate_id'"
            ).fetchone()[0]
            n = conn.execute(
                "SELECT count(*) FROM chunks WHERE namespace = 'technical'"
            ).fetchone()[0]
        return bool(has_owner_col) and n > 1000
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _indexed(), reason="local Postgres index not reachable / not migrated / not loaded"
)

S = RetrievalSource
# (query, sources a correct top-5 may come from, text that must appear in at least one top-5 chunk)
CASES = [
    (
        "How does PagedAttention manage the KV cache to avoid memory fragmentation?",
        {"vllm", "paper-pagedattention-vllm"},
        "pagedattention",
    ),
    (
        "Django select_related to avoid N+1 queries",
        {"django"},
        "select_related",
    ),
    (
        "How does Kafka achieve exactly-once semantics?",
        {"kafka-docs", "kafka-config-reference"},
        "exactly",
    ),
]


@pytest.fixture(scope="module")
def adapter():
    adapter = create_rag_adapter(warmup=True)
    yield adapter
    adapter.close()


@pytest.mark.parametrize(("query", "sources", "needle"), CASES)
async def test_technical_queries_return_sensible_cited_chunks(
    adapter, query, sources, needle, capsys
):
    response = await adapter.retrieve(make_request(S.TECHNICAL, query=query, top_k=5))
    with capsys.disabled():
        print(f"\n[{response.latency_ms} ms] {query!r}")
        for c in response.chunks:
            m = c.metadata
            print(
                f"   {c.retrieval_score:.3f} {m['score_kind']:<6} {m['source_name']:<26}"
                f" dense={m['scores']['dense']:.3f} conf={m['confidence']:<6} {c.chunk_id}"
                f"  {' > '.join(m['heading_path'])[:60]}"
            )
    assert 1 <= len(response.chunks) <= 5 and response.partial is False
    assert response.latency_ms is not None and response.latency_ms < 2000  # Agent RAG timeout
    assert all(
        c.source == S.TECHNICAL and c.metadata["namespace"] == "technical" for c in response.chunks
    )
    assert all(
        c.metadata["untrusted"] is True and c.metadata["source_url"] for c in response.chunks
    )
    assert any(needle in c.content.lower() for c in response.chunks), "no chunk mentions the term"
    assert sources & {c.metadata["source_name"] for c in response.chunks}
    top = response.chunks[0].metadata
    assert top["confidence"] in {"medium", "high"} and top["low_confidence"] is False


async def test_off_topic_query_is_flagged_low_confidence(adapter):
    response = await adapter.retrieve(make_request(S.TECHNICAL, query="sourdough bread recipe"))
    assert all(c.metadata["low_confidence"] for c in response.chunks)


async def test_question_source_applies_difficulty_mapping(adapter):
    response = await adapter.retrieve(
        make_request(S.QUESTION, query="merge overlapping intervals", difficulty=5, top_k=5)
    )
    assert response.chunks
    assert {c.metadata["difficulty"] for c in response.chunks} <= {"Hard", None}


async def test_candidate_source_is_empty_without_scope_and_for_unknown_candidates(adapter):
    no_scope = await adapter.retrieve(make_request(S.CANDIDATE, query="kafka pipeline"))
    unknown = await adapter.retrieve(
        make_request(S.CANDIDATE, query="kafka pipeline", candidate_id="no-such-candidate")
    )
    assert no_scope.chunks == [] and unknown.chunks == []


async def test_router_fan_out_over_the_pool_succeeds_concurrently(adapter):
    from agents.routing.rag_router import RAGRouter

    requests = [
        make_request(S.TECHNICAL, query=q, request_id=f"t{i}", top_k=3)
        for i, (q, _, _) in enumerate(CASES)
    ] + [
        make_request(S.QUESTION, query="design a rate limiter", request_id="q"),
        make_request(S.JOB, query="backend engineer requirements", request_id="j"),
        make_request(S.CANDIDATE, query="kafka", request_id="c"),
    ]
    batch = await RAGRouter(adapter).retrieve_with_diagnostics(requests)
    assert batch.failed_sources == [] and batch.timeout_sources == []
    by_id = {r.request_id: r for r in batch.responses}
    assert all(by_id[f"t{i}"].chunks for i in range(3)) and by_id["q"].chunks
    assert by_id["j"].chunks == [] and by_id["c"].chunks == []  # no job/candidate data yet
    assert not any(r.partial for r in batch.responses)
