"""`python -m rag.cli index`: idempotent load of sources + chunks + vectors into Postgres.

Guarantees:
  * upsert by chunk_id (re-running changes nothing; unchanged rows are not rewritten)
  * rows whose chunk_id vanished from data/chunks are deleted
  * refuses to run if the embedding cache is stale vs the chunk file, or if the
    embedding model/revision/format differs from what is already stored
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from ..registry import Source, load_registry
from . import config
from .db import connect
from .embed import (
    chunk_hash,
    load_cache,
    meta_compatible,
    read_chunks,
)


class IndexError_(RuntimeError):
    """Raised when loading would corrupt the index (model mismatch, stale cache...)."""


CHUNK_COLS = [
    "chunk_id",
    "namespace",
    "text",
    "title",
    "heading_path",
    "source_name",
    "source_type",
    "source_url",
    "doc_version",
    "domain",
    "topic",
    "related_technologies",
    "competency",
    "difficulty",
    "trust_level",
    "license",
    "token_count",
    "ingested_at",
    "metadata",
    "candidate_id",
    "text_hash",
    "embedding_model",
    "embedding",
]
# columns compared to decide whether an existing row needs rewriting
_COMPARE = [c for c in CHUNK_COLS if c not in ("chunk_id", "text")]


def _upsert_chunks_sql() -> str:
    cols = ", ".join(CHUNK_COLS)
    ph = ", ".join(f"%({c})s" for c in CHUNK_COLS)
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in CHUNK_COLS if c != "chunk_id")
    old = ", ".join(f"chunks.{c}" for c in _COMPARE + ["text"])
    new = ", ".join(f"EXCLUDED.{c}" for c in _COMPARE + ["text"])
    return (
        f"INSERT INTO chunks ({cols}) VALUES ({ph}) "
        f"ON CONFLICT (chunk_id) DO UPDATE SET {sets} "
        f"WHERE ({old}) IS DISTINCT FROM ({new})"
    )


def chunk_to_row(chunk: dict, vector: np.ndarray, text_hash_: str, model: str) -> dict:
    row = {c: chunk.get(c) for c in CHUNK_COLS if c in chunk}
    row["heading_path"] = list(chunk.get("heading_path") or [])
    row["related_technologies"] = list(chunk.get("related_technologies") or [])
    row["metadata"] = Jsonb(chunk.get("metadata") or {})
    row["ingested_at"] = _parse_ts(chunk.get("ingested_at"))
    row["text_hash"] = text_hash_
    row["embedding_model"] = model
    row["embedding"] = np.asarray(vector, dtype=np.float32)
    for c in CHUNK_COLS:
        row.setdefault(c, None)
    return row


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def source_to_row(s: Source) -> dict:
    return {
        "name": s.name,
        "namespace": s.namespace,
        "priority": s.priority,
        "url": s.url or None,
        "loader": s.loader,
        "license": s.license,
        "auto_level": s.auto_level,
        "trust_level": s.trust_level,
        "refresh_cycle": s.refresh_cycle,
        "domain": s.domain,
        "related_technologies": list(s.related_technologies or []),
        "reference_only": bool(s.reference_only),
        "verified": bool(s.verified),
        "clean_profile": s.clean_profile,
        "notes": s.notes,
        "options": Jsonb(s.options or {}),
    }


_SOURCE_SQL = """
INSERT INTO sources (name, namespace, priority, url, loader, license, auto_level, trust_level,
    refresh_cycle, domain, related_technologies, reference_only, verified, clean_profile, notes,
    options)
VALUES (%(name)s, %(namespace)s, %(priority)s, %(url)s, %(loader)s, %(license)s, %(auto_level)s,
    %(trust_level)s, %(refresh_cycle)s, %(domain)s, %(related_technologies)s, %(reference_only)s,
    %(verified)s, %(clean_profile)s, %(notes)s, %(options)s)
ON CONFLICT (name) DO UPDATE SET namespace = EXCLUDED.namespace, priority = EXCLUDED.priority,
    url = EXCLUDED.url, loader = EXCLUDED.loader, license = EXCLUDED.license,
    auto_level = EXCLUDED.auto_level, trust_level = EXCLUDED.trust_level,
    refresh_cycle = EXCLUDED.refresh_cycle, domain = EXCLUDED.domain,
    related_technologies = EXCLUDED.related_technologies, reference_only = EXCLUDED.reference_only,
    verified = EXCLUDED.verified, clean_profile = EXCLUDED.clean_profile, notes = EXCLUDED.notes,
    options = EXCLUDED.options, synced_at = now()
"""


def sync_sources(conn: psycopg.Connection, sources: Iterable[Source]) -> int:
    rows = [source_to_row(s) for s in sources]
    with conn.cursor() as cur:
        cur.executemany(_SOURCE_SQL, rows)
    return len(rows)


# ---------------------------------------------------------------------------
# model guard
# ---------------------------------------------------------------------------

_META_KEYS = ("model", "revision", "dim", "max_seq_length", "format_version")


def check_index_meta(conn: psycopg.Connection, meta: dict, set_if_empty: bool = True) -> None:
    """Fail loudly if the stored embedding identity differs from `meta`."""
    stored = dict(conn.execute("SELECT key, value FROM index_meta").fetchall())
    n_rows = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    if not stored:
        if n_rows:
            models = [r[0] for r in conn.execute("SELECT DISTINCT embedding_model FROM chunks")]
            if models != [meta["model"]]:
                raise IndexError_(
                    f"chunks contain embedding_model {models} but index_meta is empty; "
                    f"refusing to mix with {meta['model']}"
                )
        if set_if_empty:
            for k in _META_KEYS:
                conn.execute(
                    "INSERT INTO index_meta (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                    (k, str(meta[k])),
                )
        return
    diffs = {k: (stored.get(k), str(meta[k])) for k in _META_KEYS if stored.get(k) != str(meta[k])}
    if diffs:
        raise IndexError_(
            "embedding identity differs from the one stored in the database: "
            + ", ".join(f"{k}: db={a!r} vs cache/query={b!r}" for k, (a, b) in diffs.items())
            + ". Re-embed everything and rebuild the index (TRUNCATE chunks, clear index_meta) "
            "instead of mixing vector spaces."
        )


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


@dataclass
class LoadStats:
    sources: int = 0
    upserted_rows_seen: int = 0
    deleted: int = 0
    per_source: dict[str, int] = field(default_factory=dict)


def load_sources(
    names: Sequence[str],
    dsn: str | None = None,
    chunks_dir: Path = config.CHUNKS_DIR,
    cache_dir: Path = config.EMBEDDINGS_DIR,
    prune_unknown: bool = False,
    batch: int = 500,
    log: Callable[[str], None] = print,
) -> LoadStats:
    registry = load_registry(config.SOURCES_YAML)
    reg_by_name = {s.name: s for s in registry}
    stats = LoadStats()

    # Validate everything BEFORE touching the DB: chunk file <-> cache must agree.
    prepared: dict[str, tuple] = {}
    meta0: dict | None = None
    for name in names:
        if name not in reg_by_name:
            raise IndexError_(f"{name}: not registered in sources.yaml")
        chunks = read_chunks(Path(chunks_dir) / f"{name}.jsonl")
        cache = load_cache(name, cache_dir)
        if cache is None:
            raise IndexError_(
                f"{name}: no embedding cache; run `python -m rag.cli embed --source {name}`"
            )
        hashes = {c["chunk_id"]: chunk_hash(c) for c in chunks}
        stale = [cid for cid, h in hashes.items() if cache.hashes.get(cid) != h]
        if stale or set(cache.ids) - set(hashes):
            raise IndexError_(
                f"{name}: embedding cache is stale ({len(stale)} chunks missing/changed); "
                f"re-run `embed --source {name}`"
            )
        if meta0 is None:
            meta0 = cache.meta
        elif not meta_compatible(meta0, cache.meta):
            raise IndexError_(
                f"{name}: embedding cache meta differs from other sources ({cache.meta} vs {meta0})"
            )
        prepared[name] = (chunks, hashes, cache)
    if meta0 is None:
        return stats

    sql = _upsert_chunks_sql()
    with connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(727002)")
        check_index_meta(conn, meta0)
        sync_sources(conn, registry)
        for name, (chunks, hashes, cache) in prepared.items():
            rows_of = cache.row_of()
            rows = [
                chunk_to_row(
                    c,
                    cache.vectors[rows_of[c["chunk_id"]]],
                    hashes[c["chunk_id"]],
                    str(cache.meta["model"]),
                )
                for c in chunks
            ]
            with conn.cursor() as cur:
                for i in range(0, len(rows), batch):
                    cur.executemany(sql, rows[i : i + batch])
            cur = conn.execute(
                "DELETE FROM chunks WHERE source_name = %s AND chunk_id <> ALL(%s)",
                (name, list(hashes)),
            )
            stats.deleted += cur.rowcount
            stats.sources += 1
            stats.upserted_rows_seen += len(rows)
            stats.per_source[name] = len(rows)
            log(f"  [{name}] {len(rows)} chunks synced, {cur.rowcount} deleted")
        if prune_unknown:
            cur = conn.execute("DELETE FROM chunks WHERE source_name <> ALL(%s)", (list(prepared),))
            stats.deleted += cur.rowcount
            if cur.rowcount:
                log(f"  pruned {cur.rowcount} rows from sources with no chunk file")
    # ANALYZE outside the transaction so the planner sees the new data
    with connect(dsn, register=False) as conn:
        conn.autocommit = True
        conn.execute("ANALYZE chunks")
    return stats
