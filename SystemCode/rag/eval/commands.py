"""CLI handler: eval."""

from __future__ import annotations

import argparse

from ..index import config
from .gold import load_all_chunks, load_gold, resolve_gold

GOLD_PATH = config.DATA_DIR / "eval" / "gold_queries.yaml"
IDENT_PATH = config.DATA_DIR / "eval" / "identifier_queries.yaml"
NEG_PATH = config.DATA_DIR / "eval" / "negative_queries.yaml"
REPORTS_DIR = config.DATA_DIR / "reports"


def review_table(gold, relevant, chunks) -> str:
    by_id = {c["chunk_id"]: c for c in chunks}
    rows = [
        f"Gold set status: {gold.status}",
        "",
        f"{'id':<5} {'category':<11} {'#rel':>4}  query  ->  sources | sample headings",
    ]
    for q in gold.queries:
        ids = sorted(relevant[q.id])
        srcs = sorted({by_id[i]["source_name"] for i in ids})
        heads = []
        for i in ids[:3]:
            hp = by_id[i]["heading_path"]
            heads.append(" > ".join(hp[-2:]) if hp else by_id[i]["title"] or "?")
        rows.append(
            f"{q.id:<5} {q.category:<11} {len(ids):>4}  {q.query}  ->  {','.join(srcs)} | "
            f"{'; '.join(heads)}"
        )
    return "\n".join(rows)


def cmd_eval(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="rag.cli eval")
    p.add_argument("--gold", default=str(GOLD_PATH))
    p.add_argument(
        "--review", action="store_true", help="print the gold review table and exit (no DB needed)"
    )
    p.add_argument("--no-rerank", action="store_true", help="skip the cross-encoder mode")
    p.add_argument("--role", default="question_planner")
    p.add_argument(
        "--confidence",
        action="store_true",
        help="gold vs negative separation under the confidence rule",
    )
    p.add_argument(
        "--ident-study",
        action="store_true",
        help="ident_bonus in {0,1,2,3} on main gold + identifier slice",
    )
    p.add_argument(
        "--tune",
        action="store_true",
        help="grid-tune lexical weights/RRF/top_n with LOO + 2-fold checks",
    )
    a = p.parse_args(argv)

    gold = load_gold(a.gold)
    chunks = load_all_chunks()
    relevant = resolve_gold(gold, chunks)
    if a.review or not (a.ident_study or a.tune or a.confidence):
        print(review_table(gold, relevant, chunks))
    empty = [q.id for q in gold.queries if not relevant[q.id]]
    if empty:
        print(f"\nERROR: gold queries with no matching chunks: {empty}")
        return 1
    if a.review:
        return 0

    from ..index.db import db_reachable

    if a.confidence:
        from . import confidence_eval as ce

        rep = ce.run_confidence_eval(gold, ce.load_negatives(NEG_PATH), role=a.role)
        paths = ce.write(rep, REPORTS_DIR)
        print(ce.render(rep))
        print(f"[confidence] wrote {paths['md']}")
        return 0
    if a.ident_study:
        import json

        from .tune import ident_study, render_ident_study

        rep = ident_study(gold, load_gold(IDENT_PATH), role=a.role)
        ts = (
            __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .strftime("%Y%m%d-%H%M%S")
        )
        (REPORTS_DIR / f"ident-study-{ts}.json").write_text(json.dumps(rep, indent=2))
        (REPORTS_DIR / f"ident-study-{ts}.md").write_text(render_ident_study(rep))
        print(render_ident_study(rep))
        return 0
    if a.tune:
        from .tune import render_markdown as tmd
        from .tune import run_tune
        from .tune import write_report as twrite

        rep = run_tune(gold, role=a.role)
        paths = twrite(rep, REPORTS_DIR)
        print(tmd(rep))
        print(f"[tune] wrote {paths['md']}")
        return 0
    from .run import ALL_MODES, run_eval, write_report

    if not db_reachable():
        print(
            "\n[eval] Postgres is not reachable; start it with `docker compose up -d`, then "
            "migrate + index."
        )
        return 2
    modes = [m for m in ALL_MODES if not (a.no_rerank and m.rerank)]
    report = run_eval(gold, modes, role=a.role)
    from .tune import run_tune

    t = run_tune(gold, role=a.role, log=lambda *_: None)
    report["heldout"] = {
        k: t[k] for k in ("dense_only", "default", "best_on_all", "loo", "two_fold")
    }
    paths = write_report(report, REPORTS_DIR)
    from .run import render_markdown

    print("\n" + render_markdown(report))
    print(f"[eval] wrote {paths['md']} and {paths['json']}")
    return 0
