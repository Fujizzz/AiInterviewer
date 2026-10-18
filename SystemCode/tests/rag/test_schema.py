import pytest
from pydantic import ValidationError

from rag.schema import Chunk, count_tokens, make_chunk_id, utc_now_iso


def _valid_kwargs(**overrides):
    base = dict(
        chunk_id="src-abc123",
        namespace="technical",
        text="hello world",
        title="Hello",
        heading_path=["Intro"],
        source_name="src",
        source_type="git_docs",
        source_url="https://example.com",
        doc_version="abc123",
        domain="testing",
        topic="intro",
        related_technologies=["python"],
        competency=None,
        difficulty=None,
        trust_level="official",
        license="MIT",
        token_count=2,
        ingested_at=utc_now_iso(),
    )
    base.update(overrides)
    return base


def test_valid_chunk_passes():
    chunk = Chunk(**_valid_kwargs())
    assert chunk.namespace.value == "technical"
    assert chunk.trust_level.value == "official"


def test_empty_text_rejected():
    with pytest.raises(ValidationError):
        Chunk(**_valid_kwargs(text="   "))


def test_invalid_namespace_rejected():
    with pytest.raises(ValidationError):
        Chunk(**_valid_kwargs(namespace="not-a-real-namespace"))


def test_invalid_trust_level_rejected():
    with pytest.raises(ValidationError):
        Chunk(**_valid_kwargs(trust_level="definitely-trustworthy"))


def test_negative_token_count_rejected():
    with pytest.raises(ValidationError):
        Chunk(**_valid_kwargs(token_count=-1))


def test_to_jsonl_dict_uses_plain_strings_for_enums():
    chunk = Chunk(**_valid_kwargs())
    d = chunk.to_jsonl_dict()
    assert d["namespace"] == "technical"
    assert d["source_type"] == "git_docs"
    assert d["trust_level"] == "official"
    assert isinstance(d["namespace"], str)


def test_count_tokens_nonempty_text():
    assert count_tokens("") == 0
    assert count_tokens("hello") > 0


def test_make_chunk_id_deterministic():
    id1 = make_chunk_id("src", "path/a.md", ["H1", "H2"], 0)
    id2 = make_chunk_id("src", "path/a.md", ["H1", "H2"], 0)
    id3 = make_chunk_id("src", "path/a.md", ["H1", "H2"], 1)
    assert id1 == id2
    assert id1 != id3
    assert id1.startswith("src-")
