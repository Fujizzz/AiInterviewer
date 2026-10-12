"""Gold set: rule-based relevance labels, resolved to chunk_ids by matching chunk files.

Labels are written as *patterns over chunk metadata/text* (source, URL, heading path,
required text regexes) found by reading the corpus -- never by running the retriever --
so the evaluation is not circular. All gold files are drafts until a human reviews them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..index import config

CATEGORIES = {"conceptual", "identifier", "paraphrase", "question"}
MATCH_KEYS = {"source", "url", "heading", "text", "chunk_id"}


@dataclass
class Matcher:
    source: str | None = None
    url: str | None = None  # regex, re.search on source_url
    heading: str | None = None  # regex, re.search (case-insens) on " > ".join(heading_path)
    text: list[str] = field(default_factory=list)  # ALL regexes must match chunk text (case-insens)
    chunk_id: str | None = None

    def matches(self, chunk: dict) -> bool:
        if self.chunk_id and chunk["chunk_id"] != self.chunk_id:
            return False
        if self.source and chunk["source_name"] != self.source:
            return False
        if self.url and not re.search(self.url, chunk.get("source_url") or ""):
            return False
        if self.heading and not re.search(
            self.heading, " > ".join(chunk.get("heading_path") or []), re.I
        ):
            return False
        return all(re.search(t, chunk["text"], re.I | re.S) for t in self.text)


@dataclass
class GoldQuery:
    id: str
    query: str
    category: str
    relevant: list[Matcher]
    note: str = ""


@dataclass
class GoldSet:
    status: str
    queries: list[GoldQuery]


def _matcher(d: dict) -> Matcher:
    unknown = set(d) - MATCH_KEYS
    if unknown:
        raise ValueError(f"unknown matcher keys {sorted(unknown)}")
    if not d:
        raise ValueError("empty matcher")
    text = d.get("text") or []
    if isinstance(text, str):
        text = [text]
    return Matcher(
        source=d.get("source"),
        url=d.get("url"),
        heading=d.get("heading"),
        text=list(text),
        chunk_id=d.get("chunk_id"),
    )


def load_gold(path: Path) -> GoldSet:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    queries: list[GoldQuery] = []
    seen: set[str] = set()
    for q in doc.get("queries", []):
        qid = q["id"]
        if qid in seen:
            raise ValueError(f"duplicate gold id {qid}")
        seen.add(qid)
        if q.get("category") not in CATEGORIES:
            raise ValueError(f"{qid}: category must be one of {sorted(CATEGORIES)}")
        if not q.get("query", "").strip():
            raise ValueError(f"{qid}: empty query")
        rel = [_matcher(m) for m in q.get("relevant", [])]
        if not rel:
            raise ValueError(f"{qid}: no relevance matchers")
        queries.append(GoldQuery(qid, q["query"], q["category"], rel, q.get("note", "")))
    return GoldSet(status=doc.get("status", ""), queries=queries)


def load_all_chunks(
    chunks_dir: Path = config.CHUNKS_DIR, sources: Iterable[str] | None = None
) -> list[dict]:
    out: list[dict] = []
    for p in sorted(Path(chunks_dir).glob("*.jsonl")):
        if sources is not None and p.stem not in sources:
            continue
        with open(p, encoding="utf-8") as f:
            out.extend(json.loads(line) for line in f if line.strip())
    return out


def resolve_gold(gold: GoldSet, chunks: list[dict]) -> dict[str, set[str]]:
    """query id -> relevant chunk_ids (union over its matchers)."""
    by_source: dict[str, list[dict]] = {}
    for c in chunks:
        by_source.setdefault(c["source_name"], []).append(c)
    out: dict[str, set[str]] = {}
    for q in gold.queries:
        ids: set[str] = set()
        for m in q.relevant:
            pool = by_source.get(m.source, []) if m.source else chunks
            ids.update(c["chunk_id"] for c in pool if m.matches(c))
        out[q.id] = ids
    return out
