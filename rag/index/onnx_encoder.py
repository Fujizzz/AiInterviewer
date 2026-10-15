"""bge-m3 query encoder on ONNX Runtime (int8), for small servers. No torch.

Imports only numpy, onnxruntime and tokenizers (plus the standard library). It must never import
torch, transformers or sentence-transformers: a test enforces that in a fresh interpreter.

Semantics are those of sentence-transformers' BAAI/bge-m3 pipeline (checked against its
modules.json / 1_Pooling/config.json: Transformer -> Pooling(cls) -> Normalize):

    text (no prefix, as on the torch query side)
      -> XLM-R tokenizer, add <s> ... </s>, truncate to max_seq_length (1024) tokens
      -> encoder -> last_hidden_state
      -> CLS pooling (position 0)  -> L2 normalisation  -> float32 [n, 1024]

Artifact: a directory with `model.onnx`, `model.onnx.data` and `tokenizer.json`, described by the
committed manifest rag/data/encoder_manifest.json (sha256 of every file, base model + revision,
quantization settings). Build it with `python -m rag.cli encoder build-onnx`; a server only needs
the files.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import config
from .encoders import ENCODER_ONNX_INT8

MANIFEST_SCHEMA_VERSION = 1
MODEL_FILE = "model.onnx"  # graph only; the weights live in DATA_FILE (memory-mapped by ORT)
DATA_FILE = "model.onnx.data"
TOKENIZER_FILE = "tokenizer.json"
HIDDEN_OUTPUT = "token_embeddings"  # last_hidden_state [batch, seq, dim] in the BAAI export
DEFAULT_THREADS = 2
# One text per forward pass by default. Dynamic int8 quantizes activations with a per-tensor scale
# taken from the whole batch (padding included), so a padded batch would shift every vector in it
# by ~1e-2 cosine and make a query's vector depend on its neighbours. Queries arrive one at a time.
DEFAULT_BATCH = 1


class EncoderUnavailableError(RuntimeError):
    """The ONNX artifact is missing, corrupt or inconsistent with the manifest."""


@dataclass(frozen=True)
class EncoderManifest:
    raw: dict

    @property
    def variant(self) -> str:
        return str(self.raw["variant"])

    @property
    def base_model(self) -> str:
        return str(self.raw["base_model"])

    @property
    def base_revision(self) -> str:
        return str(self.raw["base_revision"])

    @property
    def dim(self) -> int:
        return int(self.raw["dim"])

    @property
    def max_seq_length(self) -> int:
        return int(self.raw["max_seq_length"])

    @property
    def format_version(self) -> int:
        return int(self.raw["format_version"])

    @property
    def pad_id(self) -> int:
        return int(self.raw.get("graph", {}).get("pad_id", 1))

    @property
    def files(self) -> dict[str, dict]:
        return dict(self.raw["artifact"]["files"])

    @property
    def default_dir_name(self) -> str:
        return str(self.raw["artifact"]["dir"])


def load_manifest(path: str | Path | None = None) -> EncoderManifest:
    path = Path(path or config.ENCODER_MANIFEST)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise EncoderUnavailableError(f"encoder manifest not found: {path}") from error
    if raw.get("schema_version") != MANIFEST_SCHEMA_VERSION or raw.get("encoder") != (
        ENCODER_ONNX_INT8
    ):
        raise EncoderUnavailableError(
            f"{path}: unsupported manifest (schema_version={raw.get('schema_version')!r}, "
            f"encoder={raw.get('encoder')!r})"
        )
    for key in ("variant", "base_model", "base_revision", "dim", "max_seq_length", "artifact"):
        if key not in raw:
            raise EncoderUnavailableError(f"{path}: missing key {key!r}")
    return EncoderManifest(raw)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def default_model_dir(manifest: EncoderManifest) -> Path:
    return config.MODELS_DIR / manifest.default_dir_name


def verify_artifact(model_dir: Path, manifest: EncoderManifest, check_sha: bool = True) -> None:
    """Raise EncoderUnavailableError unless every manifest file is present with the right size
    (and sha256 when `check_sha`; about 1-2 s for the 570 MB model)."""
    for name, info in manifest.files.items():
        path = model_dir / name
        if not path.is_file():
            raise EncoderUnavailableError(
                f"ONNX encoder file missing: {path}. Build it with "
                "`python -m rag.cli encoder build-onnx` on a dev machine and copy the directory to "
                "the server (see rag/docs/TEAM_INTEGRATION.md), or set RAG_ENCODER_MODEL_DIR."
            )
        if path.stat().st_size != int(info["bytes"]):
            raise EncoderUnavailableError(
                f"{path}: size {path.stat().st_size} != manifest {info['bytes']} "
                "(truncated or partial copy?)"
            )
        if check_sha and sha256_file(path) != info["sha256"]:
            raise EncoderUnavailableError(
                f"{path}: sha256 differs from rag/data/encoder_manifest.json "
                "(corrupt copy, or a different build than this commit expects)"
            )


def pool_and_normalize(hidden: np.ndarray) -> np.ndarray:
    """CLS pooling + L2 normalisation of last_hidden_state [batch, seq, dim] -> [batch, dim].

    bge-m3 dense = the hidden state of the first token (<s>). With right padding position 0 is
    always the real <s>, so the attention mask is not needed for CLS pooling.
    """
    cls = np.asarray(hidden[:, 0, :], dtype=np.float32)
    norm = np.linalg.norm(cls, axis=1, keepdims=True)
    return cls / np.maximum(norm, 1e-12)


def pad_batch(id_lists: Sequence[Sequence[int]], pad_id: int) -> tuple[np.ndarray, np.ndarray]:
    """Right-pad token id lists to the longest -> (input_ids, attention_mask), int64."""
    width = max(len(ids) for ids in id_lists)
    input_ids = np.full((len(id_lists), width), pad_id, dtype=np.int64)
    mask = np.zeros((len(id_lists), width), dtype=np.int64)
    for i, ids in enumerate(id_lists):
        input_ids[i, : len(ids)] = ids
        mask[i, : len(ids)] = 1
    return input_ids, mask


SMOKE_MIN_MEAN = 0.97
SMOKE_MIN_EACH = 0.95


def write_smoke_reference(
    vectors_by_query: dict[str, np.ndarray], path: Path | None = None
) -> Path:
    """Dev: store torch query vectors (float16, base64) so a server can self-check the int8 path."""
    import base64

    path = Path(path or config.ENCODER_SMOKE)
    doc = {
        "about": "torch bge-m3 query vectors (fp16 on MPS), stored as float16/base64",
        "base_revision": config.EMBED_REVISION,
        "dim": config.EMBED_DIM,
        "queries": {
            q: base64.b64encode(np.asarray(v, dtype=np.float16).tobytes()).decode("ascii")
            for q, v in vectors_by_query.items()
        },
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def smoke_check(
    encoder: OnnxQueryEncoder, path: Path | None = None, repeats: int = 5
) -> dict[str, float]:
    """Cosine between the encoder's vectors and the committed torch reference vectors, plus timing.

    Catches a CPU/kernel path that mangles int8 (e.g. x86 without VNNI saturating u8s8 products)
    on the machine that will actually serve. Expected mean cosine is about 0.988. Timing covers
    `repeats` warm encodes of each reference query (after one untimed warm-up encode).
    """
    import base64
    import time

    doc = json.loads(Path(path or config.ENCODER_SMOKE).read_text(encoding="utf-8"))
    encoder.encode(["warm up"])
    cos: list[float] = []
    ms: list[float] = []
    for query, blob in doc["queries"].items():
        ref = np.frombuffer(base64.b64decode(blob), dtype=np.float16).astype(np.float32)
        for _ in range(repeats):
            t = time.perf_counter()
            got = encoder.encode([query])[0]
            ms.append((time.perf_counter() - t) * 1000)
        cos.append(float(ref @ got / (np.linalg.norm(ref) * np.linalg.norm(got))))
    ms.sort()
    return {
        "n": len(cos),
        "mean": float(np.mean(cos)),
        "min": float(np.min(cos)),
        "p50_ms": ms[len(ms) // 2],
        "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))],
    }


class OnnxQueryEncoder:
    """Thread-safe (calls are serialized by an internal lock) int8 bge-m3 query encoder."""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        *,
        threads: int = DEFAULT_THREADS,
        verify: bool = True,
        manifest: EncoderManifest | None = None,
        spinning: bool = False,
    ) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if threads < 1:
            raise ValueError("threads must be >= 1")
        self.manifest = manifest or load_manifest()
        self.model_dir = Path(model_dir) if model_dir else default_model_dir(self.manifest)
        verify_artifact(self.model_dir, self.manifest, check_sha=verify)
        self.threads = threads
        self.dim = self.manifest.dim
        self.max_seq_length = self.manifest.max_seq_length
        self._lock = threading.Lock()

        self._tokenizer = Tokenizer.from_file(str(self.model_dir / TOKENIZER_FILE))
        self._tokenizer.enable_truncation(max_length=self.max_seq_length)
        self._tokenizer.no_padding()  # padding is done in numpy (stateless, batch-size agnostic)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if not spinning:
            # idle worker threads sleep instead of busy-waiting: the box is shared with the app
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self._session = ort.InferenceSession(
            str(self.model_dir / MODEL_FILE), opts, providers=["CPUExecutionProvider"]
        )
        inputs = {i.name for i in self._session.get_inputs()}
        outputs = {o.name for o in self._session.get_outputs()}
        if not {"input_ids", "attention_mask"} <= inputs or HIDDEN_OUTPUT not in outputs:
            raise EncoderUnavailableError(
                f"unexpected ONNX signature: inputs={sorted(inputs)} outputs={sorted(outputs)}"
            )
        self._feed_token_type = "token_type_ids" in inputs

    # -- identity -------------------------------------------------------
    @property
    def meta(self) -> dict[str, Any]:
        """Identity of the vector space = the BASE model (what index_meta stores), plus which
        encoder variant produced the query vectors (informational, recorded in traces)."""
        m = self.manifest
        return {
            "model": m.base_model,
            "revision": m.base_revision,
            "dim": self.dim,
            "max_seq_length": self.max_seq_length,
            "format_version": m.format_version,
            "encoder": ENCODER_ONNX_INT8,
            "encoder_variant": m.variant,
        }

    # -- encode ---------------------------------------------------------
    def encode(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        step = batch_size or DEFAULT_BATCH
        out = [self._encode_batch(texts[i : i + step]) for i in range(0, len(texts), step)]
        return np.concatenate(out, axis=0)

    def _encode_batch(self, texts: list[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(list(texts))
        input_ids, mask = pad_batch([e.ids for e in encodings], self.manifest.pad_id)
        feed = {"input_ids": input_ids, "attention_mask": mask}
        if self._feed_token_type:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        with self._lock:
            hidden = self._session.run([HIDDEN_OUTPUT], feed)[0]
        vecs = pool_and_normalize(hidden)
        if vecs.shape[1] != self.dim or not np.isfinite(vecs).all():
            raise EncoderUnavailableError(f"bad encoder output {vecs.shape}")
        return vecs
