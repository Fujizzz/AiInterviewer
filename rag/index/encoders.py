"""Query-encoder backends: which implementation turns a query string into a bge-m3 vector.

Pure standard library on purpose (settings.py and the adapter import it without the rag extra).

* `torch`     sentence-transformers (rag.index.embed.Embedder). The default for local development
              and the only backend used to embed the corpus.
* `onnx-int8` onnxruntime + tokenizers only (rag.index.onnx_encoder.OnnxQueryEncoder): the same
              BAAI/bge-m3 weights, dynamically quantized to int8. It never imports torch,
              transformers or sentence-transformers, so a small server can serve queries.

Both backends expose `.meta` (dict, same identity keys as the index_meta table plus `encoder` and,
for derived encoders, `encoder_variant`), `.dim` and `.encode(texts) -> float32 [n, dim]` (L2
normalised). Corpus vectors are never produced by the derived backend.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ENCODER_TORCH = "torch"
ENCODER_ONNX_INT8 = "onnx-int8"
ENCODER_CHOICES = (ENCODER_TORCH, ENCODER_ONNX_INT8)

# Backends that are a quantized/converted copy of the indexed model. They may *query* an index that
# was embedded by the base model, but they may never define index_meta or embed the corpus.
DERIVED_ENCODERS = frozenset({ENCODER_ONNX_INT8})


def normalize_encoder(value: str | None) -> str:
    name = (value or ENCODER_TORCH).strip().lower().replace("_", "-")
    if name not in ENCODER_CHOICES:
        raise ValueError(f"encoder must be one of {list(ENCODER_CHOICES)}, got {value!r}")
    return name


def encoder_label(meta: dict[str, Any] | None) -> str | None:
    """Short trace label from an encoder's meta: 'torch' or 'onnx-int8/<variant>'. None if the
    meta does not say (e.g. a test double)."""
    if not meta or not meta.get("encoder"):
        return None
    variant = meta.get("encoder_variant")
    return f"{meta['encoder']}/{variant}" if variant else str(meta["encoder"])


def encoder_settings_from_env(env: Mapping[str, str] | None = None) -> tuple[str, dict[str, Any]]:
    """(kind, build_query_encoder kwargs) from RAG_ENCODER / RAG_ENCODER_THREADS /
    RAG_ENCODER_MODEL_DIR / RAG_ENCODER_VERIFY, for code without RAGSettings (CLI search/eval)."""
    from ..settings import RAGSettings

    s = RAGSettings.from_env(env if env is not None else os.environ)
    return s.encoder, {
        "threads": s.encoder_threads,
        "model_dir": s.encoder_model_dir,
        "verify": s.encoder_verify,
    }


def build_query_encoder(
    kind: str = ENCODER_TORCH,
    *,
    device: str | None = None,
    threads: int = 2,
    model_dir: str | Path | None = None,
    verify: bool = True,
) -> Any:
    """Construct (and load) the query encoder for `kind`. Heavy imports happen here, not above."""
    kind = normalize_encoder(kind)
    if kind == ENCODER_ONNX_INT8:
        from .onnx_encoder import OnnxQueryEncoder

        return OnnxQueryEncoder(model_dir, threads=threads, verify=verify)
    from .embed import Embedder

    return Embedder(device=device)
