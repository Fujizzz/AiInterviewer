"""Regenerate tests/rag/fixtures/tiny_encoder/ (dev only: needs onnx, tokenizers, numpy).

    python tests/rag/fixtures/make_tiny_encoder.py

A 12-token, 8-dim stand-in for the bge-m3 ONNX graph with the same signature (input_ids,
attention_mask -> token_embeddings [batch, seq, dim]). Its hidden state is
    embedding[input_ids] + (number of unmasked tokens) * e_last
so the CLS vector depends on the token ids, on the attention mask and on the truncated length. That
lets the unit tests check CLS pooling, L2 normalisation, padding and truncation without a model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
from tokenizers import Tokenizer, models, pre_tokenizers, processors

OUT = Path(__file__).resolve().parent / "tiny_encoder"
VOCAB = [
    "<s>",
    "<pad>",
    "</s>",
    "<unk>",
    "hello",
    "world",
    "foo",
    "bar",
    "baz",
    "qux",
    "one",
    "two",
]
DIM = 8
MAX_SEQ = 6


def build_tokenizer() -> Tokenizer:
    tok = Tokenizer(models.WordLevel({w: i for i, w in enumerate(VOCAB)}, unk_token="<unk>"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.post_processor = processors.TemplateProcessing(
        single="<s> $A </s>", special_tokens=[("<s>", 0), ("</s>", 2)]
    )
    return tok


def build_model() -> onnx.ModelProto:
    rng = np.random.default_rng(7)
    table = np.round(rng.normal(size=(len(VOCAB), DIM)), 3).astype(np.float32)
    last = np.zeros(DIM, dtype=np.float32)
    last[-1] = 1.0
    inits = [
        numpy_helper.from_array(table, "E"),
        numpy_helper.from_array(last, "e_last"),
        numpy_helper.from_array(np.array([1], dtype=np.int64), "axis1"),
        numpy_helper.from_array(np.array([2], dtype=np.int64), "axis2"),
    ]
    nodes = [
        helper.make_node("Gather", ["E", "input_ids"], ["emb"]),
        helper.make_node("Cast", ["attention_mask"], ["mask_f"], to=TensorProto.FLOAT),
        helper.make_node("ReduceSum", ["mask_f", "axis1"], ["length"], keepdims=1),
        helper.make_node("Unsqueeze", ["length", "axis2"], ["length3"]),
        helper.make_node("Mul", ["length3", "e_last"], ["bump"]),
        helper.make_node("Add", ["emb", "bump"], ["token_embeddings"]),
    ]
    graph = helper.make_graph(
        nodes,
        "tiny_encoder",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b", "s"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b", "s"]),
        ],
        [helper.make_tensor_value_info("token_embeddings", TensorProto.FLOAT, ["b", "s", DIM])],
        initializer=inits,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model


def sha(path: Path) -> dict:
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    onnx.save(build_model(), str(OUT / "model.onnx"))
    build_tokenizer().save(str(OUT / "tokenizer.json"))
    manifest = {
        "schema_version": 1,
        "encoder": "onnx-int8",
        "variant": "tiny-fixture",
        "base_model": "BAAI/bge-m3",
        "base_revision": "fixture",
        "dim": DIM,
        "max_seq_length": MAX_SEQ,
        "format_version": 1,
        "graph": {"pad_id": 1},
        "artifact": {
            "dir": "tiny_encoder",
            "files": {
                "model.onnx": sha(OUT / "model.onnx"),
                "tokenizer.json": sha(OUT / "tokenizer.json"),
            },
        },
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
