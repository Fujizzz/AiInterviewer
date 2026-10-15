"""Wikipedia concept-article loader (MediaWiki Action API, plain-text extracts).

Used for short, definitional articles (CAP theorem, k-means clustering, infrastructure as
code, ...). Interview queries are often a bare concept name or a plain-language paraphrase of one,
and an encyclopedia lead paragraph is the best match for both. Content is CC BY-SA 4.0.

Per request: up to 20 titles (`prop=extracts|revisions`, `explaintext`), >= 1 s between requests,
descriptive User-Agent (Wikimedia API etiquette). Each page becomes one RawDoc whose
`doc_version` is the revision id and whose `source_url` is the matching `oldid` permalink, so
every chunk URL pins the exact revision that was ingested.

`options`: `titles` (list of article titles, required), `skip_sections` (extra section titles to
drop), `max_words` (truncate very long articles, default 7000).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

from ..registry import Source
from .base import FetchResult, RawDoc

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = (
    "IntelliHireRAGBot/0.1 (NUS-ISS coursework knowledge base, non-commercial; "
    "contact: duyingzeng@gmail.com) python-requests"
)
RATE_LIMIT_SECONDS = 1.0
BATCH = 20
DEFAULT_MAX_WORDS = 7000

SKIP_SECTIONS = {
    "see also",
    "references",
    "external links",
    "further reading",
    "notes",
    "citations",
    "bibliography",
    "sources",
    "footnotes",
    "explanatory notes",
    "works cited",
    "gallery",
    "in popular culture",
    "in fiction",
    "awards",
    "awards and honors",
    "notable people",
    "history of the page",
    "implementations",
    "software",
    "list of implementations",
    "example code",
}
_HEADING_RE = re.compile(r"^(={2,6})[ \t]*(.+?)[ \t]*\1[ \t]*$")
_last_request = [0.0]


def _rate_limit() -> None:
    wait = RATE_LIMIT_SECONDS - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.time()


def _strip_displaystyle(text: str) -> str:
    """Plain-text extracts render every formula twice: the MathML text tokens (one per line, lots
    of blank lines) followed by the TeX as `{\\displaystyle ...}`. Keep the TeX as inline `$...$`,
    drop the token lines in front of it and re-join the sentence around it."""
    marker = "{\\displaystyle"
    out: list[str] = []
    i = 0
    n = len(text)
    while True:
        j = text.find(marker, i)
        if j == -1:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k, depth = j, 0
        while k < n:
            c = text[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        body = re.sub(r"\s+", " ", text[j + len(marker) : k]).strip()
        # drop the MathML token lines (blank / short single tokens) directly before the TeX
        buf = "".join(out)
        lines = buf.split("\n")
        while len(lines) > 1:
            last = lines[-1].strip()
            if last == "" or (len(last) <= 12 and " " not in last and not last.startswith("=")):
                lines.pop()
            else:
                break
        buf = "\n".join(lines).rstrip(" \t")
        out = [buf]
        if buf and not buf.endswith((" ", "\n", "(", "[")):
            out.append(" ")
        out.append(f"${body}$" if body else "")
        i = k + 1
        # swallow the whitespace / blank lines after the formula; keep sentences running on
        m = re.match(r"[ \t]*(?:\n[ \t]*)*", text[i:])
        ws_end = i + (m.end() if m else 0)
        nxt = text[ws_end : ws_end + 1]
        if ws_end > i:
            i = ws_end
            if nxt and nxt not in ",.;:)]'\"-" and not nxt.isupper():
                out.append(" ")
            elif nxt and nxt.isupper():
                out.append("\n")
    return "".join(out)


def extract_to_markdown(
    title: str, extract: str, skip: set[str] | None = None, max_words: int = DEFAULT_MAX_WORDS
) -> str:
    """Plain-text extract with `== Heading ==` markers -> markdown with boilerplate sections
    (references, external links, ...) removed. Pure function: unit-tested without network."""
    skip_set = {s.lower() for s in (skip or set())} | SKIP_SECTIONS
    extract = _strip_displaystyle(extract.replace("\r\n", "\n"))
    out: list[str] = [f"# {title}", ""]
    skip_level = 0
    words = 0
    for ln in extract.split("\n"):
        m = _HEADING_RE.match(ln.strip())
        if m:
            level = len(m.group(1))
            name = m.group(2).strip()
            if skip_level and level <= skip_level:
                skip_level = 0
            if not skip_level and name.lower() in skip_set:
                skip_level = level
                continue
            if skip_level:
                continue
            out.extend(["", "#" * level + " " + name, ""])
            continue
        if skip_level:
            continue
        stripped = ln.rstrip()
        words += len(stripped.split())
        if words > max_words:
            break
        out.append(stripped)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"
    return text


def _get_batch(titles: list[str], session: requests.Session) -> dict:
    """One title batch, following the API's `continue` token: full-text extracts are returned a
    few pages at a time, so a 20-title request needs several round trips."""
    pages: dict[str, dict] = {}
    cont: dict = {}
    for _ in range(60):  # hard bound on continuations
        _rate_limit()
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "extracts|revisions",
            "explaintext": "1",
            "exlimit": "max",
            "rvprop": "ids",
            "redirects": "1",
            "titles": "|".join(titles),
            **cont,
        }
        resp = session.get(API, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        for p in data.get("query", {}).get("pages", []):
            cur = pages.setdefault(p.get("title", ""), {})
            for k, v in p.items():
                if v not in (None, "", []) or k not in cur:
                    cur[k] = v
        if "continue" not in data:
            break
        cont = data["continue"]
    return {"query": {"pages": list(pages.values())}}


def parse_response(
    payload: dict, skip: set[str] | None, max_words: int
) -> tuple[list[dict], list[str]]:
    """Returns ([{title, revid, text}], [missing titles])."""
    pages, missing = [], []
    for p in payload.get("query", {}).get("pages", []):
        title = p.get("title", "")
        if p.get("missing") or not p.get("extract"):
            missing.append(title)
            continue
        revs = p.get("revisions") or []
        revid = revs[0].get("revid") if revs else None
        if not revid:
            missing.append(title)
            continue
        text = extract_to_markdown(title, p["extract"], skip, max_words)
        if len(text.split()) < 40:  # disambiguation / stub
            missing.append(f"{title} (too short)")
            continue
        pages.append({"title": title, "revid": int(revid), "text": text})
    return pages, missing


def fetch(source: Source, raw_dir: Path) -> FetchResult:
    opts = source.options or {}
    titles: list[str] = list(dict.fromkeys(opts.get("titles") or []))
    if not titles:
        reason = f"wikipedia source '{source.name}' has no options.titles"
        print(f"[fetch] {reason}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=reason)
    skip = set(opts.get("skip_sections") or [])
    max_words = int(opts.get("max_words") or DEFAULT_MAX_WORDS)
    cache_dir = raw_dir / source.name
    cache_dir.mkdir(parents=True, exist_ok=True)

    docs: list[RawDoc] = []
    errors: list[str] = []
    notes: list[str] = []
    session = requests.Session()
    seen: set[str] = set()
    n_missing = 0
    for i in range(0, len(titles), BATCH):
        batch = titles[i : i + BATCH]
        cache = cache_dir / f"batch-{i // BATCH:03d}.json"
        try:
            if cache.exists() and json.loads(cache.read_text()).get("_titles") == batch:
                payload = json.loads(cache.read_text())
            else:
                payload = _get_batch(batch, session)
                payload["_titles"] = batch
                cache.write_text(json.dumps(payload, ensure_ascii=False))
        except Exception as e:  # network / API error: report the batch, keep going
            errors.append(f"batch {i // BATCH}: {type(e).__name__}: {e}")
            continue
        pages, missing = parse_response(payload, skip, max_words)
        n_missing += len(missing)
        for m in missing:
            errors.append(f"missing or too short: {m}")
        for p in pages:
            if p["title"] in seen:  # redirects can map two requested titles to one page
                continue
            seen.add(p["title"])
            slug = quote(p["title"].replace(" ", "_"))
            url = f"https://en.wikipedia.org/w/index.php?title={slug}&oldid={p['revid']}"
            docs.append(
                RawDoc(
                    path=p["title"].replace(" ", "_"),
                    text=p["text"],
                    doc_version=str(p["revid"]),
                    source_url=url,
                    title=p["title"],
                )
            )
    notes.append(
        f"wikipedia: {len(docs)} article(s) fetched of {len(titles)} requested, "
        f"{n_missing} missing/too short"
    )
    print(f"[fetch] {source.name}: {len(docs)} articles ({n_missing} missing)")
    return FetchResult(source_name=source.name, docs=docs, errors=errors, notes=notes)
