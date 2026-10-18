"""Serving matrix: nDCG + end-to-end latency (real query encoding each call, exact dense)

One environment per run: point RAG_DATABASE_URL / POSTGRES_* at the database and give a label.
Rerank is not offered (the server has no torch). Latency is noisy (p95 varies 30-60% between runs).
  RAG_ENCODER=onnx-int8 uv run python -m rag.eval.studies.serving_latency_matrix --label server-A
"""

from __future__ import annotations

import argparse
import json
import statistics
import time

from rag.eval.gold import load_gold
from rag.eval.metrics import ndcg_at_k, percentile, recall_at_k
from rag.retrieval import sql as rsql
from rag.retrieval.intent import RetrievalIntent
from rag.retrieval.retriever import Retriever

from .common import EVAL_DIR, REPORTS_DIR, ROLE, make_encoder, resolve_live

CONFIGS = [  # (name, mode, lexical term cap, top_n)
    ("dense only", "dense", 12, 50),
    ("lexical only (cap 6)", "lexical", 6, 50),
    ("hybrid cap 12, n 50 (old default)", "hybrid", 12, 50),
    ("hybrid cap 6, n 50 (current default)", "hybrid", 6, 50),
    ("hybrid cap 4, n 50", "hybrid", 4, 50),
    ("hybrid cap 6, n 20", "hybrid", 6, 20),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--gold", default=str(EVAL_DIR / "gold_queries_v2.yaml"))
    a = ap.parse_args()
    gold = load_gold(a.gold)
    enc = make_encoder()
    base = Retriever(embedder=enc)
    rel = resolve_live(gold, base)
    base.close()
    out: dict[str, dict] = {}
    for name, mode, cap, top_n in CONFIGS:
        rsql.content_terms.__defaults__ = (cap,)
        r = Retriever(embedder=enc, top_n=top_n)

        def run(s: str, r: Retriever = r, mode: str = mode):
            return r.retrieve(RetrievalIntent(query=s, k=10), ROLE, mode=mode)

        run("warm up")
        lat, nd, rc = [], [], []
        for g in gold.queries:
            ls = []
            for _ in range(a.reps):
                t = time.perf_counter()
                res = run(g.query)
                ls.append((time.perf_counter() - t) * 1000)
            ids = [p.chunk_id for p in res]
            lat.append(statistics.median(ls))
            nd.append(ndcg_at_k(ids, rel[g.id], 10))
            rc.append(recall_at_k(ids, rel[g.id], 10))
        out[name] = {
            "ndcg": statistics.fmean(nd),
            "recall10": statistics.fmean(rc),
            "p50": percentile(lat, 0.5),
            "p95": percentile(lat, 0.95),
        }
        o = out[name]
        print(f"[{a.label}] {name:38s} nDCG {o['ndcg']:.3f} R@10 {o['recall10']:.3f} "
              f"p50 {o['p50']:.0f} p95 {o['p95']:.0f} ms", flush=True)  # fmt: skip
        r.close()
    (REPORTS_DIR / f"serving-matrix-{a.label}.json").write_text(
        json.dumps({"label": a.label, "reps": a.reps, "configs": out}, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
