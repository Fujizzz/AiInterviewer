"""Responsibilities: verify semantic-gate contracts without claiming model accuracy.

Implementation: mock encoder/HTTP boundaries; run actual artifact validation and gate routing.
Related Modules: completion_gate validates bundles; answer_completion retains Qwen confirmation.

目录：
- bundle：Write checksum-bound fixture bytes and a non-executable logistic head.
- test_unapproved_or_mutated_artifact_is_rejected：Reject unsafe/invalid bundles before loading.
- test_logistic_score_is_stable：Verify extreme learned logits produce bounded finite probabilities.
- test_encoder_preserves_tail_and_excludes_padding：Verify truncation, masking and feature shape.
- test_encoder_keeps_batch_one_for_multiple_tails：Prevent batch-dependent quantization drift.
- test_semantic_filter_requires_qwen_confirmation：Reject avoids API; accept still requires Qwen.
- test_configured_gate_failure_never_bypasses：Verify local failure propagates before HTTP calls.

关键变量：
（无模块级变量。）

约束：
Fixture model bytes are not real ONNX; the encoder is mocked for these contract tests. Real
artifact latency/quality is measured separately on the trained bundle, not inferred from mocks.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest

from agents.answer_completion import FlashCompletionClassifier
from agents.completion_gate import SemanticEncoder, SemanticGate, file_sha256


def bundle(path):
    """Inputs: isolated temporary directory. Outputs: valid contract fixture manifest.
    Logic: actual SHA256 binding and JSON head; encoder bytes deliberately require a mock loader.
    """
    for name in ("encoder.onnx", "tokenizer.json"):
        (path / name).write_bytes(b"fixture")
    (path / "head.json").write_text(json.dumps({
        "weights": [1.0] * 384, "intercept": 0.0, "threshold": 0.5,
    }), encoding="utf-8")
    manifest = {
        "format": "minilm-mean-l2-logistic-v2", "release_approved": True,
        "inference_batch_size": 1,
        "sha256": {name: file_sha256(path / name)
                   for name in ("encoder.onnx", "tokenizer.json", "head.json")},
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


@pytest.mark.parametrize("invalid", ["approval", "batch", "checksum", "threshold", "weights"])
def test_unapproved_or_mutated_artifact_is_rejected(tmp_path, invalid):
    """Inputs: one invalid artifact contract variant. Outputs: explicit ValueError before encoding.
    Logic: update hashes only for intentionally schema-invalid JSON so both guards are exercised.
    """
    manifest = bundle(tmp_path)
    if invalid == "approval":
        manifest["release_approved"] = False
    elif invalid == "batch":
        manifest["inference_batch_size"] = 16
    elif invalid == "checksum":
        (tmp_path / "encoder.onnx").write_bytes(b"mutated")
    else:
        head = json.loads((tmp_path / "head.json").read_text())
        head["threshold" if invalid == "threshold" else "weights"] = (
            0.0 if invalid == "threshold" else [1.0] * 383
        )
        (tmp_path / "head.json").write_text(json.dumps(head), encoding="utf-8")
        manifest["sha256"]["head.json"] = file_sha256(tmp_path / "head.json")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError), patch("agents.completion_gate.SemanticEncoder") as encoder:
        SemanticGate(tmp_path)
    encoder.assert_not_called()


@pytest.mark.parametrize("logit,expected", [(1000, 1.0), (-1000, 0.0)])
def test_logistic_score_is_stable(tmp_path, logit, expected):
    """Inputs: extreme learned intercept and mocked unit features. Outputs: finite probability.
    Constraints: this is numeric contract verification, not a learned semantic decision test.
    """
    bundle(tmp_path)
    with patch("agents.completion_gate.SemanticEncoder") as construct:
        construct.return_value.encode.return_value = np.zeros((1, 384))
        gate = SemanticGate(tmp_path)
    gate.intercept = logit
    assert gate.score("fictional transcript") == expected


def test_encoder_preserves_tail_and_excludes_padding(tmp_path):
    """Inputs: mocked tokenizer and hidden states with high-valued padding vectors.
    Outputs: normalized attended-only vector; last 400 chars and left truncation are enforced.
    Logic: test the real feature transform; ONNX network and vocabulary are explicit mocks.
    """
    tokenizer = Mock()
    tokenizer.token_to_id.return_value = 1
    tokenizer.encode_batch.return_value = [
        SimpleNamespace(ids=[2, 3, 1], attention_mask=[1, 1, 0], type_ids=[0, 0, 0])
    ]
    hidden = np.zeros((1, 3, 384), dtype=np.float32)
    hidden[0, :2, 0] = 2
    hidden[0, 2, 1] = 1000
    session = Mock()
    session.get_inputs.return_value = [SimpleNamespace(name="input_ids")]
    session.run.return_value = [hidden]
    with (
        patch("agents.completion_gate.Tokenizer.from_file", return_value=tokenizer),
        patch("agents.completion_gate.ort.InferenceSession", return_value=session),
    ):
        encoder = SemanticEncoder(tmp_path)
    text = "old context " * 100 + "I have nothing more to add."
    features = encoder.encode([text])
    tokenizer.enable_truncation.assert_called_once_with(max_length=128, direction="left")
    tokenizer.encode_batch.assert_called_once_with([text[-400:]])
    assert features.shape == (1, 384)
    assert features[0, 0] == 1
    assert features[0, 1] == 0


def test_encoder_keeps_batch_one_for_multiple_tails(tmp_path):
    """Inputs: two unequal tails and mocked tokenizer/ONNX boundaries. Outputs: two unit vectors.
    Logic: verify both tokenization and inference always receive one tail, including multi-row
    offline evaluation; a quantization batch change would invalidate the calibrated threshold.
    Constraints: numerical equivalence on real model bytes is a separate artifact check.
    """
    tokenizer = Mock()
    tokenizer.token_to_id.return_value = 1
    tokenizer.encode_batch.return_value = [
        SimpleNamespace(ids=[2, 3], attention_mask=[1, 1], type_ids=[0, 0])
    ]
    session = Mock()
    session.get_inputs.return_value = [SimpleNamespace(name="input_ids")]
    session.run.return_value = [np.ones((1, 2, 384), dtype=np.float32)]
    with (
        patch("agents.completion_gate.Tokenizer.from_file", return_value=tokenizer),
        patch("agents.completion_gate.ort.InferenceSession", return_value=session),
    ):
        encoder = SemanticEncoder(tmp_path)
    features = encoder.encode(["first tail", "second longer tail"])
    assert [c.args[0] for c in tokenizer.encode_batch.call_args_list] == [
        ["first tail"], ["second longer tail"]
    ]
    assert session.run.call_count == 2
    assert all(c.args[1]["input_ids"].shape[0] == 1 for c in session.run.call_args_list)
    np.testing.assert_array_equal(features[0], features[1])


@pytest.mark.parametrize("score,qwen_result", [(0.1, True), (0.9, False), (0.9, True)])
async def test_semantic_filter_requires_qwen_confirmation(score, qwen_result):
    """Inputs: explicit gate score and mocked provider result. Outputs: reject locally or use Qwen.
    Constraints: a candidate positive can never end an answer without the second model confirming.
    """
    gate = SimpleNamespace(score=Mock(return_value=score), threshold=0.5)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())), close=AsyncMock()
    )
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=json.dumps({"finished": qwen_result}))
    )])
    with (
        patch.dict("os.environ", {"DASHSCOPE_API_KEY": "fixture",
                                  "ANSWER_COMPLETION_GATE_PATH": "fixture-dir"}),
        patch("agents.completion_gate.load_gate", return_value=gate),
        patch("agents.answer_completion.AsyncOpenAI", return_value=client),
    ):
        classifier = FlashCompletionClassifier()
    result = await classifier.classify("a fictional interview tail")
    assert result is (qwen_result if score >= gate.threshold else False)
    assert client.chat.completions.create.await_count == (1 if score >= gate.threshold else 0)
    await classifier.close()


async def test_configured_gate_failure_never_bypasses():
    """Inputs: configured gate with failed CPU inference. Outputs: explicit failure, zero API calls.
    Logic: only local encoder is mocked; actual classifier exception routing runs.
    """
    gate = SimpleNamespace(
        score=Mock(side_effect=ValueError("fixture encoding error")), threshold=0.5
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())), close=AsyncMock()
    )
    with (
        patch.dict("os.environ", {"DASHSCOPE_API_KEY": "fixture",
                                  "ANSWER_COMPLETION_GATE_PATH": "fixture-dir"}),
        patch("agents.completion_gate.load_gate", return_value=gate),
        patch("agents.answer_completion.AsyncOpenAI", return_value=client),
    ):
        classifier = FlashCompletionClassifier()
    with pytest.raises(ValueError, match="fixture encoding"):
        await classifier.classify("a fictional interview tail")
    client.chat.completions.create.assert_not_awaited()
    await classifier.close()
