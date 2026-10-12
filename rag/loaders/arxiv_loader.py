"""arXiv paper loader.

Fetches metadata via the arXiv API, then the LaTeXML-rendered HTML version
(arxiv.org/html/<id>, falling back to ar5iv.labs.arxiv.org/html/<id>),
extracts section text, and yields one RawDoc per top-level/sub section.
Rate-limited to >= RATE_LIMIT_SECONDS between any two requests, with a
descriptive User-Agent, per arXiv's usage guidance.
"""

from __future__ import annotations

import html as html_lib
import re
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from ..registry import Source
from .base import FetchResult, RawDoc

USER_AGENT = (
    "IntelliHireRAGBot/0.1 (NUS-ISS coursework data pipeline; contact: duyingzeng@gmail.com)"
)
ARXIV_API = "https://export.arxiv.org/api/query"
RATE_LIMIT_SECONDS = 3.0

_last_request_time = [0.0]


def _rate_limit() -> None:
    elapsed = time.time() - _last_request_time[0]
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_request_time[0] = time.time()


def _arxiv_id_from_url(url: str) -> str:
    m = re.search(r"(\d{4}\.\d{4,5})", url or "")
    if not m:
        raise ValueError(f"could not parse arXiv id from url: {url!r}")
    return m.group(1)


def _fetch_metadata(arxiv_id: str) -> dict:
    _rate_limit()
    resp = requests.get(
        ARXIV_API,
        params={"id_list": arxiv_id},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(resp.text)
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise ValueError(f"no arXiv entry found for id {arxiv_id}")
    title_el = entry.find("atom:title", ns)
    id_el = entry.find("atom:id", ns)
    title = (title_el.text or "").strip() if title_el is not None else arxiv_id
    id_url = (id_el.text or "").strip() if id_el is not None else ""
    version_match = re.search(r"v(\d+)$", id_url)
    version = f"v{version_match.group(1)}" if version_match else "v1"
    return {"title": title, "version": version}


def _fetch_html(arxiv_id: str) -> tuple[str, str]:
    """Returns (html, which_source) trying arxiv.org/html then ar5iv."""
    _rate_limit()
    primary = f"https://arxiv.org/html/{arxiv_id}"
    resp = requests.get(primary, headers={"User-Agent": USER_AGENT}, timeout=30)
    if resp.status_code == 200 and len(resp.text) > 500:
        return resp.text, primary

    _rate_limit()
    fallback = f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
    resp = requests.get(fallback, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text, fallback


def fetch(source: Source, raw_dir: Path) -> FetchResult:
    errors: list[str] = []
    try:
        arxiv_id = _arxiv_id_from_url(source.url)
    except Exception as e:
        print(f"[fetch] ERROR {source.name}: {e}")
        return FetchResult(
            source_name=source.name, skipped=True, skip_reason=str(e), errors=[str(e)]
        )

    out_dir = raw_dir / source.name
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        meta = _fetch_metadata(arxiv_id)
    except Exception as e:
        msg = f"metadata fetch failed: {e}"
        print(f"[fetch] ERROR {source.name}: {msg}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=msg, errors=[msg])

    try:
        html, used_source = _fetch_html(arxiv_id)
    except Exception as e:
        msg = f"html fetch failed (tried arxiv.org/html then ar5iv): {e}"
        print(f"[fetch] ERROR {source.name}: {msg}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=msg, errors=[msg])

    (out_dir / f"{arxiv_id}.html").write_text(html, encoding="utf-8")

    sections = _extract_sections(html, meta["title"])
    permalink = f"https://arxiv.org/abs/{arxiv_id}{meta['version']}"

    docs: list[RawDoc] = []
    for i, (heading, text) in enumerate(sections):
        if not text.strip():
            continue
        docs.append(
            RawDoc(
                path=f"{arxiv_id}/section-{i:02d}",
                text=text,
                doc_version=meta["version"],
                source_url=permalink,
                title=meta["title"],
                extra={"heading": heading, "html_source": used_source},
            )
        )

    if not docs:
        errors.append("HTML fetched but no sections extracted (layout unexpected)")

    return FetchResult(source_name=source.name, docs=docs, errors=errors)


# ---------------------------------------------------------------------------
# HTML -> text extraction (regex-based; no bs4 dependency needed for this
# fairly regular LaTeXML output)
# ---------------------------------------------------------------------------

_MATH_RE = re.compile(r"<math\b[^>]*>(.*?)</math>", re.S | re.I)
_ANNOTATION_RE = re.compile(
    r"<annotation[^>]*encoding=\"application/x-tex\"[^>]*>(.*?)</annotation>", re.S | re.I
)
# group(1)=level, group(2)=opening-tag attributes (used to detect the
# ltx_title_bibliography class), group(3)=heading inner HTML.
_HEADING_RE = re.compile(r"<h([1-4])\b([^>]*)>(.*?)</h\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n+")

# LaTeXML wraps the actual paper body in <article class="ltx_document ...">.
# Everything outside of it (site chrome, the "Instructions for reporting
# errors" footer, feedback widgets, etc.) is not paper content and must
# never be extracted as a section.
_ARTICLE_RE = re.compile(r"<article\b[^>]*>(.*)</article>", re.S | re.I)

# Sections that are real HTML content but not useful/retrievable RAG
# material: the bibliography (dozens of bibitem entries with no prose) and
# acknowledgments (boilerplate thanks, no technical content). Matched
# against the heading's own `class` attribute first (most reliable), then
# against its visible text as a fallback. Appendices are deliberately NOT
# excluded — they're often genuinely useful technical content.
_EXCLUDED_HEADING_CLASS_RE = re.compile(r"ltx_title_bibliography", re.I)
_EXCLUDED_HEADING_TEXT_RE = re.compile(r"^\s*(references?|bibliography|acknowledge?ments?)\b", re.I)


def _replace_math_with_tex(fragment: str) -> str:
    def _sub(m: re.Match) -> str:
        inner = m.group(1)
        ann = _ANNOTATION_RE.search(inner)
        if ann:
            return f" ${html_lib.unescape(ann.group(1).strip())}$ "
        return " "

    return _MATH_RE.sub(_sub, fragment)


def _html_to_text(fragment: str) -> str:
    fragment = _replace_math_with_tex(fragment)
    fragment = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", fragment, flags=re.S | re.I)
    text = _TAG_RE.sub(" ", fragment)
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


def _extract_sections(html: str, paper_title: str) -> list[tuple[str, str]]:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)

    article_match = _ARTICLE_RE.search(html)
    if article_match:
        body_html = article_match.group(1)
    else:
        # Unexpected layout (e.g. ar5iv fallback might differ) — fall back
        # to the whole page rather than silently returning nothing, but this
        # means footer/chrome exclusion below is best-effort only in that case.
        body_html = html

    matches = list(_HEADING_RE.finditer(body_html))
    if not matches:
        return [(paper_title, _html_to_text(body_html))]

    sections: list[tuple[str, str]] = []
    for idx, m in enumerate(matches):
        heading_attrs = m.group(2) or ""
        heading_text = _html_to_text(m.group(3)).strip() or f"section-{idx}"
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body_html)

        if _EXCLUDED_HEADING_CLASS_RE.search(heading_attrs) or _EXCLUDED_HEADING_TEXT_RE.match(
            heading_text
        ):
            continue

        body_text = _html_to_text(body_html[start:end])
        sections.append((heading_text, body_text))
    return sections
