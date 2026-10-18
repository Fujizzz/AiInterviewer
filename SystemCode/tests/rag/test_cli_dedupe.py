import pytest

# Optional rag-extra dependencies: skip the whole module when they are not installed.
pytest.importorskip("requests")
import json

from rag import cli
from rag.loaders.base import FetchResult
from rag.registry import Source
from rag.schema import Chunk


def chunk(slug, cid):
    return Chunk(
        chunk_id=cid,
        namespace="question",
        text="t " + slug,
        source_name="x",
        source_type="dataset",
        source_url="u",
        trust_level="curated",
        license="l",
        token_count=3,
        ingested_at="now",
        metadata={"slug": slug},
    )


def src():
    return Source(
        name="python-ds",
        namespace="question",
        priority="P0",
        url="u",
        loader="hf",
        license="l",
        auto_level="full",
        trust_level="community",
        refresh_cycle="q",
        domain="d",
        options={"dedupe_against": "primary"},
    )


def test_dedupe_against_primary_and_provenance_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CHUNKS_DIR", tmp_path)
    rows = [
        {"chunk_id": "p-1", "metadata": {"slug": "two-sum"}},
        {"chunk_id": "p-2", "metadata": {"slug": "add-two"}},
    ]
    (tmp_path / "primary.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    fr = FetchResult(source_name="python-ds")
    kept, overlap = cli._dedupe_against_primary(
        src(), "primary", [chunk("two-sum", "a"), chunk("brand-new", "b")], fr
    )
    assert [c.metadata["slug"] for c in kept] == ["brand-new"] and overlap == ["two-sum"]
    assert "1 of 2" in fr.notes[0]
    cli._merge_provenance("primary", overlap, "python-ds")
    cli._merge_provenance("primary", overlap, "python-ds")  # idempotent
    out = [json.loads(line) for line in (tmp_path / "primary.jsonl").read_text().splitlines()]
    assert (
        out[0]["metadata"]["also_in_sources"] == ["python-ds"]
        and "also_in_sources" not in out[1]["metadata"]
    )


def test_missing_primary_is_reported_not_silently_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CHUNKS_DIR", tmp_path)
    fr = FetchResult(source_name="python-ds")
    kept, overlap = cli._dedupe_against_primary(src(), "primary", [chunk("a", "1")], fr)
    assert len(kept) == 1 and overlap == [] and "NO cross-source dedupe" in fr.notes[0]
