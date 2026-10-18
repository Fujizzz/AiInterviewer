"""List top-10 hits that are NOT gold-labeled, per query, for manual relevance review.

Why: when the corpus grew, answers from new sources outranked the old labelled chunks and counted as
wrong. This file is the review sheet that led to data/eval/gold_extra_labels.yaml.
  uv run python -m rag.eval.studies.unlabeled_top10_review
  -> data/reports/unlabeled-top10-review.md
"""

from __future__ import annotations

import collections

from rag.eval.gold import load_gold
from rag.retrieval.intent import RetrievalIntent
from rag.retrieval.retriever import Retriever

from .common import EVAL_DIR, REPORTS_DIR, ROLE, make_encoder, resolve_live


def main() -> None:
    gold = load_gold(EVAL_DIR / "gold_queries.yaml")
    r = Retriever(embedder=make_encoder())
    rel = resolve_live(gold, r)
    lines = ["# Unlabeled top-10 hits (hybrid) -- mark each REL / NOT", ""]
    count: collections.Counter = collections.Counter()
    for q in gold.queries:
        res = r.retrieve(RetrievalIntent(query=q.query, k=10), ROLE, mode="hybrid")
        lines += [f"## {q.id} ({q.category}): {q.query}", ""]
        for p in res:
            labelled = p.chunk_id in rel[q.id]
            count["gold" if labelled else "unlabeled"] += 1
            if not labelled:
                head = " > ".join(p.heading_path[-3:])
                lines.append(f"- #{p.rank} {p.source_name} | {head} | {p.text[:220]!r}")
        lines.append("")
    out = REPORTS_DIR / "unlabeled-top10-review.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(dict(count), "->", out)
    r.close()


if __name__ == "__main__":
    main()
