"""Responsibilities: local semantic candidate filtering before the Qwen intent verifier.

Implementation: pinned INT8 multilingual MiniLM, masked mean pooling and L2 normalization,
then a learned logistic head. Export evaluation imports the serving encoder to prevent input drift.
Related Modules: answer_completion calls the gate; train_v2.py exports the serving bundle.

目录：
- file_sha256：Hash a model file in bounded blocks without deserializing executable objects.
- SemanticEncoder：Offline, CPU-only sentence embedding boundary shared with training.
- SemanticEncoder.__init__：Load fixed ONNX/tokenizer files and bound CPU parallelism.
- SemanticEncoder.encode：Embed <=400-character tails with left token truncation and masked pooling.
- SemanticGate：Validated encoder/head/threshold bundle; never ends an answer directly.
- SemanticGate.__init__：Verify artifact contract, checksums and held-out release criteria.
- SemanticGate.score：Return a numerically stable binary candidate score from learned weights.
- load_gate：Share one explicitly selected artifact across captures within each process.

关键变量：
- logger：Artifact lifecycle metadata only; no transcript or credentials.

状态说明：
encoder.session is CPU-only (two intra-op threads, one inter-op thread); tokenizer truncates
from the left at 128 tokens, preserving the latest ending. lock serializes one encoder's runs.
gate.weights/intercept are learned on training data; threshold is selected on validation only.
The empty environment path is an explicit Qwen-only configuration; loading failures never bypass it.
"""

import hashlib
import json
import logging
import math
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

logger = logging.getLogger(__name__)


def file_sha256(path):
    """Inputs: readable file path. Outputs: hex SHA256; filesystem failures propagate.
    Logic: stream 1MiB blocks; used to bind actual encoder/tokenizer/head bytes to the manifest.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class SemanticEncoder:
    """Functionality: contextual multilingual features, with no lexical cue lists.
    Logic: offline ONNX hidden states -> attention-masked mean -> unit-length vector.
    Constraints: same exported encoder/input/feature transform in release evaluation and serving;
    gradient training uses the corresponding floating-point weights. Serving never downloads files.
    """

    def __init__(self, directory):
        """Inputs: directory containing encoder.onnx and tokenizer.json. Outputs: owned encoder.
        Logic: use CPU provider and fixed 128-token tail; tokenizer padding IDs come from its
        vocabulary rather than the encoder's historical config. Errors propagate at startup.
        """
        directory = Path(directory)
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=128, direction="left")
        pad_id = self.tokenizer.token_to_id("<pad>")
        if pad_id is None:
            raise ValueError("Completion encoder tokenizer lacks its required padding token")
        self.tokenizer.enable_padding(pad_id=pad_id, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        self.session = ort.InferenceSession(
            str(directory / "encoder.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.inputs = {item.name for item in self.session.get_inputs()}
        self.lock = threading.Lock()

    def encode(self, texts):
        """Inputs: nonempty sequence of nonempty transcript strings. Outputs: N x 384 float32.
        Logic: retain last 400 characters, left-truncate at 128 tokens; include attended special
        tokens in pooling as in the source model. Normalize pooled embeddings before the head.
        Constraints: run each tail with batch dimension one, including offline evaluation. Dynamic
        INT8 activation quantization depends on the input batch; grouping unrelated tails would
        change gate probabilities. Padding never contributes; errors propagate without HTTP calls.
        """
        tails = [text.strip()[-400:] for text in texts]
        if not tails or any(not text for text in tails):
            raise ValueError("Completion encoding requires nonempty transcript tails")
        with self.lock:
            vectors = []
            for tail in tails:
                token = self.tokenizer.encode_batch([tail])[0]
                values = {
                    "input_ids": np.asarray([token.ids], dtype=np.int64),
                    "attention_mask": np.asarray([token.attention_mask], dtype=np.int64),
                    "token_type_ids": np.asarray([token.type_ids], dtype=np.int64),
                }
                feed = {k: v for k, v in values.items() if k in self.inputs}
                hidden = self.session.run(None, feed)[0]
                mask = values["attention_mask"].astype(np.float32)[..., None]
                pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
                vectors.append(pooled[0] / max(np.linalg.norm(pooled[0]), 1e-12))
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.shape != (len(tails), 384) or not np.isfinite(vectors).all():
            raise ValueError("Completion encoder produced invalid semantic features")
        return vectors


class SemanticGate:
    """Functionality: candidate probability for the downstream Qwen verifier.
    Logic: validate a non-executable JSON head and artifact hashes, then run the shared encoder.
    Constraints: only approved held-out evaluation bundles load; no rule or alternate-model path.
    """

    def __init__(self, directory):
        """Inputs: configured artifact directory. Outputs: immutable encoder/head/threshold state.
        Logic: manifest identifies the feature contract and exact files; training release criteria
        must be true. Invalid shapes, nonfinite parameters or missing files fail explicitly.
        Constraints: approval means the declared synthetic evaluation passed, not real accuracy.
        """
        directory = Path(directory).resolve(strict=True)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("format") != "minilm-mean-l2-logistic-v2"
            or manifest.get("release_approved") is not True
            or manifest.get("inference_batch_size") != 1
        ):
            raise ValueError("Completion artifact contract/evaluation is not approved")
        for name in ("encoder.onnx", "tokenizer.json", "head.json"):
            if file_sha256(directory / name) != manifest.get("sha256", {}).get(name):
                raise ValueError(f"Completion artifact checksum mismatch: {name}")
        head = json.loads((directory / "head.json").read_text(encoding="utf-8"))
        self.weights = np.asarray(head["weights"], dtype=np.float64)
        self.intercept, self.threshold = float(head["intercept"]), float(head["threshold"])
        if (
            self.weights.shape != (384,)
            or not np.isfinite(self.weights).all()
            or not math.isfinite(self.intercept)
            or not 0 < self.threshold < 1
        ):
            raise ValueError("Completion artifact has invalid head parameters")
        self.encoder = SemanticEncoder(directory)
        logger.info("Completion semantic gate loaded artifact=%s", directory.name)

    def score(self, text):
        """Inputs: one settled transcript tail. Outputs: finite probability in [0, 1].
        Logic: learned linear weights on contextual features followed by stable sigmoid.
        Constraints: a high score only permits Qwen verification, not question progression.
        """
        logit = float(self.encoder.encode([text])[0] @ self.weights + self.intercept)
        if not math.isfinite(logit):
            raise ValueError("Completion gate produced a nonfinite logit")
        if logit >= 0:
            return 1 / (1 + math.exp(-logit))
        value = math.exp(logit)
        return value / (1 + value)


@lru_cache(maxsize=1)
def load_gate(directory):
    """Inputs: explicit artifact path. Outputs: shared gate per process; load failures propagate.
    Logic: cache a single deployment artifact across independent capture clients; no HTTP access.
    Constraints: replacing a bundle requires process restart; no live parameter mutation.
    """
    return SemanticGate(directory)
