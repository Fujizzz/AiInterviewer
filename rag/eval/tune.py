"""Offline tuning of lexical weights / RRF / top_n on the gold set, with generalisation checks.

Candidate lists are fetched once per query (dense top-100, lexical candidates with their
score components for both tsvector configs); every grid point is then scored in Python, so a
full grid takes seconds. With only ~40 draft queries the grid is deliberately small and the
selection is validated by leave-one-out and repeated 2-fold cross-validation: a tuned config is
only worth adopting if it beats the default on queries it was NOT tuned on.
"""

from __future__ import annotations

import itertools
import json
import random
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..retrieval.access import resolve_namespaces, scope_namespaces
from ..retrieval.fusion import rrf_fuse
from ..retrieval.intent import RetrievalIntent
from ..retrieval.retriever import Retriever
from ..retrieval.sql import build_filter_clause, lexical_params, lexical_sql
from .gold import GoldSet, load_all_chunks, resolve_gold
from .metrics import mrr_at_k, ndcg_at_k, recall_at_k

MAX_LEX = 500
MAX_DENSE = 100


@dataclass(frozen=True)
class Cfg:
    lex: str = "simple"  # only "simple" remains (english was evaluated, then dropped)
    strict: float = 10.0
    ident: float = 3.0
    top_n: int = 50
    rrf_k: int = 60
    w_dense: float = 1.0  # weight of the lexical list is fixed at 1.0

    def complexity(self) -> tuple:
        """Distance from the default config; used to break ties toward the simple default."""
        d = Cfg()
        return (
            self.lex != d.lex,
            self.strict != d.strict,
            self.ident != d.ident,
            self.top_n != d.top_n,
            self.rrf_k != d.rrf_k,
            self.w_dense != d.w_dense,
        )

    def label(self) -> str:
        return (
            f"{self.lex} strict={self.strict:g} ident={self.ident:g} top_n={self.top_n} "
            f"k={self.rrf_k} w_dense={self.w_dense:g}"
        )


GRID = {
    "lex": ["simple"],
    "strict": [0.0, 10.0],
    "ident": [0.0, 3.0, 10.0],
    "top_n": [20, 50, 100],
    "rrf_k": [10, 30, 60],
    "w_dense": [1.0, 2.0, 3.0],
}


def grid() -> list[Cfg]:
    keys = list(GRID)
    return [Cfg(**dict(zip(keys, vals, strict=True))) for vals in itertools.product(*GRID.values())]


# ---------------------------------------------------------------------------


def collect(gold: GoldSet, role: str, r: Retriever) -> dict[str, dict]:
    """Per query: dense [(id, score)], lexical components per tsvector config."""
    # no candidate_id here: the candidate namespace is dropped (fail closed), as in retrieve()
    namespaces = scope_namespaces(resolve_namespaces(role, None), None)
    cache: dict[str, dict] = {}
    for q in gold.queries:
        intent = RetrievalIntent(query=q.query, k=10)
        where, fparams = build_filter_clause(intent, namespaces)
        entry: dict[str, object] = {"dense": r._dense(intent, namespaces, MAX_DENSE)}
        n_terms, n_ids, lp = lexical_params(q.query)
        for lex in ("simple",):
            if n_terms == 0 and n_ids == 0:
                entry[lex] = []
                continue
            sql = lexical_sql(where, n_terms, n_ids, lex, 10.0, 3.0, components=True)
            rows = r.conn.execute(sql, {**lp, **fparams, "top_n": MAX_LEX}).fetchall()
            entry[lex] = [(cid, float(rk), int(st), int(idn)) for cid, _s, rk, st, idn in rows]
        cache[q.id] = entry
    return cache


def ranking(entry: dict, cfg: Cfg) -> list[str]:
    dense = [cid for cid, _ in entry["dense"]][: cfg.top_n]
    lex_rows = sorted(
        entry[cfg.lex], key=lambda t: (-(t[1] + cfg.strict * t[2] + cfg.ident * t[3]), t[0])
    )
    lex = [t[0] for t in lex_rows][: cfg.top_n]
    fused = rrf_fuse(
        {"dense": dense, "lexical": lex},
        k=cfg.rrf_k,
        weights={"dense": cfg.w_dense, "lexical": 1.0},
    )
    return [cid for cid, _ in fused][:10]


def dense_only_ranking(entry: dict) -> list[str]:
    return [cid for cid, _ in entry["dense"]][:10]


def score_dense_only(entry: dict, relevant: set) -> dict[str, float]:
    ranked = dense_only_ranking(entry)
    return {
        "ndcg": ndcg_at_k(ranked, relevant, 10),
        "mrr": mrr_at_k(ranked, relevant, 10),
        "recall10": recall_at_k(ranked, relevant, 10),
        "recall5": recall_at_k(ranked, relevant, 5),
    }


def score_query(entry: dict, relevant: set, cfg: Cfg) -> dict[str, float]:
    ranked = ranking(entry, cfg)
    return {
        "ndcg": ndcg_at_k(ranked, relevant, 10),
        "mrr": mrr_at_k(ranked, relevant, 10),
        "recall10": recall_at_k(ranked, relevant, 10),
        "recall5": recall_at_k(ranked, relevant, 5),
    }


def score_table(
    cache: dict[str, dict], relevant: dict[str, set], cfgs: Sequence[Cfg]
) -> dict[Cfg, dict[str, dict[str, float]]]:
    return {c: {qid: score_query(cache[qid], relevant[qid], c) for qid in cache} for c in cfgs}


def mean_over(
    table_row: dict[str, dict[str, float]], qids: Sequence[str], metric: str = "ndcg"
) -> float:
    return statistics.fmean(table_row[q][metric] for q in qids) if qids else 0.0


def best_cfg(table, qids: Sequence[str]) -> Cfg:
    """Highest mean nDCG@10 on qids; ties (to 1e-9) go to the config closest to the default."""
    return min(
        table, key=lambda c: (-round(mean_over(table[c], qids), 9), c.complexity(), c.label())
    )


def loo(table, qids: list[str], metric="ndcg") -> tuple[float, list[Cfg], dict[str, float]]:
    picks, vals = [], {}
    for held in qids:
        train = [q for q in qids if q != held]
        c = best_cfg(table, train)
        picks.append(c)
        vals[held] = table[c][held][metric]
    return statistics.fmean(vals.values()), picks, vals


def two_fold(
    table, qids: list[str], cat_of: dict[str, str], repeats: int = 30, seed: int = 0
) -> tuple[float, float, float]:
    """Repeated stratified 2-fold: (mean tuned, mean default, sd of tuned) over held-out folds."""
    rng = random.Random(seed)
    tuned, default = [], []
    for _ in range(repeats):
        a, b = [], []
        by_cat: dict[str, list[str]] = {}
        for q in qids:
            by_cat.setdefault(cat_of[q], []).append(q)
        for g in by_cat.values():
            g = g[:]
            rng.shuffle(g)
            for i, q in enumerate(g):
                (a if i % 2 == 0 else b).append(q)
        for train, test in ((a, b), (b, a)):
            c = best_cfg(table, train)
            tuned.append(mean_over(table[c], test))
            default.append(mean_over(table[Cfg()], test))
    return statistics.fmean(tuned), statistics.fmean(default), statistics.pstdev(tuned)


def marginals(table, qids: Sequence[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for key, values in GRID.items():
        out[key] = {}
        for v in values:
            rows = [mean_over(table[c], qids) for c in table if getattr(c, key) == v]
            out[key][str(v)] = statistics.fmean(rows)
    return out


def summarize_cfg(table, qids, cat_of, cfg: Cfg) -> dict[str, object]:
    row = table[cfg]
    d = {m: mean_over(row, qids, m) for m in ("recall5", "recall10", "mrr", "ndcg")}
    d["by_category"] = {
        c: mean_over(row, [q for q in qids if cat_of[q] == c]) for c in sorted(set(cat_of.values()))
    }
    return d


def run_tune(
    gold: GoldSet, role: str = "question_planner", dsn: str | None = None, log=print
) -> dict:
    chunks = load_all_chunks()
    relevant = resolve_gold(gold, chunks)
    cat_of = {q.id: q.category for q in gold.queries}
    qids = [q.id for q in gold.queries]
    r = Retriever(dsn=dsn)
    try:
        log("[tune] collecting candidate lists")
        cache = collect(gold, role, r)
    finally:
        r.close()
    cfgs = grid()
    log(f"[tune] scoring {len(cfgs)} grid points x {len(qids)} queries")
    table = score_table(cache, relevant, cfgs)
    default = Cfg()
    best = best_cfg(table, qids)
    loo_mean, picks, loo_vals = loo(table, qids)
    dense_pq = {q: score_dense_only(cache[q], relevant[q]) for q in qids}
    dense_ndcg = statistics.fmean(v["ndcg"] for v in dense_pq.values())
    from .stats import paired_bootstrap

    best_vals = {q: table[best][q]["ndcg"] for q in qids}
    dense_vals = {q: dense_pq[q]["ndcg"] for q in qids}
    tf_tuned, tf_default, tf_sd = two_fold(table, qids, cat_of)
    # per-category-fold: how often LOO picks the same lexical config / config as the all-data best
    pick_counts: dict[str, int] = {}
    for c in picks:
        pick_counts[c.label()] = pick_counts.get(c.label(), 0) + 1
    top5 = sorted(cfgs, key=lambda c: (-round(mean_over(table[c], qids), 9), c.complexity()))[:5]
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "n_queries": len(qids),
        "grid_size": len(cfgs),
        "grid": GRID,
        "default": {"cfg": asdict(default), **summarize_cfg(table, qids, cat_of, default)},
        "best_on_all": {"cfg": asdict(best), **summarize_cfg(table, qids, cat_of, best)},
        "top5": [{"cfg": c.label(), "ndcg": mean_over(table[c], qids)} for c in top5],
        "dense_only": {
            "ndcg": dense_ndcg,
            "recall10": statistics.fmean(v["recall10"] for v in dense_pq.values()),
            "mrr": statistics.fmean(v["mrr"] for v in dense_pq.values()),
        },
        "loo": {
            "tuned_ndcg": loo_mean,
            "default_ndcg": mean_over(table[default], qids),
            "dense_only_ndcg": dense_ndcg,
            "pick_counts": dict(sorted(pick_counts.items(), key=lambda kv: -kv[1])),
            "tuned_minus_dense_ci": paired_bootstrap(loo_vals, dense_vals),
            "insample_best_minus_dense_ci": paired_bootstrap(best_vals, dense_vals),
        },
        "two_fold": {
            "tuned_ndcg": tf_tuned,
            "default_ndcg": tf_default,
            "dense_only_ndcg": dense_ndcg,
            "tuned_sd": tf_sd,
            "repeats": 30,
        },
        "marginals": marginals(table, qids),
        "per_query_default": {q: table[default][q] for q in qids},
        "per_query_best": {q: table[best][q] for q in qids},
    }


def render_markdown(rep: dict) -> str:
    L = [
        "# Retrieval tuning",
        "",
        f"- {rep['n_queries']} gold queries (draft), grid {rep['grid_size']} points (simple "
        "tsvector only), objective nDCG@10; ties go to the default-like config.",
        "",
        "## Default vs best-on-all-data",
        "",
        "| config | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |",
        "|---|---|---|---|---|",
    ]
    for name in ("default", "best_on_all"):
        r = rep[name]
        c = r["cfg"]
        lab = (
            f"{name}: {c['lex']} strict={c['strict']:g} ident={c['ident']:g} "
            f"top_n={c['top_n']} k={c['rrf_k']} w_dense={c['w_dense']:g}"
        )
        L.append(
            f"| {lab} | {r['recall5']:.3f} | {r['recall10']:.3f} | {r['mrr']:.3f} | "
            f"{r['ndcg']:.3f} |"
        )
    d = rep["dense_only"]
    L += [
        "",
        "## In-sample vs held-out (nDCG@10); baseline = dense-only, not the naive k=60 RRF",
        "",
        "| | nDCG@10 |",
        "|---|---|",
        f"| dense-only (no tuning) | {d['ndcg']:.3f} |",
        f"| pre-tuning default hybrid (k=60, equal weights) | {rep['default']['ndcg']:.3f} |",
        f"| tuned hybrid, IN-SAMPLE (best on all {rep['n_queries']} queries) | "
        f"{rep['best_on_all']['ndcg']:.3f} |",
        f"| tuned hybrid, leave-one-out (held-out) | {rep['loo']['tuned_ndcg']:.3f} |",
        "| tuned hybrid, repeated stratified 2-fold (held-out; sd "
        f"{rep['two_fold']['tuned_sd']:.3f}) | {rep['two_fold']['tuned_ndcg']:.3f} |",
        "",
    ]
    for name, key in (
        ("held-out (LOO) tuned hybrid - dense-only", "tuned_minus_dense_ci"),
        ("in-sample tuned hybrid - dense-only", "insample_best_minus_dense_ci"),
    ):
        c = rep["loo"][key]
        L.append(
            f"- paired bootstrap 95% CI, {name}: {c['mean_diff']:+.3f} [{c['lo']:+.3f}, "
            f"{c['hi']:+.3f}]"
            f" -> {'excludes 0' if c['significant'] else 'includes 0 (not shown)'}"
        )
    L.append("- LOO picks (config -> times chosen):")
    for k, v in list(rep["loo"]["pick_counts"].items())[:6]:
        L.append(f"  - {k}: {v}")
    L += ["", "## Top-5 grid points on all data", ""] + [
        f"- {t['cfg']}: {t['ndcg']:.3f}" for t in rep["top5"]
    ]
    L += [
        "",
        "## Marginal mean nDCG@10 per parameter value (averaged over the rest of the grid)",
        "",
    ]
    for k, vals in rep["marginals"].items():
        L.append(f"- {k}: " + ", ".join(f"{v}={s:.3f}" for v, s in vals.items()))
    return "\n".join(L) + "\n"


def write_report(rep: dict, out_dir: Path, ts: str | None = None) -> dict[str, Path]:
    ts = ts or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    md, js = out_dir / f"retrieval-tune-{ts}.md", out_dir / f"retrieval-tune-{ts}.json"
    md.write_text(render_markdown(rep), encoding="utf-8")
    js.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    return {"md": md, "json": js}


# ---------------------------------------------------------------------------
# ident_bonus study
# ---------------------------------------------------------------------------


def ident_study(
    gold_main: GoldSet,
    gold_ident: GoldSet,
    role: str = "question_planner",
    bonuses: Sequence[float] = (0.0, 1.0, 2.0, 3.0),
    dsn: str | None = None,
) -> dict:
    """Final config with ident_bonus varied; metrics on the main gold set and the
    identifier-only slice, plus paired per-query nDCG differences vs ident_bonus=0."""
    from .stats import paired_bootstrap

    chunks = load_all_chunks()
    base = Cfg(strict=10.0, ident=0.0, top_n=50, rrf_k=10, w_dense=2.0)
    out: dict = {"bonuses": list(bonuses), "sets": {}}
    r = Retriever(dsn=dsn)
    try:
        for label, gold in (("main gold (40)", gold_main), ("identifier slice", gold_ident)):
            rel = resolve_gold(gold, chunks)
            cache = collect(gold, role, r)
            rows, per_q = {}, {}
            for b in bonuses:
                cfg = Cfg(**{**asdict(base), "ident": float(b)})
                pq = {q: score_query(cache[q], rel[q], cfg) for q in cache}
                per_q[b] = {q: v["ndcg"] for q, v in pq.items()}
                rows[b] = {
                    m: statistics.fmean(v[m] for v in pq.values())
                    for m in ("recall5", "recall10", "mrr", "ndcg")
                }
            for b in bonuses:
                rows[b]["ndcg_minus_b0"] = (
                    paired_bootstrap(per_q[b], per_q[bonuses[0]]) if b != bonuses[0] else None
                )
            out["sets"][label] = {"n": len(cache), "rows": {str(b): v for b, v in rows.items()}}
    finally:
        r.close()
    return out


def render_ident_study(rep: dict) -> str:
    L = [
        "# ident_bonus study",
        "",
        "Final config otherwise (strict=10, top_n=50, RRF k=10, dense weight 2). Delta = paired "
        "bootstrap 95% CI of per-query nDCG@10 vs ident_bonus=0.",
        "",
    ]
    for name, st in rep["sets"].items():
        L += [
            f"## {name} (n={st['n']})",
            "",
            "| ident_bonus | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | nDCG delta vs 0 [95% CI] |",
            "|---|---|---|---|---|---|",
        ]
        for b, v in st["rows"].items():
            d = v["ndcg_minus_b0"]
            ds = "-" if d is None else f"{d['mean_diff']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]"
            L.append(
                f"| {float(b):g} | {v['recall5']:.3f} | {v['recall10']:.3f} | {v['mrr']:.3f} | "
                f"{v['ndcg']:.3f} | {ds} |"
            )
        L.append("")
    return "\n".join(L) + "\n"
