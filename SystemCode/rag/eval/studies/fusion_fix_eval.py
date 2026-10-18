"""Adaptive RRF weighting + absent-identifier low confidence vs the previous behaviour.

Sets: gold v2 (nDCG), identifier slice (nDCG), rare corpus tokens (hit@10; auto-sampled with
seed 0 and cached in data/eval/rare_token_queries.json), negatives and invented names (should be
LOW), and the false-low rate on answerable queries. "OLD" = no boost, no absent-identifier rule,
dense_low 0.52.
  uv run python -m rag.eval.studies.fusion_fix_eval
"""

from __future__ import annotations

import json
import random
import re
import statistics
from collections import Counter

import yaml

from rag.eval.gold import load_gold
from rag.eval.metrics import ndcg_at_k
from rag.retrieval.confidence import ConfidenceRule
from rag.retrieval.intent import RetrievalIntent
from rag.retrieval.retriever import Retriever

from .common import EVAL_DIR, ROLE, CachedEmbedder, live_chunks, make_encoder, resolve_live

INVENTED = [
    "TeaCache", "ZorbCache", "QuillFlow", "NimbusQL", "FrobnicateDB", "WidgetMesh", "KryptonSched",
    "PlumbusRPC", "VelloGraph", "ThimbleKV", "OrbitalLRU", "SnorkelFS", "BrambleTrain",
    "CobaltIndex", "LumenStream",
]  # fmt: skip

OLD = {
    "lex_boost_weight": None,
    "absent_identifier_low": False,
    "confidence_rule": ConfidenceRule(dense_low=0.52, dense_floor=0.52),
}
VARIANTS = [
    ("OLD (no boost, dense_low 0.52)", OLD),
    ("boost without dense condition (rejected)", {"lex_boost_dense_below": None,
                                                  "lex_boost_strict_dense_below": None,
                                                  "absent_identifier_low": False}),
    ("NEW defaults", {}),
]  # fmt: skip


def rare_tokens(base: Retriever, chunks: list[dict], n: int = 120) -> list[str]:
    path = EVAL_DIR / "rare_token_queries.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    rnd = random.Random(0)
    sample = rnd.sample(chunks, min(15000, len(chunks)))
    pat = re.compile(r"\b(?:[A-Z][a-z0-9]+){2,}\b|\b[a-z]{2,}(?:[A-Z][a-z0-9]+)+\b")
    df = Counter(t for c in sample for t in set(pat.findall(c["text"])) if len(t) >= 7)
    cand = sorted(t for t, k in df.items() if 1 <= k <= 3)
    rnd.shuffle(cand)
    out: list[str] = []
    for t in cand:
        k = base.conn.execute(
            "SELECT count(*) FROM chunks WHERE text ILIKE %s", (f"%{t}%",)
        ).fetchone()[0]
        if 2 <= k <= 40:
            out.append(t)
        if len(out) == n:
            break
    path.write_text(json.dumps(out), encoding="utf-8")
    return out


def main() -> None:
    gold = load_gold(EVAL_DIR / "gold_queries_v2.yaml")
    ident = load_gold(EVAL_DIR / "identifier_queries.yaml")
    neg = yaml.safe_load((EVAL_DIR / "negative_queries.yaml").read_text(encoding="utf-8"))[
        "negative"
    ]
    enc = CachedEmbedder(make_encoder())
    base = Retriever(embedder=enc, check_model=False)
    chunks = live_chunks(base)
    grel, irel = resolve_live(gold, base), resolve_live(ident, base)
    rare = rare_tokens(base, chunks)
    base.close()
    for name, kw in VARIANTS:
        r = Retriever(embedder=enc, check_model=False, **kw)

        def q(s: str, r: Retriever = r):
            return r.retrieve(RetrievalIntent(query=s, k=10), ROLE, mode="hybrid")

        g = [q(x.query) for x in gold.queries]
        g_nd = statistics.fmean(
            ndcg_at_k([p.chunk_id for p in res], grel[x.id], 10)
            for x, res in zip(gold.queries, g, strict=True)
        )
        i_nd = statistics.fmean(
            ndcg_at_k([p.chunk_id for p in q(x.query)], irel[x.id], 10) for x in ident.queries
        )
        rr = [q(t) for t in rare]
        hit = sum(
            any(t.lower() in p.text.lower() for p in res) for t, res in zip(rare, rr, strict=True)
        )
        n_low = sum(q(x["query"]).confidence == "low" for x in neg)
        inv_low = sum(q(t).confidence == "low" for t in INVENTED)
        g_low = sum(x.confidence == "low" for x in g)
        rare_low = sum(x.confidence == "low" for x in rr)
        print(
            f"{name:42s} gold-v2 nDCG {g_nd:.3f} | ident nDCG {i_nd:.3f} | "
            f"rare hit@10 {hit}/{len(rare)} | "
            f"neg low {n_low}/{len(neg)} | invented low {inv_low}/{len(INVENTED)} | false-low gold "
            f"{g_low}/{len(g)} rare {rare_low}/{len(rr)}",
            flush=True,
        )
        r.close()


if __name__ == "__main__":
    main()
