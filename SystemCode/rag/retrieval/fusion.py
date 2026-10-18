"""Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

RRF_K = 60


def rrf_fuse(
    rankings: Mapping[str, Sequence[str]],
    k: int = RRF_K,
    weights: Mapping[str, float] | None = None,
) -> list[tuple[str, float]]:
    """rankings: stage name -> ids best-first. score(id) = sum_w w / (k + rank), rank 1-based.

    Ties are broken by best (lowest) rank in any list, then by id, so output is deterministic.
    Duplicate ids within one list count once, at their first position.
    """
    scores: dict[str, float] = {}
    best: dict[str, int] = {}
    for name, ids in rankings.items():
        w = 1.0 if weights is None else weights.get(name, 1.0)
        seen = set()
        pos = 0
        for cid in ids:
            if cid in seen:
                continue
            seen.add(cid)
            pos += 1
            scores[cid] = scores.get(cid, 0.0) + w / (k + pos)
            best[cid] = min(best.get(cid, pos), pos)
    return sorted(scores.items(), key=lambda kv: (-kv[1], best[kv[0]], kv[0]))
