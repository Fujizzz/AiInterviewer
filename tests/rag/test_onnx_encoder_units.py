"""OnnxQueryEncoder on a tiny committed fixture: pooling, normalisation, padding, truncation,
manifest/artifact verification, threads. No real model needed.

Needs numpy for the pure helpers; the session tests also need onnxruntime + tokenizers.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from rag.index import onnx_encoder as OE  # noqa: E402
from rag.index.onnx_encoder import (  # noqa: E402
    EncoderUnavailableError,
    load_manifest,
    pad_batch,
    pool_and_normalize,
    verify_artifact,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_encoder"
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


# ------------------------------------------------------------------ pure helpers


def test_pool_and_normalize_takes_the_cls_token_not_the_mean():
    hidden = np.array([[[3.0, 4.0], [100.0, 0.0], [0.0, 100.0]]], dtype=np.float32)
    out = pool_and_normalize(hidden)
    assert out.shape == (1, 2) and out.dtype == np.float32
    np.testing.assert_allclose(out, [[0.6, 0.8]], atol=1e-6)  # CLS (3,4) / 5, tokens 1-2 ignored


def test_pool_and_normalize_is_unit_norm_and_survives_a_zero_vector():
    rng = np.random.default_rng(0)
    out = pool_and_normalize(rng.normal(size=(5, 7, 16)).astype(np.float32))
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-6)
    zero = pool_and_normalize(np.zeros((1, 3, 4), dtype=np.float32))
    assert np.isfinite(zero).all() and not zero.any()


def test_pad_batch_right_pads_and_masks():
    ids, mask = pad_batch([[0, 5, 2], [0, 2]], pad_id=1)
    assert ids.tolist() == [[0, 5, 2], [0, 2, 1]]
    assert mask.tolist() == [[1, 1, 1], [1, 1, 0]]
    assert ids.dtype == mask.dtype == np.int64


# ------------------------------------------------------------------ manifest + artifact


def test_manifest_of_the_fixture_loads():
    m = load_manifest(FIXTURE / "manifest.json")
    assert (m.base_model, m.dim, m.max_seq_length, m.pad_id) == ("BAAI/bge-m3", 8, 6, 1)


def test_the_committed_manifest_describes_bge_m3_at_the_indexed_revision():
    from rag.index import config

    m = load_manifest()  # rag/data/encoder_manifest.json
    assert m.base_model == config.EMBED_MODEL and m.base_revision == config.EMBED_REVISION
    assert (m.dim, m.max_seq_length, m.format_version) == (
        config.EMBED_DIM,
        config.EMBED_MAX_SEQ_LEN,
        config.EMBED_FORMAT_VERSION,
    )
    assert m.raw["pooling"] == "cls" and m.raw["normalize"] is True
    assert set(m.files) == {"model.onnx", "model.onnx.data", "tokenizer.json"}
    assert all(len(f["sha256"]) == 64 and f["bytes"] > 0 for f in m.files.values())
    assert m.raw["license"].startswith("MIT") and m.raw["quantization"]["weight_type"]


def test_manifest_rejects_unknown_schema(tmp_path):
    bad = tmp_path / "m.json"
    bad.write_text(json.dumps({"schema_version": 99, "encoder": "onnx-int8"}))
    with pytest.raises(EncoderUnavailableError, match="unsupported manifest"):
        load_manifest(bad)
    with pytest.raises(EncoderUnavailableError, match="not found"):
        load_manifest(tmp_path / "nope.json")


def test_verify_artifact_accepts_the_fixture_and_names_the_failure(tmp_path):
    m = load_manifest(FIXTURE / "manifest.json")
    verify_artifact(FIXTURE, m)

    with pytest.raises(EncoderUnavailableError, match=r"missing.*build-onnx"):
        verify_artifact(tmp_path, m)

    shutil.copytree(FIXTURE, tmp_path / "copy")
    model = tmp_path / "copy" / "model.onnx"
    model.write_bytes(model.read_bytes()[:-5])
    with pytest.raises(EncoderUnavailableError, match="size"):
        verify_artifact(tmp_path / "copy", m)

    flipped = bytearray((FIXTURE / "model.onnx").read_bytes())
    flipped[-1] ^= 0xFF  # same size, different content
    model.write_bytes(bytes(flipped))
    with pytest.raises(EncoderUnavailableError, match="sha256"):
        verify_artifact(tmp_path / "copy", m)
    verify_artifact(tmp_path / "copy", m, check_sha=False)  # size-only mode lets it through


# ------------------------------------------------------------------ the session


@pytest.fixture(scope="module")
def encoder():
    pytest.importorskip("onnxruntime")
    pytest.importorskip("tokenizers")
    return OE.OnnxQueryEncoder(
        FIXTURE, manifest=load_manifest(FIXTURE / "manifest.json"), threads=1
    )


def test_encode_is_cls_pooled_l2_normalised_float32(encoder):
    out = encoder.encode(["hello world"])
    assert out.shape == (1, 8) and out.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-6)


def test_the_cls_vector_matches_a_hand_computation(encoder):
    import onnxruntime as ort

    sess = ort.InferenceSession(str(FIXTURE / "model.onnx"), providers=["CPUExecutionProvider"])
    ids = np.array([[0, 4, 5, 2]], dtype=np.int64)  # <s> hello world </s>
    hidden = sess.run(
        ["token_embeddings"], {"input_ids": ids, "attention_mask": np.ones_like(ids)}
    )[0]
    want = hidden[0, 0] / np.linalg.norm(hidden[0, 0])
    mean = hidden[0].mean(axis=0)
    np.testing.assert_allclose(encoder.encode(["hello world"])[0], want, atol=1e-6)
    assert not np.allclose(want, mean / np.linalg.norm(mean), atol=1e-3)  # not mean pooling


def test_text_is_encoded_without_any_prefix(encoder):
    assert encoder._tokenizer.encode("hello world").tokens == ["<s>", "hello", "world", "</s>"]


def test_batching_and_padding_do_not_change_a_vector(encoder):
    texts = ["hello", "hello world foo", "bar"]
    batched = encoder.encode(texts, batch_size=3)  # padded together
    single = np.concatenate([encoder.encode([t]) for t in texts])
    np.testing.assert_allclose(batched, single, atol=1e-6)
    np.testing.assert_allclose(encoder.encode(texts, batch_size=2), single, atol=1e-6)


def test_truncation_uses_manifest_max_seq_length(encoder):
    long = " ".join(["foo"] * 20)
    cut = " ".join(["foo"] * 4)  # <s> + 4 + </s> = 6 tokens = max_seq_length
    np.testing.assert_allclose(encoder.encode([long]), encoder.encode([cut]), atol=1e-6)
    longer_ok = encoder.encode([" ".join(["foo"] * 3)])
    assert not np.allclose(longer_ok, encoder.encode([cut]), atol=1e-4)  # length is visible


def test_empty_input_and_blank_text(encoder):
    assert encoder.encode([]).shape == (0, 8)
    assert np.isfinite(encoder.encode([""])).all()


def test_meta_reports_the_base_model_identity_and_the_variant(encoder):
    meta = encoder.meta
    assert meta["model"] == "BAAI/bge-m3" and meta["dim"] == 8 and meta["max_seq_length"] == 6
    assert (meta["encoder"], meta["encoder_variant"]) == ("onnx-int8", "tiny-fixture")


def test_threads_are_configurable_and_validated():
    pytest.importorskip("onnxruntime")
    pytest.importorskip("tokenizers")
    m = load_manifest(FIXTURE / "manifest.json")
    with pytest.raises(ValueError, match="threads"):
        OE.OnnxQueryEncoder(FIXTURE, manifest=m, threads=0)
    enc = OE.OnnxQueryEncoder(FIXTURE, manifest=m, threads=3)
    assert enc.threads == 3 and enc._session.get_session_options().intra_op_num_threads == 3


def test_concurrent_encodes_agree(encoder):
    import threading

    want = encoder.encode(["hello world"])
    results: list[np.ndarray] = []
    lock = threading.Lock()

    def work():
        out = encoder.encode(["hello world"])
        with lock:
            results.append(out)

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 8 and all(np.allclose(r, want) for r in results)


def test_construction_refuses_an_artifact_that_does_not_match_the_manifest(tmp_path):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("tokenizers")
    shutil.copytree(FIXTURE, tmp_path / "x")
    raw = json.loads((FIXTURE / "manifest.json").read_text())
    raw["artifact"]["files"]["model.onnx"]["sha256"] = "0" * 64
    with pytest.raises(EncoderUnavailableError, match="sha256"):
        OE.OnnxQueryEncoder(tmp_path / "x", manifest=OE.EncoderManifest(raw), threads=1)
    # verify=False skips only the hash; a missing file is still an error
    OE.OnnxQueryEncoder(tmp_path / "x", manifest=OE.EncoderManifest(raw), threads=1, verify=False)
    (tmp_path / "x" / "model.onnx").unlink()
    with pytest.raises(EncoderUnavailableError, match="missing"):
        OE.OnnxQueryEncoder(tmp_path / "x", manifest=OE.EncoderManifest(raw), verify=False)


def test_smoke_reference_roundtrip_and_check(encoder, tmp_path):
    queries = ["hello world", "foo bar baz", "one two"]
    ref_path = OE.write_smoke_reference(
        {q: encoder.encode([q])[0] for q in queries}, tmp_path / "r.json"
    )
    res = OE.smoke_check(encoder, ref_path, repeats=2)
    assert res["n"] == 3 and res["mean"] > 0.9999 and res["min"] > 0.9999  # float16 round-off only
    assert 0 < res["p50_ms"] <= res["p95_ms"]
    # a reference made from other vectors is flagged by the cosine, not silently accepted
    other = OE.write_smoke_reference(
        {q: -encoder.encode([q])[0] for q in queries}, tmp_path / "neg.json"
    )
    assert OE.smoke_check(encoder, other, repeats=1)["mean"] < -0.99
