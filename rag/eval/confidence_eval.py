"""Separation of gold (answerable) vs negative (off-topic) queries under the confidence rule."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ..retrieval.confidence import DEFAULT_RULE, ConfidenceRule, assess
from ..retrieval.intent import RetrievalIntent
from ..retrieval.retriever import Retriever
from ..retrieval.sql import lexical_params
from .gold import GoldSet, load_all_chunks, resolve_gold

LEVELS = ("low", "medium", "high")


def load_negatives(path: Path) -> dict:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    ids = [x["id"] for x in doc["negative"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate negative ids")
    return doc


def run_confidence_eval(
    gold: GoldSet,
    negatives: dict,
    role: str = "question_planner",
    rule: ConfidenceRule = DEFAULT_RULE,
    dsn: str | None = None,
) -> dict:
    rel = resolve_gold(gold, load_all_chunks())
    r = Retriever(dsn=dsn, confidence_rule=rule)
    rows: list[dict] = []
    try:
        items = (
            [(q.id, "gold", q.query) for q in gold.queries]
            + [(x["id"], "negative", x["query"]) for x in negatives["negative"]]
            + [
                (x["id"], "non_ascii_in_domain", x["query"])
                for x in negatives.get("in_domain_non_ascii", [])
            ]
        )
        for qid, group, query in items:
            res = r.retrieve(RetrievalIntent(query=query, k=10), role, rerank=True)
            n_terms, n_ids, _ = lexical_params(query)
            no_rr, _ = assess(
                len(res),
                res.max_dense,
                None,
                res.lexical_hit,
                n_terms + n_ids,
                query.isascii(),
                rule,
            )
            hit = None
            if group == "gold":
                hit = any(p.chunk_id in rel[qid] for p in res)
            rows.append(
                {
                    "id": qid,
                    "group": group,
                    "query": query,
                    "max_dense": res.max_dense,
                    "max_rerank": res.max_rerank,
                    "lexical_hit": res.lexical_hit,
                    "conf_dense_lex": no_rr,
                    "conf_with_rerank": res.confidence,
                    "gold_hit_at_10": hit,
                    "reasons": res.reasons,
                }
            )
    finally:
        r.close()

    def counts(pred, key) -> dict[str, int]:
        c = Counter(x[key] for x in rows if pred(x))
        return {lv: c.get(lv, 0) for lv in LEVELS}

    groups = {
        "gold (all 40)": lambda x: x["group"] == "gold",
        "gold with a relevant chunk in top-10": lambda x: (
            x["group"] == "gold" and x["gold_hit_at_10"]
        ),
        "gold with NO relevant chunk in top-10 (retrieval failed)": lambda x: (
            x["group"] == "gold" and not x["gold_hit_at_10"]
        ),
        "negative (off-topic)": lambda x: x["group"] == "negative",
        "non-ASCII in-domain (informational)": lambda x: x["group"] == "non_ascii_in_domain",
    }
    table = {
        g: {
            "rule: dense+lexical": counts(p, "conf_dense_lex"),
            "rule: +rerank": counts(p, "conf_with_rerank"),
        }
        for g, p in groups.items()
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rule": rule.__dict__,
        "table": table,
        "rows": rows,
    }


def render(rep: dict) -> str:
    L = [
        "# Confidence rule: gold vs negative separation",
        "",
        f"Rule thresholds: {rep['rule']}. Calibrated on these same queries (not held out).",
        "",
        "| group | regime | low | medium | high |",
        "|---|---|---|---|---|",
    ]
    for g, regimes in rep["table"].items():
        for reg, c in regimes.items():
            L.append(f"| {g} | {reg} | {c['low']} | {c['medium']} | {c['high']} |")
    L += ["", "## Negative queries not flagged low (with rerank)", ""]
    for x in rep["rows"]:
        if x["group"] == "negative" and x["conf_with_rerank"] != "low":
            L.append(
                f"- {x['id']} `{x['query']}`: {x['conf_with_rerank']} (dense {x['max_dense']:.3f}, "
                f"rerank {x['max_rerank']:.3f}, lexical_hit {x['lexical_hit']})"
            )
    L += ["", "## Gold queries flagged low (with rerank)", ""]
    for x in rep["rows"]:
        if x["group"] == "gold" and x["conf_with_rerank"] == "low":
            L.append(
                f"- {x['id']} `{x['query']}`: dense {x['max_dense']:.3f}, rerank "
                f"{x['max_rerank']:.3f}, relevant chunk in top-10: {x['gold_hit_at_10']}"
            )
    return "\n".join(L) + "\n"


def write(rep: dict, out_dir: Path) -> dict[str, Path]:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    md, js = out_dir / f"confidence-eval-{ts}.md", out_dir / f"confidence-eval-{ts}.json"
    md.write_text(render(rep), encoding="utf-8")
    js.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    return {"md": md, "json": js}
