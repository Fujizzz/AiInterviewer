"""Candidate-isolation primitives that need no database (SQL builder, scoping, Chunk schema)."""

import pytest

from rag.retrieval.access import scope_namespaces
from rag.retrieval.intent import RetrievalIntent
from rag.retrieval.sql import FETCH_SQL, build_filter_clause
from rag.schema import Chunk


def test_scope_drops_candidate_without_owner_and_keeps_it_with_one():
    ns = ("candidate", "technical")
    assert scope_namespaces(ns, None) == ("technical",)
    assert scope_namespaces(ns, "") == ("technical",)
    assert scope_namespaces(ns, "   ") == ("technical",)
    assert scope_namespaces(("candidate",), None) == ()
    assert scope_namespaces(ns, "c1") == ns


def test_filter_clause_refuses_candidate_namespace_without_owner():
    intent = RetrievalIntent(query="kafka")
    with pytest.raises(ValueError, match="candidate_id"):
        build_filter_clause(intent, ["candidate", "technical"])


def test_filter_clause_binds_owner_and_never_inlines_it():
    intent = RetrievalIntent(query="kafka", candidate_id="c1'; DROP TABLE chunks;--")
    where, params = build_filter_clause(intent, ["candidate", "technical"])
    assert "DROP" not in where and params["cand"] == intent.candidate_id
    assert params["ns"] == ["technical"]  # candidate is only reachable through the owner branch
    assert "c.candidate_id = %(cand)s" in where


def test_filter_clause_without_candidate_namespace_has_no_owner_branch():
    where, params = build_filter_clause(
        RetrievalIntent(query="kafka", candidate_id="c1"), ["technical"]
    )
    assert "candidate" not in where and "cand" not in params


def test_hydrate_sql_is_owner_scoped():
    assert "candidate_id = %(cand)s" in FETCH_SQL


def _chunk(**kw):
    base = dict(
        chunk_id="x-1",
        namespace="technical",
        text="t",
        source_name="s",
        source_type="manual",
        source_url="u",
        trust_level="curated",
        license="MIT",
        token_count=1,
        ingested_at="2026-01-01T00:00:00Z",
    )
    return Chunk(**{**base, **kw})


def test_chunk_schema_requires_owner_for_candidate_chunks_only():
    assert _chunk(namespace="candidate", candidate_id="c1").candidate_id == "c1"
    with pytest.raises(ValueError):
        _chunk(namespace="candidate")
    with pytest.raises(ValueError):
        _chunk(namespace="candidate", candidate_id="  ")
    with pytest.raises(ValueError):
        _chunk(namespace="technical", candidate_id="c1")


def test_non_candidate_chunk_files_stay_unchanged():
    assert "candidate_id" not in _chunk().to_jsonl_dict()
    assert _chunk(namespace="candidate", candidate_id="c1").to_jsonl_dict()["candidate_id"] == "c1"


def test_intent_normalises_whitespace_in_candidate_id_and_rejects_nul():
    assert RetrievalIntent(query="q", candidate_id="  c1 ").candidate_id == "c1"
    assert RetrievalIntent(query="q", candidate_id="   ").candidate_id is None
    assert RetrievalIntent(query="q", candidate_id=None).candidate_id is None
    for bad in ("c\x001", "\x00", " \x00 "):
        with pytest.raises(ValueError, match="NUL"):
            RetrievalIntent(query="q", candidate_id=bad)
