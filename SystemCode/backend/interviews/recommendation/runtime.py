"""Responsibilities: Run fixed v4-B bidirectional tree inference.
Implementation: Validate the repository model artifact and return ranking scores with
data-availability metadata without downloading weights.
Related Modules: schemas validates request data; features builds the trained feature vector; api
maps inference failures to HTTP responses.

Declaration Index:
- ModelUnavailable: model file, version, or inference anomaly; mapped to 503 via API.
- load_bundle: verifies manifest, SHA256, input column count, and number of trees, then caches local
  model.
- rank_pairs: constructs missing-aware features, batch inference, and stable sort by specified
  direction.

Variable Index:
- MODEL_ID: unique access point for experimental model version.
- MODEL_DIR: fixed read-only model package directory; HTTP cannot specify path.
- logger: logs only version, request scale, status, and exception, without resume or field values.
- _PREDICT_LOCK: process-level serialization of model initialization and inference to prevent shared
  Booster concurrent access.

Design Notes:
schemas handle data contract; features handle original 11-dimensional order. All-NaN pairs return
insufficient evidence; otherwise, compute two raw scores using CPU dual threads. No normalization,
no zero-filling, no fallback model switching, no database reading.
"""

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from threading import Lock

import lightgbm as lgb
import numpy as np

from .features import FEATURE_NAMES, build_features
from .schemas import CandidateInput, JobInput

MODEL_ID = "jobrec-v4-B"
MODEL_DIR = Path(__file__).resolve().parent / "artifacts" / MODEL_ID
logger = logging.getLogger(__name__)
_PREDICT_LOCK = Lock()


class ModelUnavailable(RuntimeError):
    """Function: indicates local model unavailable; logic: maps to 503 via API and logs cause;
    constraint: does not trigger retry or alternative scoring.
    """


@lru_cache(maxsize=1)
def load_bundle() -> tuple:
    """Function: reads fixed model bundle; inputs fixed MODEL_DIR and installed version, outputs
    manifest and two Boosters (pref/qual).

    Logic: first verify manifest and byte hash, then load and confirm 11 dimensions and 100/20
    trees; successful result cached in-process.
    Constraints: caller holds inference lock; file/format/library errors logged and raise
    ModelUnavailable; no network or file repair.
    After cache activation, updating model requires process restart; no request-level switching
    supported.
    """
    try:
        manifest = json.loads((MODEL_DIR / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest["model_id"] != MODEL_ID
            or manifest["feature_names"] != list(FEATURE_NAMES)
            or manifest["lightgbm_version"] != lgb.__version__
            or lgb.__version__ != "4.6.0"
        ):
            raise ValueError("Model manifest or LightGBM version mismatch")
        models = []
        for task, trees in (("pref", 100), ("qual", 20)):
            path = MODEL_DIR / (task + ".txt")
            entry = manifest["files"][task + ".txt"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError(f"Model checksum mismatch: {task}")
            model = lgb.Booster(model_file=str(path))
            if model.num_feature() != 11 or model.current_iteration() != trees:
                raise ValueError(f"Model input or tree count mismatch: {task}")
            models.append(model)
        logger.info("Recommendation model loaded model=%s dimensions=11 trees=100,20", MODEL_ID)
        return manifest, tuple(models)
    except (OSError, ValueError, KeyError, TypeError, lgb.basic.LightGBMError) as exc:
        logger.exception(
            "Recommendation model unavailable model=%s; inspect local bundle", MODEL_ID
        )
        raise ModelUnavailable("Recommendation model unavailable; inspect server logs") from exc


def rank_pairs(pairs: list[tuple[CandidateInput, JobInput]], direction: str) -> dict:
    """Function: ranks candidate pool for a query; inputs validated pair list and jobs/candidates
    direction, outputs JSON dictionary.

    Logic: only infer rows with available features > 0; return both model raw scores, sorted
    descending by pref or qual.
    All-NaN rows returned last, with rank and score as None and marked insufficient_evidence; ties
    preserve request order.
    Constraints: upstream guarantees 1 to 100 rows and consistent query identity; unknown direction
    or overflow raises ValueError; inference failure raises ModelUnavailable.
    Only log statistics; no database write; available_feature_count does not indicate confidence;
    ranking is not a hiring decision.
    """
    if direction not in ("jobs", "candidates") or not pairs:
        raise ValueError("Invalid recommendation direction or empty pool")
    matrix = np.stack([build_features(candidate, job) for candidate, job in pairs])
    available = np.isfinite(matrix).sum(axis=1)
    valid = available > 0
    scores = np.full((len(pairs), 2), np.nan)
    with _PREDICT_LOCK:
        manifest, models = load_bundle()
        if valid.any():
            try:
                predictions = np.column_stack(
                    [model.predict(matrix[valid], num_threads=2) for model in models]
                )
                if not np.isfinite(predictions).all():
                    raise ValueError("Non-finite prediction")
                scores[valid] = predictions
            except (ValueError, lgb.basic.LightGBMError) as exc:
                logger.exception(
                    "Recommendation inference failed model=%s rows=%d", MODEL_ID, len(pairs)
                )
                raise ModelUnavailable(
                    "Recommendation prediction failed; inspect server logs"
                ) from exc
    column = 0 if direction == "jobs" else 1
    order = sorted(
        range(len(pairs)), key=lambda i: -scores[i, column] if valid[i] else float("inf")
    )
    results = []
    for position, index in enumerate(order, 1):
        candidate, job = pairs[index]
        values = matrix[index]
        results.append(
            {
                "candidate_id": candidate.candidate_id,
                "job_id": job.job_id,
                "status": "scored" if valid[index] else "insufficient_evidence",
                "rank": position if valid[index] else None,
                "pref_score": float(scores[index, 0]) if valid[index] else None,
                "qual_score": float(scores[index, 1]) if valid[index] else None,
                "available_feature_count": int(available[index]),
                "missing_features": [
                    name
                    for name, value in zip(FEATURE_NAMES, values, strict=True)
                    if np.isnan(value)
                ],
            }
        )
    logger.info(
        "Recommendation completed model=%s direction=%s rows=%d scored=%d insufficient=%d",
        MODEL_ID,
        direction,
        len(pairs),
        int(valid.sum()),
        int((~valid).sum()),
    )
    return {
        "model_id": MODEL_ID,
        "experimental": True,
        "release_gate_passed": manifest["release_gate_passed"],
        "score_type": "uncalibrated_ranking_score",
        "qualification_target": "synthetic_per_job_winner",
        "direction": direction,
        "sorted_by": "pref_score" if direction == "jobs" else "qual_score",
        "results": results,
    }
