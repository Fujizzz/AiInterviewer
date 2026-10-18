"""Coverage probe with terms chosen WITHOUT looking at the corpus.

data/eval/random_terms_candidates_300.txt was written from general knowledge before any query; this
script samples 250 of them (seed 0) and measures, per term:
  present    the phrase (or, leniently, all its words) occurs in at least one chunk
  retrieved  hybrid top-10 contains a chunk that mentions it (bare term and "Explain <term>")
  solid      in the top 3, a chunk has the term in its heading/title or mentions it >= 2 times
  abstains   confidence is LOW (right for terms the corpus lacks, wrong for terms it has)
"Mentions" is a literal string match: the term can be found, not necessarily explained.
  RAG_ENCODER=onnx-int8 uv run python -m rag.eval.studies.term_coverage
"""

from __future__ import annotations

import json
import random
import re
from datetime import UTC, datetime

from rag.retrieval.intent import RetrievalIntent
from rag.retrieval.retriever import Retriever

from .common import EVAL_DIR, REPORTS_DIR, ROLE, make_encoder

SEED, N = 0, 250
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.]*")


def words(term: str) -> list[str]:
    return [w for w in WORD.findall(term) if len(w) >= 2]


def norm(x: str) -> str:
    return re.sub(r"[\s_-]+", " ", x.lower())


def has_phrase(text: str, term: str) -> bool:
    return norm(term) in norm(text)


def has(text: str, term: str) -> bool:
    low = text.lower()
    return term.lower() in low or all(w.lower() in low for w in words(term))


def like(s: str) -> str:
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def main() -> None:
    lines = (EVAL_DIR / "random_terms_candidates_300.txt").read_text(encoding="utf-8").splitlines()
    cands = [x.strip() for x in lines if x.strip() and not x.startswith("#")]
    terms = random.Random(SEED).sample(cands, N)
    r = Retriever(embedder=make_encoder())
    rows = []
    for t in terms:
        ws = words(t)
        n_phrase = r.conn.execute(
            "SELECT count(*) FROM chunks WHERE text ILIKE %s", (like(t),)
        ).fetchone()[0]
        n_words = r.conn.execute(
            "SELECT count(*) FROM chunks WHERE text ILIKE ALL(%s)", ([like(w) for w in ws],)
        ).fetchone()[0]
        row = {"term": t, "n_phrase": n_phrase, "n_words": n_words}
        for form, q in (("bare", t), ("question", f"Explain {t}")):
            res = r.retrieve(RetrievalIntent(query=q, k=10), ROLE, mode="hybrid")
            first = next((i + 1 for i, p in enumerate(res) if has(p.text, t)), None)
            first_phrase = next((i + 1 for i, p in enumerate(res) if has_phrase(p.text, t)), None)
            solid = any(
                has(" ".join([*p.heading_path, p.title or ""]), t)
                or p.text.lower().count(t.lower()) >= 2
                for p in res[:3]
            )
            top = res[0] if len(res) else None
            snippet = ""
            if top is not None:
                i = top.text.lower().find(ws[0].lower())
                snippet = (
                    top.text[max(0, i - 60) : i + 100].replace("\n", " ")
                    if i >= 0
                    else top.text[:100]
                )
            row[form] = {
                "first_rank": first,
                "first_rank_phrase": first_phrase,
                "solid3": bool(first) and solid,
                "confidence": res.confidence,
                "top1_source": top.source_name if top is not None else None,
                "top1_heading": " > ".join(top.heading_path[-2:]) if top is not None else None,
                "top1_snippet": snippet,
            }
        rows.append(row)
    r.close()

    def pct(k: int, n: int) -> str:
        return f"{k}/{n} ({100 * k / max(n, 1):.0f}%)"

    present = [x for x in rows if x["n_words"] > 0]
    absent = [x for x in rows if x["n_words"] == 0]
    out = [f"terms: {len(rows)} (seed {SEED}, sampled from 300 written without the corpus)"]
    n_phrase = sum(x["n_phrase"] > 0 for x in rows)
    out.append(f"in the corpus, exact phrase           : {pct(n_phrase, len(rows))}")
    out.append(f"in the corpus, all words in one chunk : {pct(len(present), len(rows))}")
    for form in ("bare", "question"):
        hit = sum(bool(x[form]["first_rank"]) for x in present)
        top1 = sum(x[form]["first_rank"] == 1 for x in present)
        solid = sum(x[form]["solid3"] for x in present)
        out.append(
            f"[{form:8s}] of present terms: mention in top 10 {pct(hit, len(present))}, rank 1 "
            f"{pct(top1, len(present))}, solid in top 3 {pct(solid, len(present))}"
        )
        low_abs = sum(x[form]["confidence"] == "low" for x in absent)
        low_pre = sum(x[form]["confidence"] == "low" for x in present)
        out.append(
            f"[{form:8s}] confidence LOW: absent terms {pct(low_abs, len(absent))} (want high), "
            f"present terms {pct(low_pre, len(present))} (want 0)"
        )
    pr = [x for x in rows if x["n_phrase"] > 0]
    for form in ("bare", "question"):
        hit = sum(bool(x[form]["first_rank_phrase"]) for x in pr)
        top1 = sum(x[form]["first_rank_phrase"] == 1 for x in pr)
        out.append(
            f"[{form:8s}] STRICT (exact phrase) of {len(pr)} terms whose phrase is in the corpus: "
            f"in top 10 {pct(hit, len(pr))}, rank 1 {pct(top1, len(pr))}"
        )
    out.append("absent from the corpus: " + ", ".join(x["term"] for x in absent))
    miss = [x["term"] for x in present if not x["bare"]["first_rank"]]
    out.append("present but not retrieved (bare): " + ", ".join(miss))
    text = "\n".join(out)
    print(text)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (REPORTS_DIR / f"term-coverage-{ts}.json").write_text(
        json.dumps(rows, indent=1), encoding="utf-8"
    )
    (REPORTS_DIR / f"term-coverage-{ts}.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
