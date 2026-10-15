"""Query-encoder selection: RAG_ENCODER settings, labels, trace field, factory errors.

Needs nothing beyond the standard library, pydantic (shared.contracts) and pytest, so it also
runs in the deploy workflow's locked environment.
"""

from __future__ import annotations

import importlib.util

import pytest

from rag.adapter import RAGUnavailableError, create_rag_adapter
from rag.index.encoders import (
    DERIVED_ENCODERS,
    ENCODER_CHOICES,
    encoder_label,
    encoder_settings_from_env,
    normalize_encoder,
)
from rag.retrieval.confidence import RetrievalResult
from rag.settings import RAGSettings
from tests.rag.adapter_helpers import FakeBackend, make_passage, make_request, make_result


def test_default_encoder_is_torch_and_onnx_is_derived():
    assert RAGSettings().encoder == "torch"
    assert ENCODER_CHOICES == ("torch", "onnx-int8")
    assert DERIVED_ENCODERS == {"onnx-int8"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "torch"),
        ("", "torch"),
        (" Torch ", "torch"),
        ("onnx-int8", "onnx-int8"),
        ("ONNX_INT8", "onnx-int8"),
    ],
)
def test_normalize_encoder(raw, expected):
    assert normalize_encoder(raw) == expected


@pytest.mark.parametrize("raw", ["fp16", "onnx", "onnx-int4", "sentence-transformers"])
def test_normalize_encoder_rejects_unknown(raw):
    with pytest.raises(ValueError, match="encoder must be one of"):
        normalize_encoder(raw)


def test_settings_read_the_encoder_variables():
    s = RAGSettings.from_env(
        {
            "RAG_ENCODER": "onnx-int8",
            "RAG_ENCODER_THREADS": "1",
            "RAG_ENCODER_MODEL_DIR": "/var/lib/ai-interviewer/models/bge-m3-onnx-int8",
            "RAG_ENCODER_VERIFY": "false",
        }
    )
    assert (s.encoder, s.encoder_threads, s.encoder_verify) == ("onnx-int8", 1, False)
    assert s.encoder_model_dir == "/var/lib/ai-interviewer/models/bge-m3-onnx-int8"
    d = RAGSettings.from_env({})
    assert (d.encoder, d.encoder_threads, d.encoder_model_dir, d.encoder_verify) == (
        "torch",
        2,
        None,
        True,
    )


@pytest.mark.parametrize(
    "env",
    [
        {"RAG_ENCODER": "gpu"},
        {"RAG_ENCODER_THREADS": "0"},
        {"RAG_ENCODER_THREADS": "two"},
        {"RAG_ENCODER_VERIFY": "maybe"},
    ],
)
def test_settings_reject_bad_encoder_values(env):
    with pytest.raises(ValueError):
        RAGSettings.from_env(env)


def test_encoder_settings_from_env_feeds_the_cli_path():
    kind, kwargs = encoder_settings_from_env(
        {"RAG_ENCODER": "onnx-int8", "RAG_ENCODER_THREADS": "3"}
    )
    assert kind == "onnx-int8"
    assert kwargs == {"threads": 3, "model_dir": None, "verify": True}


def test_encoder_label():
    assert encoder_label(None) is None
    assert encoder_label({"model": "x"}) is None  # a test double that does not say
    assert encoder_label({"encoder": "torch"}) == "torch"
    assert encoder_label({"encoder": "onnx-int8", "encoder_variant": "int8-pc"}) == (
        "onnx-int8/int8-pc"
    )


def test_encoder_label_is_kept_by_slices_and_signals():
    res = RetrievalResult([1, 2, 3], confidence="high", encoder="onnx-int8/int8-pc")
    assert res[:2].encoder == "onnx-int8/int8-pc"
    assert res.copy().encoder == "onnx-int8/int8-pc"
    assert res.signals()["encoder"] == "onnx-int8/int8-pc"
    assert RetrievalResult().encoder is None


def test_adapter_records_the_encoder_in_every_chunk_metadata():
    import asyncio

    from rag.adapter import RAGAdapter

    result = make_result([make_passage()])
    result.encoder = "onnx-int8/int8-pc"
    adapter = RAGAdapter(FakeBackend(lambda intent, role: result))
    response = asyncio.run(adapter.retrieve(make_request()))
    assert [c.metadata["encoder"] for c in response.chunks] == ["onnx-int8/int8-pc"]


def _find_spec_without(monkeypatch, *missing: str):
    real = importlib.util.find_spec
    sentinel = importlib.util.spec_from_loader("present", loader=None)
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: None if name in missing else (sentinel if name != "rag" else real(name)),
    )


def test_factory_names_the_serve_manifest_when_onnxruntime_is_missing(monkeypatch):
    _find_spec_without(monkeypatch, "onnxruntime")
    with pytest.raises(RAGUnavailableError, match=r"onnxruntime.*requirements-serve\.txt"):
        create_rag_adapter(RAGSettings(encoder="onnx-int8"))


def test_factory_does_not_ask_for_torch_when_the_encoder_is_onnx(monkeypatch):
    _find_spec_without(monkeypatch, "sentence_transformers", "pgvector")
    with pytest.raises(RAGUnavailableError) as info:
        create_rag_adapter(RAGSettings(encoder="onnx-int8"))
    assert "sentence_transformers" not in str(info.value) and "pgvector" in str(info.value)


def test_factory_asks_for_torch_when_the_encoder_is_torch(monkeypatch):
    _find_spec_without(monkeypatch, "sentence_transformers")
    with pytest.raises(RAGUnavailableError, match=r"sentence_transformers.*requirements\.txt"):
        create_rag_adapter(RAGSettings(encoder="torch"))


def test_rerank_is_refused_on_a_torch_free_install(monkeypatch):
    _find_spec_without(monkeypatch, "sentence_transformers")
    with pytest.raises(RAGUnavailableError, match="RAG_RERANK"):
        create_rag_adapter(RAGSettings(encoder="onnx-int8", rerank=True))


def test_a_missing_model_is_a_rag_unavailable_error_not_a_raw_encoder_error():
    """With warmup=False the model loads lazily on the first retrieve(); that failure must reach
    RAGRouter as RAGUnavailableError like every other 'cannot use the retrieval layer' case."""
    import asyncio

    from rag.adapter import RAGAdapter
    from rag.index.encoders import EncoderUnavailableError

    def boom(*args, **kwargs):
        raise EncoderUnavailableError("ONNX encoder file missing: /nowhere/model.onnx")

    adapter = RAGAdapter(FakeBackend(boom), on_warmup=boom)
    with pytest.raises(RAGUnavailableError, match="query encoder unavailable.*model.onnx"):
        asyncio.run(adapter.retrieve(make_request()))
    with pytest.raises(RAGUnavailableError, match="query encoder unavailable"):
        asyncio.run(adapter.warmup())
