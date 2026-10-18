"""Embedding step: chunks/*.jsonl -> data/embeddings/<source>.{npy,ids.json}.

Embedding input format (EMBED_FORMAT_VERSION = 1), see `build_embedding_text`:

    <title, only if it differs from the last heading>\\n
    <heading_path joined with " > ">\\n
    \\n
    <chunk text>

`text_hash` is the sha256 of exactly that string, so editing a title/heading
re-embeds the chunk just like editing its body does.

Cache layout (one pair per source):
    <source>.npy       float32 [n, 1024], L2-normalised, row i <-> ids[i]
    <source>.ids.json  {"meta": {...model/revision/dim/format...},
                        "ids": [chunk_id, ...],            # row order of the .npy
                        "hashes": {chunk_id: text_hash},   # chunk_id -> text_hash
                        "npy_sha256": "..."}               # guards against torn writes

Incremental: only new chunks or chunks whose text_hash changed are re-embedded; ids
that disappeared from the chunk file are dropped. If the model, revision, dim,
format version or max_seq_length differ from the cache meta, the whole source is
re-embedded (vectors from different models must never be mixed).

The model itself is only imported lazily (`Embedder`), so the cache logic is
unit-testable with a fake `encode_fn`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config

EncodeFn = Callable[[list[str]], np.ndarray]


# ---------------------------------------------------------------------------
# text format + hashing
# ---------------------------------------------------------------------------


def build_embedding_text(chunk: dict) -> str:
    title = (chunk.get("title") or "").strip()
    heading = [h.strip() for h in (chunk.get("heading_path") or []) if h and h.strip()]
    parts: list[str] = []
    if title and (not heading or title != heading[-1]):
        parts.append(title)
    if heading:
        parts.append(" > ".join(heading))
    head = "\n".join(parts)
    return f"{head}\n\n{chunk['text']}" if head else chunk["text"]


def text_hash(embedding_text: str) -> str:
    return hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()


def chunk_hash(chunk: dict) -> str:
    return text_hash(build_embedding_text(chunk))


def read_chunks(path: Path) -> list[dict]:
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def list_chunk_sources(chunks_dir: Path = config.CHUNKS_DIR) -> list[str]:
    return sorted(p.stem for p in Path(chunks_dir).glob("*.jsonl"))


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def make_meta(
    model: str = config.EMBED_MODEL,
    revision: str = config.EMBED_REVISION,
    dim: int = config.EMBED_DIM,
    max_seq_length: int = config.EMBED_MAX_SEQ_LEN,
    format_version: int = config.EMBED_FORMAT_VERSION,
) -> dict[str, object]:
    return {
        "model": model,
        "revision": revision,
        "dim": dim,
        "max_seq_length": max_seq_length,
        "format_version": format_version,
    }


# keys that must match for cached vectors to be reusable
_COMPAT_KEYS = ("model", "revision", "dim", "max_seq_length", "format_version")


def meta_compatible(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in _COMPAT_KEYS)


@dataclass
class SourceCache:
    meta: dict
    ids: list[str]
    hashes: dict[str, str]
    vectors: np.ndarray  # [n, dim]

    def row_of(self) -> dict[str, int]:
        return {cid: i for i, cid in enumerate(self.ids)}


def cache_paths(source: str, cache_dir: Path) -> tuple[Path, Path]:
    return Path(cache_dir) / f"{source}.npy", Path(cache_dir) / f"{source}.ids.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_cache(source: str, cache_dir: Path) -> SourceCache | None:
    """Return the cache, or None if absent/corrupt (corrupt => treated as empty)."""
    npy, js = cache_paths(source, cache_dir)
    if not npy.exists() or not js.exists():
        return None
    try:
        doc = json.loads(js.read_text(encoding="utf-8"))
        if doc.get("npy_sha256") != _sha256_file(npy):
            return None
        vecs = np.load(npy)
        ids = doc["ids"]
        if vecs.ndim != 2 or vecs.shape[0] != len(ids) or set(ids) != set(doc["hashes"]):
            return None
        return SourceCache(meta=doc["meta"], ids=ids, hashes=doc["hashes"], vectors=vecs)
    except Exception:
        return None


def save_cache(source: str, cache_dir: Path, cache: SourceCache) -> None:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    npy, js = cache_paths(source, cache_dir)
    tmp_npy = npy.with_suffix(".npy.tmp")
    tmp_js = js.with_suffix(".json.tmp")
    with open(tmp_npy, "wb") as f:
        np.save(f, np.ascontiguousarray(cache.vectors, dtype=np.float32))
    doc = {
        "meta": cache.meta,
        "ids": cache.ids,
        "hashes": cache.hashes,
        "npy_sha256": _sha256_file(tmp_npy),
    }
    tmp_js.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_npy, npy)
    os.replace(tmp_js, js)


# ---------------------------------------------------------------------------
# incremental planning (pure, no model)
# ---------------------------------------------------------------------------


@dataclass
class EmbedPlan:
    reuse: list[str] = field(default_factory=list)  # cached and unchanged
    todo: list[str] = field(default_factory=list)  # new or hash changed
    dropped: list[str] = field(default_factory=list)  # in cache, gone from chunks
    full_rebuild: bool = False  # cache unusable (meta mismatch)


def plan_embedding(current: dict[str, str], cache: SourceCache | None, meta: dict) -> EmbedPlan:
    """current: chunk_id -> text_hash (insertion-ordered as in the chunk file)."""
    if cache is None:
        return EmbedPlan(todo=list(current))
    if not meta_compatible(cache.meta, meta):
        return EmbedPlan(todo=list(current), dropped=list(cache.ids), full_rebuild=True)
    plan = EmbedPlan()
    for cid, h in current.items():
        if cache.hashes.get(cid) == h:
            plan.reuse.append(cid)
        else:
            plan.todo.append(cid)
    plan.dropped = [cid for cid in cache.ids if cid not in current]
    return plan


@dataclass
class EmbedStats:
    source: str
    total: int = 0
    reused: int = 0
    embedded: int = 0
    dropped: int = 0
    full_rebuild: bool = False
    seconds: float = 0.0
    tokens: int = 0  # cl100k token_count of embedded chunks (approximate for bge-m3)


def embed_source(
    source: str,
    chunks: Sequence[dict],
    encode_fn: EncodeFn,
    meta: dict,
    cache_dir: Path = config.EMBEDDINGS_DIR,
    save_every: int = 1024,
    log: Callable[[str], None] = lambda s: None,
) -> EmbedStats:
    """Bring the on-disk cache for `source` in line with `chunks` (incrementally)."""
    t0 = time.time()
    by_id = {c["chunk_id"]: c for c in chunks}
    if len(by_id) != len(chunks):
        raise ValueError(f"{source}: duplicate chunk_id in chunk file")
    current = {cid: chunk_hash(c) for cid, c in by_id.items()}
    cache = load_cache(source, cache_dir)
    plan = plan_embedding(current, cache, meta)
    stats = EmbedStats(
        source=source,
        total=len(current),
        reused=len(plan.reuse),
        dropped=len(plan.dropped),
        full_rebuild=plan.full_rebuild,
    )

    dim = int(meta["dim"])
    # surviving vectors, keyed by id
    vec: dict[str, np.ndarray] = {}
    if cache is not None and not plan.full_rebuild:
        rows = cache.row_of()
        for cid in plan.reuse:
            vec[cid] = cache.vectors[rows[cid]]

    def write_out() -> None:
        # chunk-file order for finished ids; unfinished ones are simply absent
        ids = [cid for cid in current if cid in vec]
        mat = np.stack([vec[cid] for cid in ids]) if ids else np.zeros((0, dim), np.float32)
        save_cache(
            source, cache_dir, SourceCache(dict(meta), ids, {c: current[c] for c in ids}, mat)
        )

    if not plan.todo:
        if plan.dropped or cache is None or set(cache.ids) != set(vec):
            write_out()
        stats.seconds = time.time() - t0
        return stats

    for start in range(0, len(plan.todo), save_every):
        block = plan.todo[start : start + save_every]
        texts = [build_embedding_text(by_id[cid]) for cid in block]
        out = np.asarray(encode_fn(texts), dtype=np.float32)
        if out.shape != (len(block), dim):
            raise ValueError(
                f"{source}: encoder returned {out.shape}, expected {(len(block), dim)}"
            )
        if not np.isfinite(out).all():
            raise ValueError(f"{source}: non-finite values in embeddings (block starting {start})")
        for cid, v in zip(block, out, strict=True):
            vec[cid] = v
        stats.embedded += len(block)
        stats.tokens += sum(int(by_id[c].get("token_count") or 0) for c in block)
        write_out()
        el = time.time() - t0
        log(
            f"  [{source}] {stats.embedded}/{len(plan.todo)} embedded, "
            f"{stats.embedded / max(el, 1e-9):.1f} chunks/s"
        )
    stats.seconds = time.time() - t0
    return stats


# ---------------------------------------------------------------------------
# real model (lazy)
# ---------------------------------------------------------------------------

_SNAPSHOT_ALLOW = [
    "1_Pooling/*",
    "config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "pytorch_model.bin",
    "model.safetensors",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
]


def pick_device(device: str | None = None) -> str:
    if device:
        return device
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class Embedder:
    """sentence-transformers wrapper around BAAI/bge-m3 (dense, 1024-d, normalised)."""

    def __init__(
        self,
        model: str = config.EMBED_MODEL,
        revision: str = config.EMBED_REVISION,
        device: str | None = None,
        fp16: bool = True,
        max_seq_length: int = config.EMBED_MAX_SEQ_LEN,
        batch_size: int = 16,
    ):
        import torch
        from huggingface_hub import snapshot_download
        from sentence_transformers import SentenceTransformer

        self.model_name, self.revision = model, revision
        self.device = pick_device(device)
        self.fp16 = fp16
        self.batch_size = batch_size
        self.max_seq_length = max_seq_length
        path = snapshot_download(model, revision=revision, allow_patterns=_SNAPSHOT_ALLOW)
        kwargs = {"model_kwargs": {"torch_dtype": torch.float16}} if fp16 else {}
        self.model = SentenceTransformer(path, device=self.device, **kwargs)
        self.model.max_seq_length = max_seq_length
        self.dim = self.model.get_sentence_embedding_dimension()

    @property
    def meta(self) -> dict:
        m = make_meta(self.model_name, self.revision, self.dim, self.max_seq_length)
        # informational only (not part of the compatibility check): fp16 vs fp32 vectors
        # differ by ~1e-4 cosine, far below retrieval noise.
        m.update(fp16=self.fp16, device=self.device, encoder="torch")
        return m

    def encode(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        out = self.model.encode(
            texts,
            batch_size=batch_size or self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(out, dtype=np.float32)


def tune_batch_size(
    embedder: Embedder,
    texts: list[str],
    candidates: Sequence[int] = (8, 16, 32, 64),
    log: Callable[[str], None] = print,
) -> dict[int, float]:
    """Texts/s per batch size on a sample (after one warm-up pass)."""
    import torch

    embedder.encode(texts[:8], batch_size=8)  # warm-up (kernel compile / allocator)
    res: dict[int, float] = {}
    for bs in candidates:
        try:
            t = time.time()
            embedder.encode(texts, batch_size=bs)
            if embedder.device == "mps":
                torch.mps.synchronize()
            res[bs] = len(texts) / (time.time() - t)
            log(f"  batch_size={bs}: {res[bs]:.1f} texts/s")
        except Exception as exc:  # e.g. MPS OOM
            log(f"  batch_size={bs}: failed ({type(exc).__name__}: {str(exc)[:80]})")
    return res


def dir_size_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in Path(path).glob("**/*") if p.is_file())
