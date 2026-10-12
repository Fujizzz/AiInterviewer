"""Chunk schema shared by all loaders/chunkers.

One JSON object per line in data/chunks/<source>.jsonl must validate against
the `Chunk` model below before it is written to disk. This keeps the
downstream pgvector indexing step (out of scope here) honest about what it
can expect.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - tiktoken optional
    _ENC = None


class Namespace(str, Enum):
    CANDIDATE = "candidate"
    JOB = "job"
    TECHNICAL = "technical"
    RUBRIC = "rubric"
    QUESTION = "question"
    TEST = "test"


class SourceType(str, Enum):
    GIT_DOCS = "git_docs"
    DATASET = "dataset"
    PAPER = "paper"
    CRAWL = "crawl"
    MANUAL = "manual"


class TrustLevel(str, Enum):
    OFFICIAL = "official"
    CURATED = "curated"
    COMMUNITY = "community"
    REFERENCE_ONLY = "reference_only"


def count_tokens(text: str) -> int:
    """Best-effort token count; falls back to len//4 when tiktoken is absent.

    `disallowed_special=()` tells tiktoken to treat every substring as plain
    text instead of raising on tokens that look like special tokens (e.g.
    literal "<|endoftext|>" showing up inside scraped docs/code) — we only
    ever use this for counting, never for building model input, so there is
    no reason to reject real-world text.
    """
    if not text:
        return 0
    if _ENC is not None:
        try:
            return len(_ENC.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4)


def make_chunk_id(source: str, path: str, heading_path: list[str], idx: int) -> str:
    """Deterministic, stable chunk id.

    Re-running the pipeline on unchanged input must produce the same id so
    that a future upsert-based indexer can diff cleanly.
    """
    key = f"{path}|{'/'.join(heading_path)}|{idx}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"{source}-{digest}"


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Chunk(BaseModel):
    chunk_id: str
    namespace: Namespace
    text: str
    title: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    source_name: str
    source_type: SourceType
    source_url: str
    doc_version: str | None = None
    domain: str | None = None
    topic: str | None = None
    related_technologies: list[str] = Field(default_factory=list)
    competency: str | None = None
    difficulty: str | None = None
    trust_level: TrustLevel
    license: str
    token_count: int
    ingested_at: str
    # Free-form, source-specific extras (e.g. slug, tsc list, proficiency
    # levels, role_family). Optional and defaults to {} so older chunk files
    # and consumers stay compatible.
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def text_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text must be non-empty")
        return v

    @field_validator("chunk_id")
    @classmethod
    def chunk_id_non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("chunk_id must be non-empty")
        return v

    @field_validator("token_count")
    @classmethod
    def token_count_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("token_count must be >= 0")
        return v

    def to_jsonl_dict(self) -> dict:
        d = self.model_dump()
        d["namespace"] = self.namespace.value
        d["source_type"] = self.source_type.value
        d["trust_level"] = self.trust_level.value
        return d
