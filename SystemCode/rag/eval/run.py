"""Evaluation runner: compare retrieval modes on the gold set."""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..retrieval.intent import RetrievalIntent
from ..retrieval.retriever import Retriever
from .gold import GoldSet, load_all_chunks, resolve_gold
from .metrics import hit_at_k, mrr_at_k, ndcg_at_k, percentile, recall_at_k

DEFAULT_ROLE = "question_planner"  # may read technical + question, which the gold set spans


@dataclass
class ModeSpec:
    name: str
    mode: str  # dense | lexical | hybrid
    lexical_config: str = "simple"
    rerank: bool = False


ALL_MODES = [
    ModeSpec("dense", "dense"),
    ModeSpec("lexical (simple)", "lexical"),
    ModeSpec("hybrid RRF (simple)", "hybrid"),
    ModeSpec("hybrid RRF + rerank", "hybrid", "simple", True),
]


def evaluate_ranking(ranked: Sequence[str], relevant: set) -> dict[str, float]:
    return {
        "recall@5": recall_at_k(ranked, relevant, 5),
        "recall@10": recall_at_k(ranked, relevant, 10),
        "mrr@10": mrr_at_k(ranked, relevant, 10),
        "ndcg@10": ndcg_at_k(ranked, relevant, 10),
        "hit@10": hit_at_k(ranked, relevant, 10),
    }


def run_eval(
    gold: GoldSet,
    modes: Sequence[ModeSpec] = tuple(ALL_MODES),
    role: str = DEFAULT_ROLE,
    dsn: str | None = None,
    retriever_factory: Callable[[str], Retriever] | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    chunks = load_all_chunks()
    relevant = resolve_gold(gold, chunks)
    unresolved = [q.id for q in gold.queries if not relevant[q.id]]
    if unresolved:
        raise ValueError(f"gold queries with no matching chunks: {unresolved}")
    cat_of = {q.id: q.category for q in gold.queries}

    results: dict[str, dict] = {}
    retrievers: dict[str, Retriever] = {}
    shared: dict[str, object] = {}

    def get_retriever(cfg: str) -> Retriever:
        if cfg not in retrievers:
            r = (
                retriever_factory(cfg)
                if retriever_factory
                else Retriever(dsn=dsn, lexical_config=cfg)
            )
            # share heavy models between retrievers
            if "emb" in shared:
                r._embedder = shared["emb"]
            if "rr" in shared:
                r._reranker = shared["rr"]
            retrievers[cfg] = r
        return retrievers[cfg]

    try:
        for spec in modes:
            r = get_retriever(spec.lexical_config)
            log(f"[eval] mode: {spec.name}")
            # warm-up (model load / plan caches) -- not timed
            first = RetrievalIntent(query=gold.queries[0].query, k=10)
            r.retrieve(first, role, mode=spec.mode, rerank=spec.rerank)
            if spec.mode != "lexical":
                shared["emb"] = r.embedder
            if spec.rerank:
                shared["rr"] = r.reranker
            per_q: dict[str, dict] = {}
            lat: list[float] = []
            for q in gold.queries:
                intent = RetrievalIntent(query=q.query, k=10)
                t = time.perf_counter()
                passages = r.retrieve(intent, role, mode=spec.mode, rerank=spec.rerank)
                dt = (time.perf_counter() - t) * 1000
                ranked = [p.chunk_id for p in passages]
                m = evaluate_ranking(ranked, relevant[q.id])
                first_hit = next((i for i, c in enumerate(ranked, 1) if c in relevant[q.id]), None)
                per_q[q.id] = {
                    **m,
                    "latency_ms": dt,
                    "first_hit_rank": first_hit,
                    "n_relevant": len(relevant[q.id]),
                }
                lat.append(dt)
            results[spec.name] = {
                "per_query": per_q,
                "summary": summarize(per_q),
                "by_category": by_category(per_q, cat_of),
                "latency_ms": {
                    "p50": percentile(lat, 0.5),
                    "p95": percentile(lat, 0.95),
                    "mean": statistics.fmean(lat),
                },
            }
    finally:
        for r in retrievers.values():
            r.close()
    from .stats import compare_modes

    sig = compare_modes(
        {name: m["per_query"] for name, m in results.items()},
        [
            ("hybrid RRF (simple)", "dense"),
            ("hybrid RRF + rerank", "hybrid RRF (simple)"),
            ("hybrid RRF + rerank", "dense"),
        ],
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "significance": sig,
        "gold_status": gold.status,
        "role": role,
        "n_queries": len(gold.queries),
        "relevant_counts": {qid: len(v) for qid, v in relevant.items()},
        "modes": results,
    }


METRIC_KEYS = ["recall@5", "recall@10", "mrr@10", "ndcg@10", "hit@10"]


def summarize(per_q: dict[str, dict]) -> dict[str, float]:
    return {k: statistics.fmean(v[k] for v in per_q.values()) for k in METRIC_KEYS}


def by_category(per_q: dict[str, dict], cat_of: dict[str, str]) -> dict[str, dict[str, float]]:
    groups: dict[str, dict[str, dict]] = defaultdict(dict)
    for qid, v in per_q.items():
        groups[cat_of[qid]][qid] = v
    return {c: {**summarize(g), "n": len(g)} for c, g in sorted(groups.items())}


def render_markdown(report: dict) -> str:
    L: list[str] = []
    L.append("# Retrieval evaluation")
    L.append("")
    L.append(f"- generated: {report['generated_at']}")
    L.append(f"- gold set: {report['n_queries']} queries, status: **{report['gold_status']}**")
    L.append(f"- role: `{report['role']}`; k=10; recall is capped recall (denominator min(|R|, k))")
    L.append("")
    L.append("## Overall")
    L.append("")
    L.append("| mode | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | Hit@10 | p50 ms | p95 ms |")
    L.append("|---|---|---|---|---|---|---|---|")
    for name, m in report["modes"].items():
        s, lt = m["summary"], m["latency_ms"]
        L.append(
            f"| {name} | {s['recall@5']:.3f} | {s['recall@10']:.3f} | {s['mrr@10']:.3f} | "
            f"{s['ndcg@10']:.3f} | {s['hit@10']:.3f} | {lt['p50']:.0f} | {lt['p95']:.0f} |"
        )
    cats = sorted({c for m in report["modes"].values() for c in m["by_category"]})
    for c in cats:
        L.append("")
        L.append(f"## Category: {c}")
        L.append("")
        L.append("| mode | n | Recall@10 | MRR@10 | nDCG@10 |")
        L.append("|---|---|---|---|---|")
        for name, m in report["modes"].items():
            s = m["by_category"].get(c)
            if s:
                L.append(
                    f"| {name} | {s['n']} | {s['recall@10']:.3f} | {s['mrr@10']:.3f} | "
                    f"{s['ndcg@10']:.3f} |"
                )
    L.append("")
    if report.get("significance"):
        L.append("")
        L.append(
            "## Paired bootstrap 95% CI of per-query differences (10,000 resamples of the queries)"
        )
        L.append("")
        L.append("| comparison | metric | mean diff | 95% CI | shown? |")
        L.append("|---|---|---|---|---|")
        for pair, ms in report["significance"].items():
            for m, c in ms.items():
                L.append(
                    f"| {pair} | {m} | {c['mean_diff']:+.3f} | [{c['lo']:+.3f}, {c['hi']:+.3f}] | "
                    f"{'yes' if c['significant'] else 'not shown'} |"
                )
        L.append("")
        L.append(
            "hybrid-vs-dense compares the *tuned* hybrid on the queries it was tuned on "
            "(optimistic); "
            "see the held-out block below. 'not shown' means the CI includes 0 on 40 draft "
            "queries, not that there is no effect."
        )
    h = report.get("heldout")
    if h:
        d = h["dense_only"]
        L.append("")
        L.append("## In-sample vs held-out nDCG@10 (baseline = dense-only)")
        L.append("")
        L.append("| | nDCG@10 |")
        L.append("|---|---|")
        L.append(f"| dense-only | {d['ndcg']:.3f} |")
        L.append(
            f"| pre-tuning default hybrid (k=60, equal weights) | {h['default']['ndcg']:.3f} |"
        )
        L.append(f"| tuned hybrid, in-sample | {h['best_on_all']['ndcg']:.3f} |")
        L.append(f"| tuned hybrid, leave-one-out | {h['loo']['tuned_ndcg']:.3f} |")
        L.append(
            f"| tuned hybrid, repeated 2-fold (sd {h['two_fold']['tuned_sd']:.3f}) | "
            f"{h['two_fold']['tuned_ndcg']:.3f} |"
        )
        c = h["loo"]["tuned_minus_dense_ci"]
        L.append("")
        L.append(
            f"Held-out (LOO) tuned hybrid minus dense-only: {c['mean_diff']:+.3f} [{c['lo']:+.3f}, "
            f"{c['hi']:+.3f}] "
            f"({'excludes 0' if c['significant'] else 'includes 0: not shown'})."
        )
        L.append(
            "Rerank over hybrid: not yet shown significant on the current gold set (CIs above "
            "include 0); its edge over dense-only is borderline and rests on an in-sample-tuned "
            "hybrid."
        )
    L.append("")
    L.append(
        "Latency covers the whole `retrieve()` call (query embedding + SQL + fusion [+ rerank]) "
        "after one warm-up query."
    )
    return "\n".join(L) + "\n"


def write_report(report: dict, out_dir: Path, ts: str | None = None) -> dict[str, Path]:
    ts = ts or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    md, js = out_dir / f"retrieval-eval-{ts}.md", out_dir / f"retrieval-eval-{ts}.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"md": md, "json": js}
