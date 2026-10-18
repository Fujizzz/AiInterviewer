"""The real int8 artifact against the real local index. Skipped unless ALL of these exist:

* numpy, onnxruntime, tokenizers, psycopg, psycopg_pool, pgvector installed;
* the artifact built with `python -m rag.cli encoder build-onnx` (rag/data/models/...);
* the local Postgres index (docker compose up -d, migrated, loaded with the bge-m3 corpus).

No torch involved: the query vector is compared with the corpus vectors that torch made.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
for _dep in ("onnxruntime", "tokenizers", "psycopg", "psycopg_pool", "pgvector"):
    pytest.importorskip(_dep)

import psycopg  # noqa: E402

from rag.adapter import create_rag_adapter  # noqa: E402
from rag.index import config  # noqa: E402
from rag.index.db import db_reachable  # noqa: E402
from rag.index.embed import build_embedding_text  # noqa: E402
from rag.index.onnx_encoder import (  # noqa: E402
    EncoderUnavailableError,
    OnnxQueryEncoder,
    default_model_dir,
    load_manifest,
    smoke_check,
    verify_artifact,
)
from rag.retrieval.intent import RetrievalIntent  # noqa: E402
from rag.retrieval.retriever import Retriever  # noqa: E402
from rag.settings import RAGSettings  # noqa: E402
from shared.contracts import RetrievalSource  # noqa: E402
from tests.rag.adapter_helpers import make_request  # noqa: E402


def _artifact_dir():
    try:
        manifest = load_manifest()
        d = default_model_dir(manifest)
        verify_artifact(d, manifest, check_sha=False)
        return d
    except EncoderUnavailableError:
        return None


def _indexed() -> bool:
    if not db_reachable():
        return False
    try:
        with psycopg.connect(config.get_dsn(), connect_timeout=2) as conn:
            return conn.execute("SELECT count(*) FROM chunks").fetchone()[0] > 1000
    except psycopg.Error:
        return False


ARTIFACT = _artifact_dir()
needs_artifact = pytest.mark.skipif(ARTIFACT is None, reason="ONNX int8 artifact not built")
needs_index = pytest.mark.skipif(not _indexed(), reason="local Postgres index not available")
pytestmark = needs_artifact


@pytest.fixture(scope="module")
def encoder():
    return OnnxQueryEncoder(ARTIFACT, threads=2)  # also verifies the sha256 of every file


def test_artifact_matches_the_committed_manifest():
    verify_artifact(ARTIFACT, load_manifest(), check_sha=True)


def test_vectors_agree_with_the_committed_torch_reference(encoder):
    res = smoke_check(encoder)
    assert res["n"] >= 5 and res["mean"] > 0.97 and res["min"] > 0.95, res


def test_shape_norm_and_batch_independence(encoder):
    """A query's vector must not depend on the other texts in the call (default: one per pass)."""
    a = encoder.encode(["How does PagedAttention reduce KV cache fragmentation?", "解释 MVCC"])
    assert a.shape == (2, 1024) and a.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(a, axis=1), 1.0, atol=1e-5)
    np.testing.assert_array_equal(
        a[:1], encoder.encode(["How does PagedAttention reduce KV cache fragmentation?"])
    )
    np.testing.assert_array_equal(a[1:], encoder.encode(["解释 MVCC"]))


def test_long_input_is_truncated_not_rejected(encoder):
    vec = encoder.encode(["kv cache " * 2000])
    assert vec.shape == (1, 1024) and np.isfinite(vec).all()


@needs_index
def test_int8_query_vectors_sit_next_to_the_torch_corpus_vectors(encoder):
    with psycopg.connect(config.get_dsn()) as conn:
        rows = conn.execute(
            "SELECT text, title, heading_path, embedding::text FROM chunks "
            "WHERE namespace = 'technical' ORDER BY chunk_id LIMIT 12"
        ).fetchall()
    cos = []
    for text, title, heading, emb in rows:
        stored = np.array(emb.strip("[]").split(","), dtype=np.float32)
        q = encoder.encode(
            [build_embedding_text({"text": text, "title": title, "heading_path": heading})]
        )[0]
        cos.append(float(stored @ q / np.linalg.norm(stored)))
    assert min(cos) > 0.94 and np.mean(cos) > 0.97, cos


@needs_index
def test_retriever_accepts_the_derived_encoder_and_records_the_variant(encoder):
    r = Retriever(embedder=encoder)  # check_model=True: index_meta is compared with the manifest
    try:
        res = r.retrieve(
            RetrievalIntent(query="How does PagedAttention reduce KV cache fragmentation?", k=5),
            "question_planner",
        )
    finally:
        r.close()
    assert res.encoder == f"onnx-int8/{encoder.manifest.variant}"
    assert {p.source_name for p in res} & {"vllm", "paper-pagedattention-vllm"}
    assert res.confidence in {"medium", "high"}


@needs_index
async def test_adapter_with_the_onnx_encoder_end_to_end():
    adapter = create_rag_adapter(RAGSettings(encoder="onnx-int8", encoder_threads=2), warmup=True)
    try:
        response = await adapter.retrieve(
            make_request(
                RetrievalSource.TECHNICAL, query="Django select_related to avoid N+1", top_k=5
            )
        )
    finally:
        adapter.close()
    assert response.chunks and response.latency_ms is not None and response.latency_ms < 2000
    assert all(c.metadata["encoder"].startswith("onnx-int8/") for c in response.chunks)
    assert any("select_related" in c.content for c in response.chunks)


@needs_index
def test_a_non_matching_artifact_manifest_is_refused_by_the_index_check(encoder, tmp_path):
    """Same weights but a manifest claiming another base revision must not talk to this index."""
    import json

    from rag.index.load import IndexError_
    from rag.index.onnx_encoder import EncoderManifest

    raw = json.loads(json.dumps(encoder.manifest.raw))
    raw["base_revision"] = "0" * 40
    bad = OnnxQueryEncoder(ARTIFACT, manifest=EncoderManifest(raw), threads=1, verify=False)
    r = Retriever(embedder=bad)
    try:
        with pytest.raises(IndexError_, match="revision"):
            r.retrieve(RetrievalIntent(query="kv cache", k=3), "question_planner")
    finally:
        r.close()
