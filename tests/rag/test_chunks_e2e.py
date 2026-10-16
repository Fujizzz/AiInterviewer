"""End-to-end invariants over the real pipeline output (data/chunks/*.jsonl).

Skipped when no chunks have been produced. These tests assert on the *data*; when one fails
the fix belongs in the cleaning pipeline, not in the whitelist below.
"""

import json
import re
from pathlib import Path

import pytest

from rag.htmlmd import KNOWN_HTML_TAGS
from rag.registry import load_registry

ROOT = Path(__file__).resolve().parents[2] / "rag"  # repo/rag (module root: data/, ...)
CHUNKS_DIR = ROOT / "data" / "chunks"
FILES = sorted(CHUNKS_DIR.glob("*.jsonl")) if CHUNKS_DIR.exists() else []

pytestmark = pytest.mark.skipif(not FILES, reason="no data/chunks/*.jsonl — run the pipeline first")


def _rows():
    for f in FILES:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield f.stem, json.loads(line)


@pytest.fixture(scope="module")
def rows():
    return list(_rows())


_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]*(?:\n[^`\n]+)?`")
_ANY_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9-]*)(\s[^>\n]*)?>")


def prose_of(text: str) -> str:
    """Text outside code fences and outside inline code spans."""
    out, in_fence = [], False
    for ln in text.split("\n"):
        if _FENCE.match(ln):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(ln)
    return _INLINE_CODE.sub("", "\n".join(out))


# Tag-shaped tokens that are legitimate *content*, not markup residue. Keep this list tight:
# every entry must be text that a reader is meant to see. Entries are lower-case tag names.
LEGIT_TAG_TOKENS = {
    # --- placeholders in prose / config docs (angle brackets mean "fill this in") ---
    "key": "redis-docs: `<key>` placeholder in a command description",
    "filename": "redis-docs: `<filename>` placeholder",
    "ip": "redis-docs: cluster node line `<ip>:<port>`",
    "port": "redis-docs: cluster node line `<ip>:<port>`",
    "current-epoch": "redis-docs: CLUSTER NODES field name",
    "runid": "redis-docs: replication field name",
    "duration": "kafka-config-reference: `<duration>` placeholder in a config value syntax",
    "user": "kafka-config-reference: SASL/ACL principal syntax `<user>`",
    "client-id": "kafka-config-reference: quota entity syntax `<client-id>`",
    "command": "django: management-command placeholder `<command>` (usage line)",
    "username": "django: `<username>` placeholder",
    "feature": "django: `<feature Y>` placeholder in a deprecation sentence",
    "coroutine": "django: `<coroutine …>` repr shown in prose",
    "lib": "pytorch-tutorials: `<lib>` placeholder in a path",
    "uuid": "vllm: `<uuid>` placeholder in a key format",
    "index": "vllm: `<index>` placeholder in a key format",
    "msgpack": "vllm: `<msgpack>` placeholder",
    "eos": "paper-pagedattention-vllm: `<eos>` end-of-sequence token",
    # --- prose that talks about an HTML element by name ---
    "select": "django (ModelAdmin.radio_fields): 'a select-box interface (<select>)' names the "
    "HTML element",
    # --- leetcode 'Tag Validator' problem: the problem statement is about tags ---
    "div": "leetcode-dataset 'Tag Validator': example inputs/outputs contain <div> tags by design",
    "b": "leetcode-dataset 'Tag Validator': example tag",
    "code": "leetcode-dataset 'Tag Validator': example tag",
}
# Narrow per-source scoping so a whitelisted name cannot hide real residue in another source.
LEGIT_TAG_SOURCES = {
    "key": {"redis-docs"},
    "filename": {"redis-docs"},
    "ip": {"redis-docs"},
    "port": {"redis-docs"},
    "current-epoch": {"redis-docs"},
    "runid": {"redis-docs"},
    "duration": {"kafka-config-reference"},
    "user": {"kafka-config-reference"},
    "client-id": {"kafka-config-reference"},
    "command": {"django"},
    "username": {"django"},
    "feature": {"django"},
    "coroutine": {"django"},
    "select": {"django"},
    "lib": {"pytorch-tutorials"},
    "uuid": {"vllm"},
    "index": {"vllm"},
    "msgpack": {"vllm"},
    "eos": {"paper-pagedattention-vllm"},
    "div": {"leetcode-dataset"},
    "b": {"leetcode-dataset"},
    "code": {"leetcode-dataset"},
}


def test_whitelist_is_documented_and_scoped():
    assert set(LEGIT_TAG_TOKENS) == set(LEGIT_TAG_SOURCES)
    assert all(reason.strip() for reason in LEGIT_TAG_TOKENS.values())


def test_fastapi_has_no_include_placeholders_or_slash_blocks(rows):
    fa = [r for s, r in rows if s == "fastapi"]
    if not fa:
        pytest.skip("fastapi not chunked")
    include = re.compile(r"\{\*[ \t]+\S[^\n]*\*\}|\{![>]?[ \t]*\S+!\}")
    bad = [r["chunk_id"] for r in fa if include.search(r["text"])]
    assert not bad, f"unresolved code includes: {bad[:5]}"
    bad = [r["chunk_id"] for r in fa if re.search(r"(?m)^\s*///", r["text"])]
    assert not bad, f"leftover /// admonition lines: {bad[:5]}"


def _is_placeholder(name: str, attrs: str | None) -> bool:
    """`<namespace>`, `<authentication token>`, `<YOUR-PASSWORD-HERE>`: angle brackets meaning
    "fill this in". Never an HTML element, never PascalCase JSX (`<Tabs>`), never a tag with
    `attr=value` pairs."""
    if attrs and "=" in attrs:
        return False
    if name.lower() in KNOWN_HTML_TAGS:
        return False
    if re.fullmatch(r"[A-Z][a-z0-9]+[A-Za-z0-9]*", name):  # PascalCase component
        return False
    return not name.lower().startswith(("docs-", "mat-", "ng-"))


def test_placeholder_policy_unit():
    assert _is_placeholder("namespace", None)
    assert _is_placeholder("authentication", " token")
    assert _is_placeholder("YOUR-PASSWORD-HERE", None)
    assert not _is_placeholder("div", None)
    assert not _is_placeholder("Tabs", None)
    assert not _is_placeholder("docs-callout", " critical")
    assert not _is_placeholder("select", ' v-model="x"')


# Real content that merely *looks* like markup: HTML shown to the reader in prose because the
# source's own formatting (blockquoted fences, hand-written <pre> inside backticks) defeats fence
# detection. Narrowly scoped per source, every entry justified.
LEGIT_HTML_IN_PROSE = {
    ("mdn-content", "header"): "React tutorial: JSX example inside a blockquoted code fence",
    ("mdn-content", "h1"): "React tutorial: JSX example inside a blockquoted code fence",
    ("mdn-content", "div"): "CSS selector example text in prose",
    ("mdn-content", "video"): "prose naming the <video> element",
    ("mdn-content", "h2"): "WCAG table cell naming heading elements (e.g., <h2>)",
    ("python-docs", "header"): "HTTP header example in prose",
    ("owasp-community", "select"): "prose naming the HTML <select> element",
    ("react-dev", "div"): "prose naming <div> (JSX tutorial)",
    ("react-dev", "select"): "prose naming <select>",
    ("typescript-handbook", "pre"): "TS compiler source shown in a hand-indented block",
    ("typescript-handbook", "h3"): "same block",
    ("go-docs", "pre"): "Go doc-comment syntax page talks about <pre> blocks",
    ("go-docs", "h3"): "Go doc-comment syntax page talks about <h3>",
    ("redis-docs-patterns", "div"): "Redis docs: stray closing tag in a pattern page",
    ("cassandra-docs", "table"): "Cassandra docs: raw table kept by the source",
    (
        "wikipedia-frontend",
        "center",
    ): "Semantic HTML article: names the deprecated <center> element",
    ("wikipedia-frontend", "section"): "Semantic HTML article: names HTML5 sectioning elements",
    ("wikipedia-frontend", "article"): "Semantic HTML article: names HTML5 sectioning elements",
    ("wikipedia-frontend", "div"): "Semantic HTML / JSX articles: markup examples in plain text",
    ("wikipedia-frontend", "video"): "Shadow DOM article: <video> element example",
    ("vite-docs", "audio"): "Vite docs demo of the `<audio>` element",
    ("vite-docs", "svg-image"): "Vite docs: Vue component usage in prose",
    # --- corpus expansion, batch 2 ---
    ("paper-dpo", "summary"): "DPO paper: prompt-template placeholders <Summary A> / <Summary B>",
    ("freertos-kernel-book", "h3"): "FreeRTOS book: stray closing </h3> in the upstream markdown",
    ("hikaricp-pool-sizing", "h1"): "HikariCP README: HTML <h1> title block spanning several lines",
    ("kotlin-multiplatform-docs", "item"): "Android string resources XML shown in a table cell",
    ("kotlin-multiplatform-docs", "plurals"): "Android string resources XML shown in a table cell",
    (
        "kotlin-multiplatform-docs",
        "string-array",
    ): "Android string resources XML shown in a table cell",
    (
        "mmagic-docs",
        "summary",
    ): "model README: <details><summary align=...> wrapper with nested links",
    (
        "pydantic-docs",
        "h3",
    ): "Pydantic models page: dictionary-definition blockquote written in HTML",
    ("cypress-docs", "div"): "Cypress error-messages page: commented jQuery sample with <div />",
    ("cypress-docs", "mountresponse"): "TypeScript generic type name <MountResponse> in a table",
    (
        "paper-self-rag",
        "p",
    ): "Self-RAG paper: <p> / </p> are the special passage tokens of the method",
    ("zephyr-docs", "p"): "Zephyr latency page: <p> is a placeholder for a priority value",
}


def test_no_raw_html_tags_outside_code(rows):
    offenders = []
    for src, r in rows:
        for m in _ANY_TAG.finditer(prose_of(r["text"])):
            name = m.group(1).lower()
            if name in LEGIT_TAG_TOKENS and src in LEGIT_TAG_SOURCES[name]:
                continue
            if (src, name) in LEGIT_HTML_IN_PROSE:
                continue
            if _is_placeholder(m.group(1), m.group(2)):
                continue
            offenders.append((src, r["chunk_id"], m.group(0)[:60]))
    assert not offenders, f"{len(offenders)} raw tag(s), e.g. {offenders[:8]}"


def test_no_heading_id_suffixes(rows):
    rx = re.compile(r"\{#[^}]+\}")
    bad = []
    for src, r in rows:
        if rx.search(r.get("title") or "") or any(
            rx.search(h) for h in r.get("heading_path") or []
        ):
            bad.append((src, r["chunk_id"], "title/heading_path"))
        elif any(ln.startswith("#") and rx.search(ln) for ln in prose_of(r["text"]).split("\n")):
            bad.append((src, r["chunk_id"], "text heading"))
    assert not bad, f"{len(bad)} heading id suffix(es), e.g. {bad[:6]}"


def test_no_tab_directive_in_python_fences(rows):
    bad = []
    for src, r in rows:
        in_fence, lang = False, ""
        for ln in r["text"].split("\n"):
            m = _FENCE.match(ln)
            if m:
                in_fence = not in_fence
                lang = ln.strip()[3:].strip().lower() if in_fence else ""
                continue
            if in_fence and lang.startswith("python") and "%%tab" in ln:
                bad.append((src, r["chunk_id"]))
                break
    assert not bad, bad[:5]


def test_no_snippet_markers(rows):
    assert not [(s, r["chunk_id"]) for s, r in rows if "--8<--" in r["text"]]


_SQUIGGLE = re.compile(r"^\s*~{6,}\s*$")


def _has_orphan_fence(text: str) -> bool:
    """Odd number of fence-marker lines. Runs of six or more tildes without an info string are
    squiggle underlines (TypeScript error output inside a ``` block), not fences."""
    n = sum(1 for ln in text.split("\n") if _FENCE.match(ln) and not _SQUIGGLE.match(ln))
    return bool(n % 2)


def test_no_odd_fence_counts(rows):
    bad = [(s, r["chunk_id"]) for s, r in rows if _has_orphan_fence(r["text"])]
    assert not bad, f"{len(bad)} chunk(s) with an orphan fence, e.g. {bad[:5]}"


def test_chunk_ids_unique_across_all_files(rows):
    seen, dup = {}, []
    for s, r in rows:
        cid = r["chunk_id"]
        if cid in seen:
            dup.append((cid, seen[cid], s))
        seen[cid] = s
    assert not dup, dup[:5]


def test_reference_only_sources_have_no_chunks(rows):
    ref = {s.name for s in load_registry(ROOT / "data" / "sources.yaml") if s.reference_only}
    assert ref, "registry should contain reference_only sources"
    bad = sorted({s for s, _ in rows if s in ref})
    assert not bad, f"reference_only sources with chunks: {bad}"
    assert not [f.stem for f in FILES if f.stem in ref]


def test_every_source_url_is_an_immutable_permalink(rows):
    """URL must pin the version: contain doc_version (commit sha / arXiv vN / dataset revision) or,
    for postgres docs, /docs/<major>/ where <major> comes from doc_version ("PostgreSQL 18.6")."""
    bad = []
    for s, r in rows:
        url, ver = r["source_url"], r.get("doc_version") or ""
        if not ver:
            bad.append((s, r["chunk_id"], "no doc_version"))
        elif s == "postgres-docs":
            major = re.search(r"(\d+)", ver).group(1)
            if f"/docs/{major}/" not in url or "/docs/current/" in url:
                bad.append((s, r["chunk_id"], url))
        elif ver not in url:
            bad.append((s, r["chunk_id"], url))
    assert not bad, f"{len(bad)} non-permalink source_url(s), e.g. {bad[:3]}"


def test_permalink_rewrite_unit():
    from rag.loaders.crawler_loader import apply_permalink

    cfg = {"permalink": {"match": "/docs/current/", "replace": "/docs/{major}/"}}
    assert (
        apply_permalink("https://www.postgresql.org/docs/current/mvcc.html", cfg, "PostgreSQL 18.6")
        == "https://www.postgresql.org/docs/18/mvcc.html"
    )
    assert (
        apply_permalink("https://x/docs/current/a.html", cfg, "unknown")
        == "https://x/docs/current/a.html"
    )
    assert apply_permalink("https://x/a.html", {}, "PostgreSQL 18.6") == "https://x/a.html"
