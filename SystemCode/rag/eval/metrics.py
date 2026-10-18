"""IR metrics over a ranked id list and a set of relevant ids (binary relevance)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Capped recall: |relevant in top-k| / min(|relevant|, k).

    Rule-labelled gold sets often mark many chunks of one section as relevant; plain
    recall would then be bounded by k/|R| and could never reach 1. Capping the
    denominator at k makes 1.0 attainable and keeps the metric comparable across queries.
    """
    if not relevant:
        return 0.0
    hits = sum(1 for c in ranked[:k] if c in relevant)
    return hits / min(len(relevant), k)


def hit_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    return 1.0 if any(c in relevant for c in ranked[:k]) else 0.0


def mrr_at_k(ranked: Sequence[str], relevant: set[str], k: int = 10) -> float:
    for i, c in enumerate(ranked[:k], 1):
        if c in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: set[str], k: int = 10) -> float:
    if not relevant:
        return 0.0
    dcg = sum(1.0 / math.log2(i + 1) for i, c in enumerate(ranked[:k], 1) if c in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def percentile(values: Iterable[float], q: float) -> float:
    xs: list[float] = sorted(values)
    if not xs:
        return 0.0
    pos = (len(xs) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
