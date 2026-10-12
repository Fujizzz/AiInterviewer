"""Shared configuration for the index/retrieval layer (DB DSN, model ids)."""

from __future__ import annotations

import os
from pathlib import Path

# Module root = the repo's top-level `rag/` directory (this file is rag/index/config.py).
MODULE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = MODULE_ROOT  # backwards-compatible alias; .env lives in rag/
DATA_DIR = MODULE_ROOT / "data"
CHUNKS_DIR = DATA_DIR / "chunks"
EMBEDDINGS_DIR = DATA_DIR / "embeddings"
SOURCES_YAML = DATA_DIR / "sources.yaml"
SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

EMBED_MODEL = "BAAI/bge-m3"
# Pinned so that the cache + DB record exactly which weights produced the vectors.
EMBED_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EMBED_DIM = 1024
EMBED_MAX_SEQ_LEN = 1024
# Bump when the embedding input format (embed.build_embedding_text) changes.
EMBED_FORMAT_VERSION = 1

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Tiny .env reader (KEY=VALUE lines). Real environment variables win."""
    path = path or (PROJECT_ROOT / ".env")
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def get_dsn(env: dict[str, str] | None = None) -> str:
    """Build the libpq DSN from DATABASE_URL or POSTGRES_* (env > .env > dev defaults)."""
    merged = {**load_dotenv(), **(env if env is not None else os.environ)}
    if merged.get("DATABASE_URL"):
        return merged["DATABASE_URL"]
    user = merged.get("POSTGRES_USER", "intellihire")
    pw = merged.get("POSTGRES_PASSWORD", "intellihire_dev_only")
    host = merged.get("POSTGRES_HOST", "127.0.0.1")
    port = merged.get("POSTGRES_PORT", "5432")
    db = merged.get("POSTGRES_DB", "intellihire")
    from urllib.parse import quote

    return f"postgresql://{quote(user)}:{quote(pw)}@{host}:{port}/{db}"
