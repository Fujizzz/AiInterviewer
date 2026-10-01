"""固定v4-B双向树推理。校验随仓库发布的权重，输出排序分数和资料可用性，不联网下载。

目录：
- ModelUnavailable：模型文件、版本或推理异常，交给API映射为503。
- load_bundle：核验清单、SHA256、输入列数和树数并缓存本地模型。
- rank_pairs：构造缺失感知特征、批量推理并按指定方向稳定排序。

关键变量：
- MODEL_ID：唯一接入的实验模型版本。
- MODEL_DIR：只读模型包的固定目录，HTTP不能指定路径。
- logger：仅记录版本、请求规模、状态和异常，不记录简历或字段值。
- _PREDICT_LOCK：进程内串行化模型初始化和推理，避免共享Booster并发访问。

设计说明：
schemas负责数据契约，features负责原11维顺序。全NaN配对返回依据不足，
其余按CPU两线程计算两个原始分数；不归一化、不补零、不切换备用模型、不读取数据库。
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
    """功能：表示本地模型不可用；逻辑：API转换503并记录原因；约束：不触发重试或替代评分。"""


@lru_cache(maxsize=1)
def load_bundle() -> tuple:
    """功能：读取固定模型包；输入固定MODEL_DIR及安装版本，输出manifest和pref/qual两个Booster。

    逻辑：先校验清单及字节哈希，再加载并核对11维、100/20棵树；成功结果进程内缓存。
    约束：调用方持有推理锁；文件/格式/库错误记录异常并抛ModelUnavailable；不联网或修复文件。
    缓存生效后更新模型需要重启进程，不支持请求级切换。
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
    """功能：对一个查询的候选池排序；输入验证后的配对列表和jobs/candidates方向，输出JSON字典。

    逻辑：可用特征大于零的行才推理；两模型原始分数都返回，按pref或qual降序稳定排序。
    全NaN行最后返回，rank和分数为None并标记insufficient_evidence；同分保持请求次序。
    约束：上游保证1至100行且查询身份一致；未知方向/溢出抛ValueError，推理失败抛ModelUnavailable。
    仅记录统计日志，无数据库写入；available_feature_count不代表置信度，排名不是招聘决策。
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
