"""DB integration tests: migrate / load / retrieve against a throwaway database.

Skipped automatically when Postgres is unreachable (e.g. Docker is down). Needs a role
that can CREATE DATABASE (the docker-compose user is a superuser). A scratch database is
created per module and dropped afterwards -- the real `intellihire` database is never touched.
"""

import re
import uuid

import pytest

# Optional rag-extra dependencies: skip the whole module when they are not installed.
np = pytest.importorskip("numpy")
psycopg = pytest.importorskip("psycopg")
pytest.importorskip("pgvector")

from psycopg import conninfo  # noqa: E402

from rag.index import config  # noqa: E402
from rag.index import db as dbmod  # noqa: E402
from rag.index import embed as E  # noqa: E402
from rag.index import load as L  # noqa: E402
from rag.registry import load_registry  # noqa: E402
from rag.retrieval.access import NamespaceAccessError  # noqa: E402
from rag.retrieval.intent import Filters, RetrievalIntent  # noqa: E402
from rag.retrieval.retriever import Retriever  # noqa: E402

BASE_DSN = config.get_dsn()


def _reachable() -> bool:
    return dbmod.db_reachable(BASE_DSN)


pytestmark = pytest.mark.skipif(
    not _reachable(), reason="Postgres not reachable (docker compose up -d)"
)

DIM = config.EMBED_DIM
META = E.make_meta()  # real model identity; vectors below are synthetic


def _bow_vec(text: str) -> np.ndarray:
    """Deterministic bag-of-words hashing vector: texts sharing words are close in cosine."""
    v = np.zeros(DIM, np.float32)
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        v[hash_w(w) % DIM] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


def hash_w(w: str) -> int:
    import zlib

    return zlib.crc32(w.encode())


class FakeEmbedder:
    meta = META

    def encode(self, texts, batch_size=None):
        return np.stack([_bow_vec(t) for t in texts])


def encode_fn(texts):
    return FakeEmbedder().encode(texts)


def _create_db(tmp_path_factory):
    """Create a throwaway database + apply the schema. Yields its DSN, drops it afterwards."""
    name = f"intellihire_test_{uuid.uuid4().hex[:8]}"
    admin_dsn = conninfo.make_conninfo(BASE_DSN, dbname="postgres")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        admin = psycopg.connect(BASE_DSN, autocommit=True, connect_timeout=3)
    admin.execute(f'CREATE DATABASE "{name}"')
    test_dsn = conninfo.make_conninfo(BASE_DSN, dbname=name)

    # Some Postgres builds (e.g. minimal local ones) lack contrib pg_trgm; fall back to a schema
    # without the trigram index so the rest still gets exercised. The pgvector image has it.
    sql_path = config.SCHEMA_SQL
    has_trgm = admin.execute(
        "SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'"
    ).fetchone()
    if not has_trgm:
        stripped = "\n".join(
            line
            for line in config.SCHEMA_SQL.read_text().splitlines()
            if "pg_trgm" not in line and "gin_trgm_ops" not in line
        )
        sql_path = tmp_path_factory.mktemp("schema") / "schema.sql"
        sql_path.write_text(stripped)
    orig = config.SCHEMA_SQL
    config.SCHEMA_SQL = sql_path
    try:
        dbmod.migrate(test_dsn)
        yield test_dsn
    finally:
        config.SCHEMA_SQL = orig
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


@pytest.fixture(scope="module")
def dsn(tmp_path_factory):
    yield from _create_db(tmp_path_factory)


@pytest.fixture(scope="module")
def hnsw_dsn(tmp_path_factory):
    yield from _create_db(tmp_path_factory)


def source_for(ns: str) -> str:
    for s in load_registry(config.SOURCES_YAML):
        if s.namespace == ns:
            return s.name
    return "vllm"


def mk(
    cid,
    ns,
    text,
    source,
    title=None,
    heading=("H",),
    domain="d",
    difficulty=None,
    tech=(),
    candidate_id=None,
):
    row = {
        "chunk_id": cid,
        "namespace": ns,
        "text": text,
        "title": title or heading[-1],
        "heading_path": list(heading),
        "source_name": source,
        "source_type": "git_docs",
        "source_url": f"https://example.org/{cid}",
        "doc_version": "v1",
        "domain": domain,
        "topic": None,
        "related_technologies": list(tech),
        "competency": None,
        "difficulty": difficulty,
        "trust_level": "official",
        "license": "MIT",
        "token_count": 20,
        "ingested_at": "2026-10-02T00:00:00Z",
        "metadata": {"k": cid},
    }
    if ns == "candidate":  # migration 003: candidate chunks must be owned
        row["candidate_id"] = candidate_id or "cand-A"
    return row


FIXTURE = [
    mk(
        "vllm-a1",
        "technical",
        "PagedAttention stores the KV cache in fixed size blocks to avoid fragmentation",
        "vllm",
        heading=("Paged Attention",),
        domain="ml-serving",
        tech=("vLLM", "kv-cache"),
    ),
    mk(
        "vllm-a2",
        "technical",
        "Continuous batching schedules requests at the iteration level",
        "vllm",
        heading=("Scheduler",),
        domain="ml-serving",
        tech=("vllm",),
    ),
    mk(
        "fastapi-b1",
        "technical",
        "Use select_related and prefetch_related to reduce database queries in the ORM",
        "fastapi",
        heading=("Queries",),
        domain="web-backend",
        tech=("django",),
    ),
    mk(
        "fastapi-b2",
        "technical",
        "Dependency injection with Depends declares shared logic for path operations",
        "fastapi",
        heading=("Dependencies",),
        domain="web-backend",
    ),
    mk(
        "tih-q1",
        "question",
        "Given an array find two numbers that add up to a target value",
        "tech-interview-handbook",
        heading=("Two Sum",),
        domain="interview-prep",
        difficulty="Easy",
    ),
    mk(
        "tih-q2",
        "question",
        "Merge overlapping intervals in a list of ranges",
        "tech-interview-handbook",
        heading=("Merge Intervals",),
        domain="interview-prep",
        difficulty="Medium",
    ),
    mk(
        "rub-1",
        "rubric",
        "Scoring anchor for system design depth level four requires discussing consistency "
        "tradeoffs",
        source_for("rubric"),
        heading=("BARS", "System design"),
        domain="rubric",
    ),
    mk(
        "cand-1",
        "candidate",
        "Candidate led the migration of the consistency layer and worked with Kafka",
        source_for("candidate"),
        heading=("Experience",),
        domain="resume",
    ),
    mk(
        "cand-2",
        "candidate",
        "Candidate B owns the consistency layer migration and runs Kafka in production",
        source_for("candidate"),
        heading=("Experience",),
        domain="resume",
        candidate_id="cand-B",
    ),
    mk(
        "job-1",
        "job",
        "Backend engineer role requires Kafka and PostgreSQL experience",
        source_for("job"),
        heading=("Job",),
        domain="jobs",
    ),
    mk(
        "test-1",
        "test",
        "SYNTHETIC TEST CASE consistency tradeoffs Kafka PagedAttention select_related",
        source_for("test"),
        heading=("Synthetic",),
        domain="test",
    ),
]


def build_cache(tmp_path, chunks, meta=META):
    """Write chunks/*.jsonl + embeddings cache for each source into tmp dirs."""
    import json

    chunks_dir, cache_dir = tmp_path / "chunks", tmp_path / "emb"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    by_src = {}
    for c in chunks:
        by_src.setdefault(c["source_name"], []).append(c)
    for s, cs in by_src.items():
        (chunks_dir / f"{s}.jsonl").write_text("\n".join(json.dumps(c) for c in cs) + "\n")
        E.embed_source(s, cs, encode_fn, meta, cache_dir)
    return chunks_dir, cache_dir, sorted(by_src)


@pytest.fixture(scope="module")
def loaded(dsn, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("load")
    chunks_dir, cache_dir, names = build_cache(tmp, FIXTURE)
    L.load_sources(
        names,
        dsn=dsn,
        chunks_dir=chunks_dir,
        cache_dir=cache_dir,
        prune_unknown=True,
        log=lambda s: None,
    )
    return dsn


@pytest.fixture()
def retriever(loaded):
    r = Retriever(dsn=loaded, embedder=FakeEmbedder(), top_n=20)
    yield r
    r.close()


def ids(passages):
    return [p.chunk_id for p in passages]


# ---------------- migrate / load ----------------


def test_migrate_is_idempotent(dsn):
    dbmod.migrate(dsn)
    assert "already current" in dbmod.migrate(dsn)
    with dbmod.connect(dsn) as c:
        assert c.execute("SELECT count(*) FROM candidate_identity").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 3


def test_load_rows_roundtrip(loaded):
    with dbmod.connect(loaded) as c:
        (n,) = c.execute("SELECT count(*) FROM chunks").fetchone()
        assert n == len(FIXTURE)
        row = c.execute(
            "SELECT heading_path, related_technologies, metadata, embedding_model, text_hash, "
            "vector_dims(embedding), tsv IS NOT NULL FROM chunks WHERE chunk_id = 'vllm-a1'"
        ).fetchone()
        assert row[0] == ["Paged Attention"] and row[1] == ["vLLM", "kv-cache"]
        assert row[2] == {"k": "vllm-a1"} and row[3] == "BAAI/bge-m3" and len(row[4]) == 64
        assert row[5] == 1024 and row[6] is True


def test_reload_is_idempotent_and_does_not_rewrite_rows(dsn, tmp_path):
    chunks_dir, cache_dir, names = build_cache(tmp_path, FIXTURE)
    L.load_sources(names, dsn=dsn, chunks_dir=chunks_dir, cache_dir=cache_dir, log=lambda s: None)
    with dbmod.connect(dsn) as c:
        before = dict(c.execute("SELECT chunk_id, xmin::text FROM chunks").fetchall())
    L.load_sources(names, dsn=dsn, chunks_dir=chunks_dir, cache_dir=cache_dir, log=lambda s: None)
    with dbmod.connect(dsn) as c:
        after = dict(c.execute("SELECT chunk_id, xmin::text FROM chunks").fetchall())
    assert before == after


def test_update_and_delete_follow_chunk_files(dsn, tmp_path):
    chunks = [dict(c) for c in FIXTURE if c["source_name"] == "fastapi"]
    chunks_dir, cache_dir, names = build_cache(tmp_path, chunks)
    L.load_sources(names, dsn=dsn, chunks_dir=chunks_dir, cache_dir=cache_dir, log=lambda s: None)

    edited = [
        dict(chunks[0], text="completely rewritten text about middleware"),
    ]  # b2 removed
    chunks_dir, cache_dir, names = build_cache(tmp_path / "v2", edited)
    L.load_sources(names, dsn=dsn, chunks_dir=chunks_dir, cache_dir=cache_dir, log=lambda s: None)
    with dbmod.connect(dsn) as c:
        rows = dict(
            c.execute("SELECT chunk_id, text FROM chunks WHERE source_name = 'fastapi'").fetchall()
        )
    assert list(rows) == ["fastapi-b1"] and "middleware" in rows["fastapi-b1"]
    # restore for the other tests in this module
    full = [c for c in FIXTURE if c["source_name"] == "fastapi"]
    chunks_dir, cache_dir, names = build_cache(tmp_path / "v3", full)
    L.load_sources(names, dsn=dsn, chunks_dir=chunks_dir, cache_dir=cache_dir, log=lambda s: None)


def test_stale_cache_and_model_mismatch_fail_loudly(loaded, tmp_path):
    chunks_dir, cache_dir, names = build_cache(
        tmp_path, [c for c in FIXTURE if c["source_name"] == "vllm"]
    )
    # chunk file edited after embedding => stale cache
    import json

    p = chunks_dir / "vllm.jsonl"
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    rows[0]["text"] += " changed"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with pytest.raises(L.IndexError_, match="stale"):
        L.load_sources(
            ["vllm"], dsn=loaded, chunks_dir=chunks_dir, cache_dir=cache_dir, log=lambda s: None
        )

    other = E.make_meta(model="some/other-model")
    chunks_dir2, cache_dir2, _ = build_cache(
        tmp_path / "m", [c for c in FIXTURE if c["source_name"] == "vllm"], meta=other
    )
    with pytest.raises(L.IndexError_, match="differs"):
        L.load_sources(
            ["vllm"], dsn=loaded, chunks_dir=chunks_dir2, cache_dir=cache_dir2, log=lambda s: None
        )


def test_retriever_refuses_mismatched_query_embedder(loaded):
    class Other(FakeEmbedder):
        meta = E.make_meta(model="some/other-model")

    r = Retriever(dsn=loaded, embedder=Other())
    try:
        with pytest.raises(L.IndexError_):
            r.retrieve(RetrievalIntent(query="kv cache"), "evaluator")
    finally:
        r.close()


# ---------------- retrieval ----------------


def test_hybrid_finds_expected_chunk_with_traceable_scores(retriever):
    out = retriever.retrieve(
        RetrievalIntent(query="How does PagedAttention reduce KV cache fragmentation?", k=3),
        "question_planner",
    )
    assert out[0].chunk_id == "vllm-a1"
    p = out[0]
    assert p.rank == 1 and p.source_url.endswith("vllm-a1") and p.doc_version == "v1"
    assert p.heading_path == ["Paged Attention"]
    assert p.scores["dense"] is not None and p.scores["lexical"] is not None and p.scores["rrf"] > 0
    assert p.scores["rerank"] is None
    assert p.stage_ranks["dense"] == 1 and p.stage_ranks["lexical"] == 1


def test_identifier_query_matches_snake_case(retriever):
    out = retriever.retrieve(
        RetrievalIntent(query="difference between select_related and prefetch_related", k=3),
        "question_planner",
        mode="lexical",
    )
    assert out and out[0].chunk_id == "fastapi-b1"


def test_modes_dense_and_lexical_only(retriever):
    d = retriever.retrieve(
        RetrievalIntent(query="merge overlapping intervals", k=2), "question_planner", mode="dense"
    )
    assert (
        d[0].chunk_id == "tih-q2" and d[0].scores["lexical"] is None and d[0].scores["rrf"] is None
    )
    lx = retriever.retrieve(
        RetrievalIntent(query="merge overlapping intervals", k=2),
        "question_planner",
        mode="lexical",
    )
    assert lx[0].chunk_id == "tih-q2" and lx[0].scores["dense"] is None


def test_test_namespace_is_never_returned(retriever):
    q = "SYNTHETIC TEST CASE consistency tradeoffs Kafka PagedAttention select_related"
    for role in ("question_planner", "evaluator", "recommender"):
        for mode in ("dense", "lexical", "hybrid"):
            got = retriever.retrieve(RetrievalIntent(query=q, k=20), role, mode=mode)
            assert "test-1" not in ids(got)
            assert all(p.namespace != "test" for p in got)
    with pytest.raises(NamespaceAccessError):
        retriever.retrieve(RetrievalIntent(query=q, namespaces=["test"]), "question_planner")


def test_evaluator_cannot_see_candidate_job_or_question(retriever):
    q = "Kafka consistency Backend engineer candidate migration two numbers target"
    got = retriever.retrieve(RetrievalIntent(query=q, k=20), "evaluator")
    assert {p.namespace for p in got} <= {"rubric", "technical"}
    assert not {"cand-1", "job-1", "tih-q1"} & set(ids(got))
    with pytest.raises(NamespaceAccessError):
        retriever.retrieve(RetrievalIntent(query=q, namespaces=["candidate"]), "evaluator")


def test_recommender_and_planner_scopes(retriever):
    q = "Kafka consistency Backend engineer candidate migration"
    rec = retriever.retrieve(RetrievalIntent(query=q, k=20, candidate_id="cand-A"), "recommender")
    assert {p.namespace for p in rec} <= {"job", "candidate"} and {"cand-1", "job-1"} <= set(
        ids(rec)
    )
    plan = retriever.retrieve(RetrievalIntent(query=q, k=20), "question_planner")
    assert "rub-1" not in ids(plan)  # planner has no rubric access


def test_namespace_narrowing_and_filters(retriever):
    only_q = retriever.retrieve(
        RetrievalIntent(query="array interval numbers", namespaces=["question"], k=10),
        "question_planner",
    )
    assert only_q and {p.namespace for p in only_q} == {"question"}
    by_dom = retriever.retrieve(
        RetrievalIntent(
            query="queries ORM dependency", filters=Filters(domain="web-backend"), k=10
        ),
        "question_planner",
    )
    assert by_dom and {p.source_name for p in by_dom} == {"fastapi"}
    by_src = retriever.retrieve(
        RetrievalIntent(query="cache blocks batching", filters=Filters(source_name=["vllm"]), k=10),
        "question_planner",
        mode="dense",
    )
    assert {p.source_name for p in by_src} == {"vllm"}
    by_tech = retriever.retrieve(
        RetrievalIntent(query="blocks", filters=Filters(related_technologies=["VLLM"]), k=10),
        "question_planner",
    )
    assert set(ids(by_tech)) <= {"vllm-a1", "vllm-a2"} and by_tech  # case-insensitive tag match


def test_difficulty_filter_lenient_and_strict(retriever):
    q = "array numbers intervals ranges target"
    strict = retriever.retrieve(
        RetrievalIntent(
            query=q, namespaces=["question"], difficulty="easy", strict_attrs=True, k=10
        ),
        "question_planner",
    )
    assert set(ids(strict)) == {"tih-q1"}
    lenient = retriever.retrieve(
        RetrievalIntent(query=q, difficulty="Easy", k=20), "question_planner"
    )
    assert "tih-q2" not in ids(lenient) and "tih-q1" in ids(
        lenient
    )  # Medium excluded; null-difficulty rows allowed


def test_filter_values_are_bound_parameters(retriever):
    evil = "x'; DROP TABLE chunks; --"
    got = retriever.retrieve(
        RetrievalIntent(query="kv cache", filters=Filters(domain=evil, source_name=evil), k=5),
        "question_planner",
    )
    assert got == []
    assert retriever.retrieve(
        RetrievalIntent(query="kv cache blocks", k=3), "question_planner"
    )  # table still there


def test_migration_002_dropped_tsv_en_and_is_recorded(loaded):
    with dbmod.connect(loaded) as c:
        cols = {
            r[0]
            for r in c.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'chunks'"
            )
        }
        assert "tsv" in cols and "tsv_en" not in cols
        assert sorted(r[0] for r in c.execute("SELECT version FROM schema_migrations")) == [1, 2, 3]
    assert "already current" in dbmod.migrate(loaded)


def test_final_retrieval_defaults():
    from rag.retrieval import retriever as R

    r = R.Retriever()
    assert (r.top_n, r.rrf_k, r.rrf_weights, r.strict_bonus, r.ident_bonus, r.rerank_n) == (
        50,
        10,
        {"dense": 2.0, "lexical": 1.0},
        10.0,
        0.0,
        10,
    )


def test_rerank_stage_with_fake_reranker(loaded):
    class FakeReranker:
        def score(self, query, texts):
            return [1.0 if "intervals" in t else 0.0 for t in texts]

    r = Retriever(dsn=loaded, embedder=FakeEmbedder(), reranker=FakeReranker(), rerank_n=10)
    try:
        out = r.retrieve(
            RetrievalIntent(query="numbers target ranges", k=2), "question_planner", rerank=True
        )
        assert out[0].chunk_id == "tih-q2" and out[0].scores["rerank"] == 1.0
        assert [p.rank for p in out] == [1, 2]
    finally:
        r.close()


# ---------------- confidence signals ----------------


def test_result_carries_confidence_signals(loaded):
    from rag.retrieval.confidence import ConfidenceRule

    # the bag-of-words fake embedder has much lower cosines than bge-m3, so use a rule scaled to it
    r = Retriever(
        dsn=loaded,
        embedder=FakeEmbedder(),
        confidence_rule=ConfidenceRule(dense_low=0.2, dense_high=0.35),
    )
    try:
        res = r.retrieve(
            RetrievalIntent(query="How does PagedAttention reduce KV cache fragmentation?", k=3),
            "question_planner",
        )
    finally:
        r.close()
    assert isinstance(res, list) and res[0].chunk_id == "vllm-a1"
    assert res.max_dense is not None and res.max_dense > 0.3 and res.max_rerank is None
    assert res.lexical_hit is True and res.confidence == "high" and res.low_confidence is False


def test_negative_queries_are_low_confidence(retriever):
    for q in (
        "sourdough starter feeding schedule",
        "who won the 2018 football world cup",
        "???",
        "how does it work",
        "asdkjh qwoeiru zxmnb",
    ):
        res = retriever.retrieve(RetrievalIntent(query=q, k=3), "question_planner")
        assert res.low_confidence is True and res.confidence == "low", (q, res.signals())


def test_non_ascii_query_is_dense_only_lexical_hit_false(retriever):
    res = retriever.retrieve(RetrievalIntent(query="数据库如何保持一致性", k=3), "question_planner")
    assert res.lexical_hit is False
    assert any("non-ASCII" in r for r in res.reasons)
    # mixed query: the ASCII tokens still reach lexical search
    res2 = retriever.retrieve(
        RetrievalIntent(query="PagedAttention 如何减少碎片", k=3), "question_planner"
    )
    assert res2.lexical_hit is True


def test_min_rerank_score_cutoff(loaded):
    class FakeReranker:
        def score(self, query, texts):
            return [0.9 if "intervals" in t else 0.1 for t in texts]

    r = Retriever(dsn=loaded, embedder=FakeEmbedder(), reranker=FakeReranker(), rerank_n=10)
    try:
        intent = RetrievalIntent(query="numbers target ranges intervals", k=5)
        all_ = r.retrieve(intent, "question_planner", rerank=True)
        cut = r.retrieve(intent, "question_planner", rerank=True, min_rerank_score=0.5)
        assert len(all_) > len(cut) >= 1 and all(p.scores["rerank"] >= 0.5 for p in cut)
        assert cut.max_rerank == all_.max_rerank == 0.9  # signal is computed before the cut-off
        r.min_rerank_score = 0.95
        assert len(r.retrieve(intent, "question_planner", rerank=True)) == 0
    finally:
        r.close()


def test_nul_bytes_do_not_reach_sql(retriever):
    res = retriever.retrieve(
        RetrievalIntent(query="kv\x00 cache", filters=Filters(domain="ml-serving\x00")),
        "question_planner",
    )
    assert res and res[0].chunk_id.startswith("vllm")


# ---------------- real index: negative queries (needs the populated DB + cached bge-m3)
# ----------------


def _real_index_ready() -> bool:
    try:
        with dbmod.connect(BASE_DSN) as c:
            return c.execute("SELECT count(*) FROM chunks").fetchone()[0] > 16000
    except Exception:
        return False


@pytest.mark.skipif(not _real_index_ready(), reason="real index not loaded")
def test_real_index_negative_queries_are_flagged_low():
    import yaml

    from rag.index.embed import Embedder

    try:
        emb = Embedder()
    except Exception as exc:  # pragma: no cover - model not available offline
        pytest.skip(f"embedding model unavailable: {exc}")
    doc = yaml.safe_load((config.DATA_DIR / "eval" / "negative_queries.yaml").read_text())
    r = Retriever(dsn=BASE_DSN, embedder=emb)
    try:
        lv = {
            x["id"]: r.retrieve(
                RetrievalIntent(query=x["query"], k=5), "question_planner"
            ).confidence
            for x in doc["negative"]
        }
        low = sum(v == "low" for v in lv.values())
        assert "high" not in lv.values(), lv
        assert low >= 17, (  # 18 -> 16 (batch 1) -> 15 (batch 2) -> 18 (dense_low 0.55)
            f"only {low}/{len(lv)} negatives flagged low: {lv}"
        )  # measured 18/22 without rerank
        for q in (
            "Explain MVCC in PostgreSQL",
            "difference between select_related and prefetch_related",
            "How does PagedAttention reduce KV cache fragmentation?",
        ):
            assert not r.retrieve(
                RetrievalIntent(query=q, k=5), "question_planner"
            ).low_confidence, q
    finally:
        r.close()


@pytest.mark.skipif(not _real_index_ready(), reason="real index not loaded")
def test_real_index_rare_identifier_found_and_absent_identifier_is_low():
    """Dense neighbours of an unfamiliar name are noise: a rare identifier that exists must still
    surface (adaptive lexical weight), and one that exists nowhere must be flagged low."""
    from rag.index.embed import Embedder

    try:
        emb = Embedder()
    except Exception as exc:  # pragma: no cover - model not available offline
        pytest.skip(f"embedding model unavailable: {exc}")
    r = Retriever(dsn=BASE_DSN, embedder=emb)
    try:
        for q in ("HyperLogLog", "CORS preflight"):
            res = r.retrieve(RetrievalIntent(query=q, k=10), "question_planner")
            word = q.split()[0].lower()
            assert any(word in p.text.lower() for p in res), q
        res = r.retrieve(RetrievalIntent(query="TeaCache", k=10), "question_planner")
        assert res.low_confidence and any("not found verbatim" in x for x in res.reasons)
        # a hyphenated plain word is not a code identifier: no absent-identifier verdict
        res = r.retrieve(
            RetrievalIntent(query="low-dimensional adapter matrices", k=5), "question_planner"
        )
        assert not any("not found verbatim" in x for x in res.reasons)
    finally:
        r.close()


@pytest.mark.skipif(not _real_index_ready(), reason="real index not loaded")
def test_real_index_capitalised_words_do_not_create_lexical_hits():
    r = Retriever(dsn=BASE_DSN, check_model=False)
    try:
        for q in (
            "how to LIMIT my calorie intake",
            "ORDER a birthday cake online",
            "TABLE manners at a formal dinner",
            "HTTPS NVIDIA GPUs APIs IDs INDEX",
        ):
            res = r.retrieve(RetrievalIntent(query=q, k=3), "question_planner", mode="lexical")
            assert res.lexical_hit is False, (q, res.signals())
        for q in (
            "difference between select_related and prefetch_related",
            "XREADGROUP",
            "KStream",
        ):
            assert (
                r.retrieve(
                    RetrievalIntent(query=q, k=3), "question_planner", mode="lexical"
                ).lexical_hit
                is True
            ), q
    finally:
        r.close()


# ---------------- pin the production dense plan (real index only) ----------------


@pytest.mark.skipif(
    not _real_index_ready(), reason="real index not loaded (plan depends on table size/stats)"
)
def test_real_index_dense_plan_is_pinned_to_exact_search():
    """Production dense search is EXACT: `Retriever(exact_dense=True)` (the default) disables index
    scans inside the dense transaction, so the role-default dense query is a sequential scan + sort
    at any corpus size (at ~64k rows the planner would otherwise choose the incrementally built
    HNSW index, whose overlap@10 with the exact result fell to 0.93). Applied by the same code
    path as retrieve(); this pins it so a regression is visible instead of silently making
    retrieval approximate."""
    from rag.retrieval.retriever import plan_scan_type

    r = Retriever(dsn=BASE_DSN, check_model=False)
    try:
        assert r.hnsw_ef_search == 100, (
            "production ef_search changed; re-measure plan + overlap and update README/BACKLOG"
        )
        msg = (
            "Dense plan for {what} is now {got!r}, expected {want!r}. If this is 'hnsw', "
            "production dense search "
            "has become APPROXIMATE (measured overlap@10 0.93, min 0.2 vs exact at "
            "ef_search=100 on 64k rows). "
            "That may be fine, but update README + BACKLOG + this test deliberately.\nPlan:\n{plan}"
        )
        for role, want in (("question_planner", "seqscan"), ("evaluator", "seqscan")):
            plan = r.explain_dense(RetrievalIntent(query="x"), role)
            got = plan_scan_type(plan)
            assert got == want, msg.format(
                what=f"role {role}", got=got, want=want, plan="\n".join(plan[:6])
            )
        plan = r.explain_dense(
            RetrievalIntent(query="x", filters=Filters(source_name="paper-lora")),
            "question_planner",
        )
        got = plan_scan_type(plan)
        assert got != "hnsw", msg.format(
            what="a selective source_name filter",
            got=got,
            want="an exact plan",
            plan="\n".join(plan[:6]),
        )
    finally:
        r.close()


def test_explain_dense_uses_production_session_settings(loaded):
    """explain_dense goes through the same transaction/settings code as retrieve(): a planner
    override applied there must be visible in the plan (forcing seqscan off on a tiny table still
    yields an index plan or error-free plan)."""
    from rag.retrieval.retriever import plan_scan_type

    r = Retriever(dsn=loaded, embedder=FakeEmbedder(), check_model=False)
    try:
        plan = r.explain_dense(RetrievalIntent(query="x"), "question_planner")
        assert plan_scan_type(plan) in ("seqscan", "hnsw", "other-exact")
        r.planner_overrides = {"enable_seqscan": "off", "enable_sort": "off"}
        # still executes fine under the overrides
        assert r.retrieve(
            RetrievalIntent(query="kv cache blocks", k=2), "question_planner", mode="dense"
        )
    finally:
        r.close()


# ---------------- HNSW + iterative scan (forced plan) ----------------


def test_hnsw_iterative_scan_with_restrictive_filter(hnsw_dsn):
    """At 16k rows the planner often picks an exact scan, so force the HNSW path and check that a
    very restrictive filter (1% of rows) still yields top_n rows -- or as many as match -- thanks to
    hnsw.iterative_scan (pgvector >= 0.8)."""
    rng = np.random.default_rng(7)
    n_tech, n_q = 3000, 30
    with dbmod.connect(hnsw_dsn) as c:
        ver = c.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[
            0
        ]
        L.sync_sources(c, [s for s in load_registry(config.SOURCES_YAML) if s.name == "vllm"])
        vecs = rng.normal(size=(n_tech + n_q, DIM)).astype(np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        rows = []
        for i, v in enumerate(vecs):
            ns = "technical" if i < n_tech else "question"
            ch = mk(f"vllm-h{i}", ns, f"filler text number {i}", "vllm")
            rows.append(L.chunk_to_row(ch, v, f"hash{i}", "BAAI/bge-m3"))
        with c.cursor() as cur:
            cur.executemany(L._upsert_chunks_sql(), rows)
        c.execute("ANALYZE chunks")
    qvec = vecs[n_tech + 3]  # a point inside the restricted subset

    class QEmb:
        meta = META

        def encode(self, texts, batch_size=None):
            return np.stack([qvec for _ in texts])

    force = {
        "enable_seqscan": "off",
        "enable_sort": "off",
        "enable_bitmapscan": "off",
        "enable_indexscan": "on",
    }
    r = Retriever(dsn=hnsw_dsn, embedder=QEmb(), top_n=50, planner_overrides=force)
    try:
        intent = RetrievalIntent(query="anything", namespaces=["question"], k=50)
        # 1. the forced plan really is the HNSW index
        from rag.retrieval.sql import build_filter_clause, dense_sql

        where, params = build_filter_clause(intent, ("question",))
        params.update(qvec=qvec, top_n=50)
        with r.conn.transaction():
            for k, v in force.items():
                r.conn.execute("SELECT set_config(%s, %s, true)", (k, v))
            plan = "\n".join(x[0] for x in r.conn.execute("EXPLAIN " + dense_sql(where), params))
        assert "chunks_embedding_hnsw" in plan, plan

        if tuple(int(x) for x in ver.split(".")[:2]) >= (0, 8):
            # 2. without iterative scan the filtered HNSW scan comes back short (the problem being
            # solved)
            with r.conn.transaction():
                for k, v in force.items():
                    r.conn.execute("SELECT set_config(%s, %s, true)", (k, v))
                r.conn.execute("SELECT set_config('hnsw.ef_search', '40', true)")
                r.conn.execute("SELECT set_config('hnsw.iterative_scan', 'off', true)")
                short = r.conn.execute(dense_sql(where), params).fetchall()
            assert len(short) < n_q, (
                f"expected a short result without iterative scan, got {len(short)}"
            )
            # 3. production path: iterative scan on -> every matching row (30 < top_n 50)
            got = r.retrieve(intent, "question_planner", mode="dense")
            # HNSW graph traversal need not reach every node, so "all matching rows" is not
            # guaranteed:
            # observed 27/30. Require a large majority, and strictly more than without iterative
            # scan.
            assert (
                len(got) >= 0.8 * n_q
                and len(got) > len(short)
                and all(p.namespace == "question" for p in got)
            ), (len(got), len(short))
            assert got[0].chunk_id == f"vllm-h{n_tech + 3}"
            print(
                f"HNSW forced: filtered rows without iterative_scan={len(short)}, with={len(got)} "
                f"of {n_q} matching"
            )
            # 4. top_n smaller than matches -> exactly top_n
            r.top_n = 10
            got10 = r.retrieve(
                RetrievalIntent(query="anything", namespaces=["question"], k=10),
                "question_planner",
                mode="dense",
            )
            assert len(got10) == 10
        else:  # pragma: no cover
            pytest.skip(f"pgvector {ver} < 0.8 has no iterative scan")
    finally:
        r.close()


# ---------------- candidate isolation (migration 003) ----------------


def test_candidate_chunks_are_only_returned_to_their_owner(retriever):
    q = "Candidate migration of the consistency layer with Kafka"
    for owner, mine, other in (("cand-A", "cand-1", "cand-2"), ("cand-B", "cand-2", "cand-1")):
        intent = RetrievalIntent(query=q, namespaces=["candidate"], candidate_id=owner, k=20)
        for mode in ("dense", "lexical", "hybrid"):
            got = retriever.retrieve(intent, "question_planner", mode=mode)
            assert ids(got) == [mine] and other not in ids(got), (owner, mode)
            assert all(p.candidate_id == owner for p in got)


def test_candidate_namespace_fails_closed_without_candidate_id(retriever):
    q = "Candidate migration of the consistency layer with Kafka"
    for cid in (None, "", "   "):
        got = retriever.retrieve(
            RetrievalIntent(query=q, namespaces=["candidate"], candidate_id=cid), "question_planner"
        )
        assert list(got) == [] and got.low_confidence
    # default role namespaces: candidate is silently dropped, other namespaces still answer
    default = retriever.retrieve(RetrievalIntent(query=q, k=20), "question_planner")
    assert {"cand-1", "cand-2"}.isdisjoint(ids(default)) and len(default) > 0


def test_candidate_id_never_widens_other_namespaces_and_unknown_owner_sees_nothing(retriever):
    q = "Candidate migration of the consistency layer with Kafka"
    none = retriever.retrieve(
        RetrievalIntent(query=q, namespaces=["candidate"], candidate_id="cand-Z"),
        "question_planner",
    )
    assert list(none) == []
    mixed = retriever.retrieve(
        RetrievalIntent(query=q, candidate_id="cand-A", k=20), "question_planner"
    )
    assert "cand-1" in ids(mixed) and "cand-2" not in ids(mixed)


def test_candidate_id_is_a_bound_parameter(retriever):
    evil = "cand-A' OR '1'='1"
    got = retriever.retrieve(
        RetrievalIntent(query="consistency Kafka", namespaces=["candidate"], candidate_id=evil),
        "question_planner",
    )
    assert list(got) == []


def test_database_rejects_unowned_candidate_chunks_and_owned_non_candidate_chunks(loaded):
    import psycopg

    base = (
        "INSERT INTO chunks (chunk_id, namespace, text, source_name, source_type, source_url, "
        "trust_level, license, token_count, text_hash, embedding_model, embedding, candidate_id) "
        "SELECT 'bad-1', %s, 'x', 'vllm', 'git_docs', 'u', 'official', 'MIT', 1, 'h', 'm', "
        "embedding, %s FROM chunks LIMIT 1"
    )
    with psycopg.connect(loaded) as c:
        for ns, owner in (("candidate", None), ("candidate", ""), ("technical", "cand-A")):
            with pytest.raises(psycopg.errors.CheckViolation):
                with c.transaction():
                    c.execute(base, (ns, owner))


# ---------------- pooled backend + adapter against a real database ----------------


@pytest.fixture()
def pooled(loaded):
    """PooledRetriever on the throwaway DB; the model is replaced by the bag-of-words embedder."""
    pytest.importorskip("psycopg_pool")
    from rag.retrieval.pool import PooledRetriever

    backend = PooledRetriever(loaded, pool_min=1, pool_max=4, statement_timeout_ms=300)
    backend._embedder = FakeEmbedder()  # same identity as the stored index_meta
    backend.open(wait_timeout=5)
    yield backend
    backend.close()


async def test_adapter_over_real_pool_isolates_two_candidates(pooled):
    import asyncio

    from rag.adapter import RAGAdapter
    from shared.contracts import RetrievalSource
    from tests.rag.adapter_helpers import make_request

    adapter = RAGAdapter(pooled)
    query = "Candidate migration of the consistency layer with Kafka"

    async def ask(candidate_id):
        response = await adapter.retrieve(
            make_request(RetrievalSource.CANDIDATE, query=query, candidate_id=candidate_id)
        )
        return [c.chunk_id for c in response.chunks]

    # concurrent calls through one pool: no cross-talk between owners
    a, b, z, none, blank = await asyncio.gather(
        ask("cand-A"), ask("cand-B"), ask("cand-Z"), ask(None), ask("   ")
    )
    assert a == ["cand-1"] and b == ["cand-2"]
    assert z == [] and none == [] and blank == []
    # whitespace around the id is normalised (pinned): " cand-B " is cand-B, not a new identity
    assert await ask(" cand-B ") == ["cand-2"]
    # NUL bytes are refused, never stripped into another candidate's id
    assert await ask("cand-\x00B") == [] and await ask("\x00") == []
    # other sources never return candidate chunks
    tech = await adapter.retrieve(
        make_request(RetrievalSource.TECHNICAL, query=query, candidate_id="cand-A", top_k=20)
    )
    assert not {"cand-1", "cand-2"} & {c.chunk_id for c in tech.chunks}


def test_pooled_connections_carry_server_side_timeouts(pooled):
    import time

    with pooled._pool.connection() as conn:
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "300ms"
        assert conn.execute("SHOW lock_timeout").fetchone()[0] == "1s"
        started = time.perf_counter()
        with pytest.raises(psycopg.errors.QueryCanceled):
            conn.execute("SELECT pg_sleep(5)")
        assert time.perf_counter() - started < 2
    with pooled._pool.connection() as conn:  # the connection is reusable after the cancel
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_warmup_touches_the_database_and_checks_the_index(pooled):
    pooled.warmup()
    assert pooled.known_domains()
    assert pooled._meta_checked


def test_warmup_primes_caches_with_real_retrievals_without_the_statement_timeout(
    pooled, monkeypatch
):
    from rag.retrieval import pool as pool_module
    from rag.retrieval.retriever import Retriever

    seen: list[tuple[str, str]] = []
    real = Retriever.retrieve

    def spy(self, intent, role, **kwargs):
        # what the cold scan would be cancelled by: the pool's own limit is 300 ms in this fixture
        seen.append((intent.query, self.conn.execute("SHOW statement_timeout").fetchone()[0]))
        return real(self, intent, role, **kwargs)

    monkeypatch.setattr(Retriever, "retrieve", spy)
    pooled.warmup()
    assert [q for q, _ in seen] == list(pool_module.WARMUP_QUERIES)
    assert {t for _, t in seen} == {"2min"}  # SET LOCAL statement_timeout = '120s'
    with pooled._pool.connection() as conn:  # lifted for that transaction only
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "300ms"


def test_warmup_survives_a_failing_cache_priming_query(pooled, monkeypatch, caplog):
    import logging

    from rag.retrieval.retriever import Retriever

    def boom(self, intent, role, **kwargs):
        raise RuntimeError("cold scan failed")

    monkeypatch.setattr(Retriever, "retrieve", boom)
    with caplog.at_level(logging.WARNING, logger="rag.retrieval.pool"):
        pooled.warmup()  # must not raise: RAG still starts
    assert pooled.known_domains() and pooled._meta_checked
    assert any("cache warm-up failed" in r.message for r in caplog.records)
    with pooled._pool.connection() as conn:  # the failed transaction left a usable connection
        assert conn.execute("SELECT 1").fetchone()[0] == 1
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "300ms"
