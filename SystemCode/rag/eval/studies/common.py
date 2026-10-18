"""Shared helpers for the studies: live-chunk gold resolution, cached encoder, encoder choice."""

from __future__ import annotations

from rag.eval.gold import GoldSet, resolve_gold
from rag.index import config
from rag.index.encoders import build_query_encoder, encoder_settings_from_env
from rag.retrieval.retriever import Retriever

EVAL_DIR = config.DATA_DIR / "eval"
REPORTS_DIR = config.DATA_DIR / "reports"
ROLE = "question_planner"


def live_chunks(r: Retriever) -> list[dict]:
    """Chunks as stored in the database (data/chunks/*.jsonl may be stale vs the loaded index)."""
    rows = r.conn.execute(
        "SELECT chunk_id, source_name, source_url, heading_path, text FROM chunks"
    ).fetchall()
    return [
        {
            "chunk_id": a,
            "source_name": b,
            "source_url": c,
            "heading_path": d,
            "text": e,
        }
        for a, b, c, d, e in rows
    ]


def resolve_live(gold: GoldSet, r: Retriever) -> dict[str, set[str]]:
    return resolve_gold(gold, live_chunks(r))


def make_encoder():
    """Query encoder chosen like the app does: RAG_ENCODER=torch|onnx-int8 (+ _MODEL_DIR)."""
    kind, kwargs = encoder_settings_from_env()
    return build_query_encoder(kind, **kwargs)


class CachedEmbedder:
    """Encode each distinct query once (latency studies that want DB-only time)."""

    def __init__(self, emb):
        self.emb, self.cache, self.meta = emb, {}, emb.meta

    def encode(self, texts):
        out = []
        for t in texts:
            if t not in self.cache:
                self.cache[t] = self.emb.encode([t])[0]
            out.append(self.cache[t])
        return out
