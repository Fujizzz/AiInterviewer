"""Dev-only: build the int8 ONNX query-encoder artifact (never imported at serve time).

    python -m rag.cli encoder build-onnx [--variant NAME] [--update-manifest]

Steps (reproducible, pinned to config.EMBED_REVISION):
  1. download the OFFICIAL ONNX export from the BAAI/bge-m3 repo (`onnx/` folder, MIT) into
     rag/data/models/src/ (a real copy: onnx refuses symlinks into the HF cache);
  2. onnxruntime.quantization.quantize_dynamic with the settings of the chosen variant;
  3. optional graph edit (word embeddings to fp16) for variants that ask for it;
  4. sanity check against the fp32 ONNX (cosine on a few queries);
  5. write manifest.json next to the model, and with --update-manifest the committed
     rag/data/encoder_manifest.json (sha256 of every artifact file).

Needs `onnx`, `onnxruntime`, `huggingface-hub` and about 6 GB of free RAM: quantizing loads the
2.3 GB fp32 graph and holds several copies. Do NOT run it on the 2-3 GB server; build on a dev
machine and copy the output directory.
"""

from __future__ import annotations

import json
import platform
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config
from .encoders import ENCODER_ONNX_INT8
from .onnx_encoder import (
    DATA_FILE,
    MANIFEST_SCHEMA_VERSION,
    MODEL_FILE,
    TOKENIZER_FILE,
    EncoderManifest,
    sha256_file,
)

SOURCE_SUBDIR = "onnx"
SOURCE_FILES = ("model.onnx", "model.onnx_data", "tokenizer.json")
WORD_EMBEDDINGS = "0.auto_model.embeddings.word_embeddings.weight"
SANITY_QUERIES = [
    "How does PagedAttention reduce KV cache fragmentation?",
    "Explain MVCC in PostgreSQL",
    "Kafka exactly-once semantics",
    "PagedAttention 如何减少 KV cache 显存碎片",
    "select_related vs prefetch_related in Django",
    "pizza",
]


@dataclass(frozen=True)
class QuantVariant:
    """One dynamic-quantization recipe. Field names mirror quantize_dynamic's arguments."""

    name: str
    weight_type: str = "qint8"  # qint8 | quint8
    per_channel: bool = False
    reduce_range: bool = False
    op_types: tuple[str, ...] | None = None  # None = ORT default (MatMul, Gather, ...)
    exclude_patterns: tuple[str, ...] = ()  # substrings of node names kept in fp32
    embeddings: str = "ort"  # ort (as quantize_dynamic decides) | fp16 (graph edit)
    note: str = ""


_ATTN_PROJ = tuple(
    f"layer.{i}/attention/{p}/MatMul"
    for i in range(24)
    for p in ("self/query", "self/key", "self/value", "output/dense")
)
_LAST2 = tuple(f"layer.{i}/" for i in (22, 23))

VARIANTS: dict[str, QuantVariant] = {
    v.name: v
    for v in (
        QuantVariant("int8-pt", note="QInt8 weights, per-tensor (ORT defaults)"),
        QuantVariant("int8-pc", per_channel=True, note="QInt8, per-channel weights"),
        QuantVariant(
            "uint8-pc", weight_type="quint8", per_channel=True, note="QUInt8, per-channel"
        ),
        QuantVariant(
            "int8-pc-rr",
            per_channel=True,
            reduce_range=True,
            note="QInt8 per-channel, 7-bit range (avoids AVX2 u8s8 saturation)",
        ),
        QuantVariant(
            "int8-pc-emb16",
            per_channel=True,
            op_types=("MatMul",),
            embeddings="fp16",
            note="MatMul int8 per-channel; word embeddings kept as fp16 (not quantized)",
        ),
        QuantVariant(
            "int8-pc-noattn",
            per_channel=True,
            exclude_patterns=_ATTN_PROJ,
            note="per-channel int8 except the Q/K/V/output projections of all 24 layers",
        ),
        QuantVariant(
            "int8-pc-last2fp32",
            per_channel=True,
            exclude_patterns=_LAST2,
            note="per-channel int8 except the last two transformer layers",
        ),
    )
}
# uint8-pc: u8u8 matmuls avoid the u8s8 VPMADDUBSW saturation path of AVX2 CPUs without VNNI, and it
# kept paraphrase / identifier queries best (rag/data/reports/encoder-onnx-int8-20261015.md).
# Fallbacks if `encoder verify --smoke` fails on the server CPU: int8-pt, then int8-pc-rr.
DEFAULT_VARIANT = "uint8-pc"


def variant_dir(name: str) -> Path:
    """Where `build` puts a variant (the default variant is the shipped artifact directory)."""
    if name == DEFAULT_VARIANT:
        return config.MODELS_DIR / "bge-m3-onnx-int8"
    return config.MODELS_DIR / "variants" / name


def _say(log: Callable[[str], None] | None, msg: str) -> None:
    (log or print)(msg)


def fetch_source(src_dir: Path, log: Callable[[str], None] | None = None) -> Path:
    """Download BAAI/bge-m3 onnx/ at the pinned revision into a real directory; return onnx/."""
    from huggingface_hub import snapshot_download

    onnx_dir = src_dir / SOURCE_SUBDIR
    if all((onnx_dir / f).is_file() for f in SOURCE_FILES):
        return onnx_dir
    _say(log, f"downloading {config.EMBED_MODEL}@{config.EMBED_REVISION[:8]} onnx/ (~2.3 GB)")
    snapshot_download(
        config.EMBED_MODEL,
        revision=config.EMBED_REVISION,
        allow_patterns=[f"{SOURCE_SUBDIR}/*"],
        local_dir=str(src_dir),
    )
    return onnx_dir


def _file_info(path: Path) -> dict:
    return {"sha256": sha256_file(path), "bytes": path.stat().st_size}


def _embeddings_to_fp16(model_path: Path) -> None:
    """Store the fp32 word-embedding table as fp16 and Cast the Gather output back to fp32."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    model = onnx.load(str(model_path))
    init = next(t for t in model.graph.initializer if t.name == WORD_EMBEDDINGS)
    half = numpy_helper.from_array(numpy_helper.to_array(init).astype(np.float16), init.name)
    init.CopyFrom(half)
    gather = next(
        n for n in model.graph.node if n.op_type == "Gather" and n.input[0] == WORD_EMBEDDINGS
    )
    old_out = gather.output[0]
    gather.output[0] = old_out + "_fp16"
    cast = helper.make_node(
        "Cast", [gather.output[0]], [old_out], to=TensorProto.FLOAT, name=old_out + "/CastFp32"
    )
    nodes = list(model.graph.node)
    nodes.insert(nodes.index(gather) + 1, cast)
    del model.graph.node[:]
    model.graph.node.extend(nodes)
    onnx.save(model, str(model_path))


def _to_external_data(model_path: Path) -> None:
    """Rewrite model.onnx as a small graph file + model.onnx.data holding every weight.

    Why: ONNX Runtime memory-maps external weights, so only the pages that inference touches become
    resident and they are clean, reclaimable page cache. A single-file model is parsed into the
    heap instead: measured 1.4 GB RSS vs 0.37 GB for the same int8 network.
    """
    import onnx

    data = model_path.with_name(DATA_FILE)
    data.unlink(missing_ok=True)  # onnx appends to an existing file
    model = onnx.load(str(model_path))
    onnx.save(
        model,
        str(model_path),
        save_as_external_data=True,
        all_tensors_to_one_file=True,
        location=DATA_FILE,
        size_threshold=1024,
    )


def quantize(
    src_model: Path,
    out_model: Path,
    variant: QuantVariant,
    log: Callable[[str], None] | None = None,
) -> dict:
    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic

    names = None
    if variant.exclude_patterns:
        graph = onnx.load(str(src_model), load_external_data=False).graph
        names = [
            n.name
            for n in graph.node
            if n.op_type == "MatMul" and any(p in n.name for p in variant.exclude_patterns)
        ]
        _say(log, f"  excluding {len(names)} MatMul nodes from quantization")
    out_model.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    quantize_dynamic(
        src_model,
        out_model,
        weight_type=QuantType.QInt8 if variant.weight_type == "qint8" else QuantType.QUInt8,
        per_channel=variant.per_channel,
        reduce_range=variant.reduce_range,
        op_types_to_quantize=list(variant.op_types) if variant.op_types else None,
        nodes_to_exclude=names,
        use_external_data_format=False,
    )
    if variant.embeddings == "fp16":
        _embeddings_to_fp16(out_model)
    _to_external_data(out_model)
    return {"seconds": round(time.time() - t0, 1), "excluded_nodes": len(names or [])}


def _sanity(onnx_fp32: Path, artifact_dir: Path, manifest: EncoderManifest) -> float:
    """Min cosine between the artifact's vectors and the fp32 ONNX on SANITY_QUERIES."""
    import onnxruntime as ort
    from tokenizers import Tokenizer

    from .onnx_encoder import OnnxQueryEncoder, pad_batch, pool_and_normalize

    enc = OnnxQueryEncoder(artifact_dir, threads=4, manifest=manifest, verify=False)
    tok = Tokenizer.from_file(str(onnx_fp32.parent / TOKENIZER_FILE))
    tok.enable_truncation(max_length=manifest.max_seq_length)
    sess = ort.InferenceSession(str(onnx_fp32), providers=["CPUExecutionProvider"])
    ids, mask = pad_batch([e.ids for e in tok.encode_batch(SANITY_QUERIES)], manifest.pad_id)
    ref = pool_and_normalize(
        sess.run(["token_embeddings"], {"input_ids": ids, "attention_mask": mask})[0]
    )
    got = enc.encode(SANITY_QUERIES)
    return float((ref * got).sum(axis=1).min())


def build(
    variant_name: str = DEFAULT_VARIANT,
    out_dir: Path | None = None,
    src_dir: Path | None = None,
    update_manifest: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Build one variant. Returns the manifest dict."""
    import onnx
    import onnxruntime

    variant = VARIANTS[variant_name]
    src_dir = src_dir or (config.MODELS_DIR / "src")
    out_dir = out_dir or variant_dir(variant_name)
    onnx_dir = fetch_source(src_dir, log)
    _say(log, f"quantizing variant {variant.name}: {variant.note}")
    quantize(onnx_dir / "model.onnx", out_dir / MODEL_FILE, variant, log)
    shutil.copyfile(onnx_dir / TOKENIZER_FILE, out_dir / TOKENIZER_FILE)

    quant = asdict(variant)
    quant.update(
        tool="onnxruntime.quantization.quantize_dynamic",
        activation_type="uint8 (DynamicQuantizeLinear, computed per call)",
        exclude_patterns=[f"{len(variant.exclude_patterns)} patterns"]
        if len(variant.exclude_patterns) > 3
        else list(variant.exclude_patterns),
        op_types=list(variant.op_types) if variant.op_types else "ORT default (MatMul, Gather)",
    )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "encoder": ENCODER_ONNX_INT8,
        "variant": variant.name,
        "base_model": config.EMBED_MODEL,
        "base_revision": config.EMBED_REVISION,
        "license": "MIT (BAAI/bge-m3 and its ONNX export)",
        "dim": config.EMBED_DIM,
        "max_seq_length": config.EMBED_MAX_SEQ_LEN,
        "format_version": config.EMBED_FORMAT_VERSION,
        "pooling": "cls",
        "normalize": True,
        "source": {
            "repo": config.EMBED_MODEL,
            "revision": config.EMBED_REVISION,
            "path": "onnx/model.onnx + model.onnx_data (fp32, official export, opset 11)",
            "files": {f: _file_info(onnx_dir / f) for f in SOURCE_FILES},
        },
        "quantization": quant,
        "graph": {
            "inputs": ["input_ids", "attention_mask"],
            "hidden_output": "token_embeddings",
            "pad_id": 1,
        },
        "artifact": {
            "dir": "bge-m3-onnx-int8",
            "files": {
                MODEL_FILE: _file_info(out_dir / MODEL_FILE),
                DATA_FILE: _file_info(out_dir / DATA_FILE),
                TOKENIZER_FILE: _file_info(out_dir / TOKENIZER_FILE),
            },
        },
        "build": {
            "onnxruntime": onnxruntime.__version__,
            "onnx": onnx.__version__,
            "platform": f"{platform.system()} {platform.machine()}",
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    min_cos = _sanity(onnx_dir / "model.onnx", out_dir, EncoderManifest(manifest))
    _say(log, f"sanity: min cosine vs fp32 ONNX on {len(SANITY_QUERIES)} queries = {min_cos:.4f}")
    if min_cos < 0.90:
        raise RuntimeError(f"variant {variant.name} is broken (min cosine {min_cos:.3f})")
    if update_manifest:
        config.ENCODER_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        _say(log, f"wrote {config.ENCODER_MANIFEST}")
    elif variant_name == DEFAULT_VARIANT:
        try:
            committed = json.loads(config.ENCODER_MANIFEST.read_text(encoding="utf-8"))
            same = committed["artifact"]["files"] == manifest["artifact"]["files"]
            _say(log, "artifact sha256 matches the committed manifest: " + str(same))
        except FileNotFoundError:
            pass
    total_mb = sum(f.stat().st_size for f in out_dir.iterdir()) / 1e6
    _say(log, f"artifact: {out_dir} ({total_mb:.0f} MB)")
    return manifest
