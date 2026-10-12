"""Paired bootstrap confidence intervals for per-query metric differences."""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence


def paired_bootstrap(
    a: dict[str, float],
    b: dict[str, float],
    n_boot: int = 10000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict[str, float]:
    """Mean of (a - b) over queries, with a percentile bootstrap
    (queries resampled with replacement).

    `significant` = the (1-alpha) interval excludes 0. With ~40 queries this is a low-power test:
    "not significant" means "not shown", not "no difference".
    """
    ids = sorted(set(a) & set(b))
    if not ids:
        raise ValueError("no common queries")
    diffs = [a[i] - b[i] for i in ids]
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(statistics.fmean(rng.choices(diffs, k=n)) for _ in range(n_boot))
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return {
        "mean_diff": statistics.fmean(diffs),
        "lo": lo,
        "hi": hi,
        "n": n,
        "significant": bool(lo > 0 or hi < 0),
    }


def compare_modes(
    per_query: dict[str, dict[str, dict[str, float]]],
    pairs: Sequence[tuple],
    metrics: Sequence[str] = ("ndcg@10", "mrr@10", "recall@10"),
) -> dict[str, dict]:
    """per_query: mode -> qid -> metrics. pairs: (name_a, name_b) -> reports a - b."""
    out: dict[str, dict] = {}
    for ma, mb in pairs:
        if ma not in per_query or mb not in per_query:
            continue
        out[f"{ma} - {mb}"] = {
            m: paired_bootstrap(
                {q: v[m] for q, v in per_query[ma].items()},
                {q: v[m] for q, v in per_query[mb].items()},
            )
            for m in metrics
        }
    return out
