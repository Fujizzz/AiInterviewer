"""CLI handler: search."""

from __future__ import annotations

import argparse
import json

from .access import NamespaceAccessError, allowed_for_role, resolve_namespaces
from .intent import Filters, RetrievalIntent


def _csv(v):
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


def cmd_search(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="rag.cli search")
    p.add_argument("query")
    p.add_argument("--role", required=True, help="question_planner | evaluator | recommender")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--rerank", action="store_true")
    p.add_argument(
        "--min-rerank",
        type=float,
        default=None,
        help="drop passages with rerank score below this (needs --rerank)",
    )
    p.add_argument("--mode", choices=["dense", "lexical", "hybrid"], default="hybrid")
    p.add_argument(
        "--namespace",
        default=None,
        help="comma-separated, must be allowed for the role (case-sensitive)",
    )
    p.add_argument("--domain", default=None)
    p.add_argument("--source-name", default=None)
    p.add_argument("--tech", default=None, help="related_technologies, comma-separated")
    p.add_argument("--difficulty", default=None)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    # Everything that can be rejected without a database is rejected first, as ONE clean line (no
    # traceback).
    try:
        intent = RetrievalIntent(
            query=a.query,
            k=a.k,
            namespaces=_csv(a.namespace) or None,
            difficulty=a.difficulty,
            filters=Filters(
                domain=_csv(a.domain),
                source_name=_csv(a.source_name),
                related_technologies=_csv(a.tech),
            ),
        )
        allowed_for_role(a.role)
        resolve_namespaces(a.role, intent.namespaces)
        if a.min_rerank is not None and not a.rerank:
            raise ValueError("--min-rerank requires --rerank")
    except NamespaceAccessError as exc:
        print(f"[search] ACCESS DENIED: {exc}")
        return 4
    except ValueError as exc:
        print(f"[search] INVALID REQUEST: {exc}")
        return 4

    from ..index.db import db_reachable
    from ..index.load import IndexError_
    from .retriever import Retriever

    if not db_reachable():
        print("[search] Postgres is not reachable (is `docker compose up -d` running?)")
        return 2
    r = Retriever()
    try:
        res = r.retrieve(
            intent, a.role, mode=a.mode, rerank=a.rerank, min_rerank_score=a.min_rerank
        )
    except NamespaceAccessError as exc:
        print(f"[search] ACCESS DENIED: {exc}")
        return 4
    except IndexError_ as exc:
        print(f"[search] INDEX MISMATCH: {exc}")
        return 3
    except Exception as exc:  # DB/driver errors: one line, no traceback
        print(f"[search] ERROR: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        return 1
    finally:
        r.close()
    if a.json:
        print(
            json.dumps(
                {"signals": res.signals(), "passages": [p.to_dict() for p in res]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    def f(v):
        return "-" if v is None else f"{v:.3f}"

    print(
        f"confidence={res.confidence} low_confidence={res.low_confidence} "
        f"max_dense={f(res.max_dense)} "
        f"max_rerank={f(res.max_rerank)} lexical_hit={res.lexical_hit}"
    )
    if res.low_confidence:
        print("  -> LOW CONFIDENCE: treat as NO EVIDENCE (" + "; ".join(res.reasons) + ")")
    for ps in res:
        s = ps.scores
        print(
            f"{ps.rank}. [{ps.namespace}/{ps.source_name}] "
            f"{' > '.join(ps.heading_path) or ps.title}"
        )
        print(
            f"   dense={f(s['dense'])} lex={f(s['lexical'])} rrf={f(s['rrf'])} "
            f"rerank={f(s['rerank'])}  {ps.chunk_id}"
        )
        print(f"   {ps.source_url}")
        print("   " + ps.text[:160].replace("\n", " "))
    if not res:
        print("(no results)")
    return 0
