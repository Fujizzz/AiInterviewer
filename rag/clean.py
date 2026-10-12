"""Markdown/RST cleanup: frontmatter, HTML comments, MDX/JSX noise, nav
boilerplate, and exact-duplicate removal.

Deliberately simple regex-based cleanup (no markdown AST parser) — good
enough for stripping machine-generated boilerplate without touching the
actual heading structure the chunker depends on.

IMPORTANT: every cleanup step runs with fenced code blocks (``` / ~~~)
replaced by opaque placeholders first (`_protect_fences` / `_restore_fences`),
so nothing in this module can ever rewrite text *inside* a code fence —
this is what stops a shell heredoc (`cat <<EOF`) or any other `<...>`-shaped
text inside a code sample from being misread as an HTML/JSX tag.
"""

from __future__ import annotations

import re

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
_FRONTMATTER_TOML_RE = re.compile(r"\A\+\+\+\s*\n.*?\n\+\+\+\s*\n", re.S)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# Block-level only (tag at the start of a line): prose that merely *mentions*
# `<script>` (e.g. Django's json_script docs) must not be matched, and a match
# may never swallow a protected fence placeholder or span an absurd length.
_STYLE_SCRIPT_RE = re.compile(r"^[ \t]*<(style|script)\b[^>]*>.*?</\1>[ \t]*$", re.S | re.I | re.M)
_MAX_BLOCK_STRIP_CHARS = 6000


def _guarded_strip(rx: re.Pattern, text: str, what: str) -> str:
    def _sub(m: re.Match) -> str:
        if "\x00" in m.group(0) or len(m.group(0)) > _MAX_BLOCK_STRIP_CHARS:
            print(f"[clean] WARNING skipping suspicious {what} match ({len(m.group(0))} chars)")
            return m.group(0)
        return ""

    return rx.sub(_sub, text)


# MDX/JSX: import/export statements, and common component wrapper tags
# (kept generic — we strip the tags, not the text they wrap, since the
# text inside e.g. <Tabs>/<Note> is usually real content).
#
# `[^>\n]*` forbids newlines in the "attributes" portion, so a match can
# never span more than one line — the original bug was `[^>]*` (allowing
# newlines) greedily eating everything up to the *next* unrelated `>`
# anywhere later in the file. `(?<!<)` stops this from matching the second
# `<` of a shell heredoc like `cat <<EOF` (which would otherwise look like
# `<EOF ...>` to this regex).
_MDX_IMPORT_EXPORT_RE = re.compile(r"^\s*(import|export)\s+.*$", re.M)
_JSX_SELF_CLOSING_RE = re.compile(r"(?<!<)<([A-Z][A-Za-z0-9]*)\s*/>")
_JSX_TAG_RE = re.compile(r"(?<!<)</?([A-Z][A-Za-z0-9]*)(\s[^>\n]*)?>")

# Any single JSX-tag match that would delete more than this many characters
# is treated as suspicious (most likely a false positive, e.g. a `<` that
# isn't really a tag) and is left untouched instead of being stripped.
_MAX_JSX_STRIP_CHARS = 200

# Common doc-site nav/boilerplate lines that add no information.
_BOILERPLATE_LINE_PATTERNS = [
    re.compile(r"^\s*(edit this page|edit on github|was this page helpful\??)\s*$", re.I),
    re.compile(r"^\s*(previous|next)\s*(\||$)", re.I),
    re.compile(r"^\s*\[\s*(edit|improve) this (page|doc)[^\]]*\]\([^)]*\)\s*$", re.I),
    re.compile(r"^\s*table of contents\s*$", re.I),
    re.compile(r"^\s*#\s*table of contents\s*$", re.I),
]

_FENCE_LINE_RE = re.compile(r"^(\s*)(```|~~~)")
_FENCE_LINE_COUNT_RE = re.compile(r"^[ \t]*(```|~~~)", re.M)
_FENCE_PLACEHOLDER_TMPL = "\x00FENCE_BLOCK_{0}\x00"
_FENCE_PLACEHOLDER_RE = re.compile(r"\x00FENCE_BLOCK_(\d+)\x00")


# ---------------------------------------------------------------------------
# fence protection — nothing else in this module may touch fence contents
# ---------------------------------------------------------------------------


def protect_fences(text: str) -> tuple[str, dict[str, str]]:
    """Replaces every fenced code block with an opaque one-line placeholder.
    Returns (text_with_placeholders, {placeholder: original_fence_text})."""
    lines = text.split("\n")
    out_lines: list[str] = []
    placeholders: dict[str, str] = {}
    i, n, counter = 0, len(lines), 0

    while i < n:
        if _FENCE_LINE_RE.match(lines[i]):
            fence_lines = [lines[i]]
            i += 1
            while i < n and not _FENCE_LINE_RE.match(lines[i]):
                fence_lines.append(lines[i])
                i += 1
            if i < n:
                fence_lines.append(lines[i])  # closing fence
                i += 1
            token = _FENCE_PLACEHOLDER_TMPL.format(counter)
            placeholders[token] = "\n".join(fence_lines)
            out_lines.append(token)
            counter += 1
        else:
            out_lines.append(lines[i])
            i += 1

    return "\n".join(out_lines), placeholders


def restore_fences(text: str, placeholders: dict[str, str]) -> str:
    def _sub(m: re.Match) -> str:
        token = m.group(0)
        return placeholders.get(token, token)

    return _FENCE_PLACEHOLDER_RE.sub(_sub, text)


def count_fences(text: str) -> int:
    """Counts fence-marker lines (``` or ~~~). Used as a cheap sanity check
    that cleaning never silently deletes part of a code block: this count
    must be identical before and after `clean_markdown`."""
    return len(_FENCE_LINE_COUNT_RE.findall(text))


# ---------------------------------------------------------------------------
# individual cleanup passes (assume fences are already protected)
# ---------------------------------------------------------------------------


def strip_frontmatter(text: str) -> str:
    text = _FRONTMATTER_RE.sub("", text, count=1)
    text = _FRONTMATTER_TOML_RE.sub("", text, count=1)
    return text


def strip_html_comments(text: str) -> str:
    return _guarded_strip(_HTML_COMMENT_RE, text, "HTML comment")


def strip_style_script(text: str) -> str:
    return _guarded_strip(_STYLE_SCRIPT_RE, text, "<style>/<script> block")


def strip_mdx_jsx(text: str, source_name: str = "") -> str:
    text = _MDX_IMPORT_EXPORT_RE.sub("", text)
    text = _JSX_SELF_CLOSING_RE.sub("", text)

    def _sub(m: re.Match) -> str:
        matched = m.group(0)
        if len(matched) > _MAX_JSX_STRIP_CHARS:
            label = f" in {source_name}" if source_name else ""
            print(
                f"[clean] WARNING skipping suspicious JSX/HTML-tag-shaped match{label} "
                f"({len(matched)} chars, > {_MAX_JSX_STRIP_CHARS} cap): {matched[:80]!r}..."
            )
            return matched
        return ""

    return _JSX_TAG_RE.sub(_sub, text)


def strip_boilerplate_lines(text: str) -> str:
    lines = text.split("\n")
    out = [ln for ln in lines if not any(p.match(ln) for p in _BOILERPLATE_LINE_PATTERNS)]
    return "\n".join(out)


_TABLE_ROW_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def normalize_tables(text: str) -> str:
    """Collapses the alignment padding of pipe-table rows (runs of spaces,
    `-----` separators) — padded wide tables otherwise waste >50% of their
    tokens on whitespace."""
    out = []
    for ln in text.split("\n"):
        if _TABLE_ROW_LINE_RE.match(ln):
            ln = re.sub(r"[ \t]{2,}", " ", ln.strip())
            if re.fullmatch(r"\|(?:\s*:?-{3,}:?\s*\|)+", ln):  # alignment row
                cells = ln.strip("|").split("|")
                ln = (
                    "|"
                    + "|".join(
                        " --- "
                        if ":" not in c
                        else " " + c.strip().replace("-" * c.strip().count("-"), "---") + " "
                        for c in cells
                    )
                    + "|"
                )
        out.append(ln)
    return "\n".join(out)


_HEADING_ID_RE = re.compile(
    r"^([ \t]*#{1,6}[ \t]+.*?)[ \t]*\{#[^}\s]+\}[ \t]*$", re.M
)  # indented headings too (inside admonitions / content tabs)


def strip_heading_ids(text: str) -> str:
    """`## dsl.store.format {#dsl-store-format}` -> `## dsl.store.format` (Hugo/kramdown id suffix,
    no-space form). Must run before chunking so titles / heading_paths never carry it."""
    return _HEADING_ID_RE.sub(r"\1", text)


_ENTITY_RE = re.compile(r"&(?:#x[0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]{1,9});")
_ENTITY_PAIR_RE = re.compile(r"&lt;([^&\n<>`]{1,40}?)&gt;")
_TABLE_SAFE_PIPES = {
    "&#124;",
    "&#x7c;",
    "&#x7C;",
    "&vert;",
    "&verbar;",
}  # must stay escaped inside pipe tables


def unescape_entities(text: str) -> str:
    """Numeric/named HTML entities -> characters, in prose only (callers pass text with fences
    already protected; inline code is protected here). `&lt;x&gt;` becomes `` `<x>` `` so the
    result is never mistaken for a raw HTML tag; entity-encoded pipes stay escaped."""
    from .htmlmd import _protect_inline_code, _restore_inline_code

    text, ph = _protect_inline_code(text)
    text = _ENTITY_PAIR_RE.sub(lambda m: f"`<{m.group(1)}>`", text)

    def _one(m: re.Match) -> str:
        full = m.group(0)
        if full in _TABLE_SAFE_PIPES:
            return full
        import html as _html

        u = _html.unescape(full)
        if u == full:
            return full
        return " " if u.isspace() else u

    text = _ENTITY_RE.sub(_one, text)
    return _restore_inline_code(text, ph)


def dedupe_consecutive_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def dedupe_paragraphs(text: str) -> str:
    """Drop exact-duplicate consecutive paragraphs (common in generated docs
    with repeated 'see also' blocks)."""
    paragraphs = re.split(r"\n\s*\n", text)
    out: list[str] = []
    prev_norm = None
    for p in paragraphs:
        norm = p.strip()
        if norm and norm == prev_norm:
            continue
        out.append(p)
        prev_norm = norm
    return "\n\n".join(out)


def clean_markdown(text: str, source_name: str = "") -> str:
    """Generic cleaner. Profile-specific preprocessing (rst/d2l/hugo/...) is
    applied *before* this by `rag.profiles.apply_profile`."""
    protected, placeholders = protect_fences(text)
    protected = strip_frontmatter(protected)
    protected = strip_html_comments(protected)
    protected = strip_style_script(protected)
    protected = strip_mdx_jsx(protected, source_name=source_name)
    protected = strip_boilerplate_lines(protected)
    protected = strip_heading_ids(protected)
    protected = unescape_entities(protected)
    protected = normalize_tables(protected)
    protected = dedupe_paragraphs(protected)
    protected = dedupe_consecutive_blank_lines(protected)
    restored = restore_fences(protected, placeholders)
    return restored.strip()


def normalize_for_dedupe(text: str) -> str:
    """Normalized form used to detect near-identical chunks across a source
    (e.g. the same snippet repeated across i18n copies that slipped through
    path filtering)."""
    collapsed = re.sub(r"\s+", " ", text).strip().lower()
    return collapsed


def dedupe_chunks(chunk_texts: list[str]) -> list[bool]:
    """Returns a keep/drop boolean mask (True = keep) for a list of chunk
    text bodies, dropping exact normalized duplicates after the first."""
    seen = set()
    keep = []
    for t in chunk_texts:
        norm = normalize_for_dedupe(t)
        if norm in seen:
            keep.append(False)
        else:
            seen.add(norm)
            keep.append(True)
    return keep


# ---------------------------------------------------------------------------
# post-chunk junk filter: doc-tool directives / nav-index boilerplate that
# survived as their own chunk (mkdocstrings `::: module.path` stubs, bare
# Jinja `{% include %}` directives, leftover empty style/script wrappers)
# ---------------------------------------------------------------------------

_MKDOCSTRINGS_LINE_RE = re.compile(r"^\s*:::\s*\S+\s*$")
_MKDOCSTRINGS_OPTION_LINE_RE = re.compile(r"^\s+([\w_]+:\s*.*|-\s+\S+)\s*$")
_JINJA_DIRECTIVE_LINE_RE = re.compile(r"^\s*\{[%{]-?\s*\S.*?[%}]-?\s*$")
_SNIPPET_MARKER_LINE_RE = re.compile(r"^\s*(?:#|//)?\s*--8<--\s*\[(?:start|end):[^\]]*\]\s*$")
_OMISSION_NOTE_LINE_RE = re.compile(
    r"^\s*\((?:full content omitted for size|snippet [^)]*|generated table/listing "
    r"omitted)[^\n]*\)\s*$"
)
_BARE_STYLE_SCRIPT_TAG_RE = re.compile(r"^\s*</?(style|script)\b[^>]*>\s*$", re.I)

# mkdocstrings reference stubs are junk even when they carry one short
# "here's what this module is" sentence (that sentence isn't retrievable
# content on its own) — only a real paragraph's worth of prose rescues them.
_MKDOCSTRINGS_PROSE_RESCUE_CHARS = 150
# Any other directive-only chunk (Jinja include, bare style/script tag) has
# no legitimate reason to carry any remaining prose at all.
_GENERIC_JUNK_RESCUE_CHARS = 10


MIN_TECHNICAL_TOKENS = 20
CODE_DUMP_MIN_TOKENS = 1000


def junk_category(
    text: str, token_count: int | None = None, namespace: str | None = None
) -> str | None:
    """Returns a short category name if the chunk is junk, else None."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return "empty"
    if namespace == "technical" and token_count is not None and token_count < MIN_TECHNICAL_TOKENS:
        return "too-short"
    if (token_count or 99) < 40 and lines[-1].rstrip().endswith(":") and "```" not in text:
        # e.g. "Below is the producer configuration:" whose generated table is not in the repo
        return "dangling-intro"
    if (
        re.search(r"Redirecting in \d+ seconds|has been deprecated\. There is a new tutorial", text)
        and (token_count or 0) < 120
    ):
        return "redirect-stub"
    if token_count is not None and token_count >= CODE_DUMP_MIN_TOKENS and _is_code_data_dump(text):
        return "code-data-dump"

    has_mkdocstrings = False
    non_directive: list[str] = []
    for ln in lines:
        if _MKDOCSTRINGS_LINE_RE.match(ln):
            has_mkdocstrings = True
            continue
        if has_mkdocstrings and _MKDOCSTRINGS_OPTION_LINE_RE.match(ln):
            continue
        if _JINJA_DIRECTIVE_LINE_RE.match(ln):
            continue
        if (
            _BARE_STYLE_SCRIPT_TAG_RE.match(ln)
            or _SNIPPET_MARKER_LINE_RE.match(ln)
            or _OMISSION_NOTE_LINE_RE.match(ln)
        ):
            continue
        non_directive.append(ln)

    remaining = "\n".join(non_directive).strip()
    rescue_threshold = (
        _MKDOCSTRINGS_PROSE_RESCUE_CHARS if has_mkdocstrings else _GENERIC_JUNK_RESCUE_CHARS
    )
    if len(remaining) < rescue_threshold:
        return "doc-tool-directive-only"

    if namespace == "question":
        return None  # link/name lists of practice questions are legitimate content there
    return _extra_junk_category(lines)


def is_junk_text(text: str) -> bool:
    return junk_category(text) is not None


def _is_code_data_dump(text: str) -> bool:
    """A chunk that is one big fenced block of (mostly) numbers/punctuation —
    palettes, embeddings, label maps: no retrievable meaning."""
    stripped = text.strip()
    if not (_FENCE_LINE_RE.match(stripped.split("\n", 1)[0]) and stripped.endswith(("```", "~~~"))):
        return False
    body = "\n".join(stripped.split("\n")[1:-1])
    if not body:
        return False
    alpha = sum(1 for ch in body if ch.isalpha())
    return alpha / max(len(body), 1) < 0.25


# --- link-index / toctree-style pages: (almost) every line is a bare link or
# a link-only bullet, with no prose around them -------------------------------
_LINK_ONLY_LINE_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*(?:\*\*)?\[[^\]]+\]\([^)]*\)(?:\*\*)?\s*(?:[-:–—].{0,80})?$"
)
_HEADING_ONLY_RE = re.compile(r"^\s*#{1,6}\s+\S")
_FIGURE_ONLY_RE = re.compile(
    r"^\s*(\*?\[(?:figure|image)[^\]]*\]\*?|!\[[^\]]*\]\([^)]*\))\s*$", re.I
)
_BARE_BULLET_NAME_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+`?[\w./:-]{1,60}`?\s*$")


_LINK_MD_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
DESCRIPTIVE_WORDS = 6  # a link item with this many words of text outside the link is "descriptive"
PROSE_SENTENCE_WORDS = 12  # a non-list line with this many words is a real sentence


def _descriptive_text_words(line: str) -> int:
    rest = _LINK_MD_RE.sub(" ", line)
    rest = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", rest)
    return len(re.findall(r"[A-Za-z]{2,}", rest))


def _has_prose_sentence(lines: list[str]) -> bool:
    for ln in lines:
        if re.match(r"^\s*(?:[-*+]|\d+[.)]|#|\||`{3})", ln):
            continue
        if len(re.findall(r"[A-Za-z][A-Za-z'’-]*", ln)) >= PROSE_SENTENCE_WORDS:
            return True
    return False


def _extra_junk_category(lines: list[str]) -> str | None:
    body = [ln for ln in lines if not _HEADING_ONLY_RE.match(ln)]
    if not body:
        return "heading-only"
    # link items that carry descriptive text are real content (e.g. "[X](url) - what it does and
    # when to use it")
    descriptive = sum(
        1
        for ln in body
        if _LINK_MD_RE.search(ln) and _descriptive_text_words(ln) >= DESCRIPTIVE_WORDS
    )
    link_only = sum(
        1
        for ln in body
        if _LINK_ONLY_LINE_RE.match(ln) and _descriptive_text_words(ln) < DESCRIPTIVE_WORDS
    )
    has_prose = _has_prose_sentence(
        body
    )  # an intro sentence of >= 12 words makes the list part of real content
    if len(body) >= 3 and link_only / len(body) >= 0.8 and descriptive == 0 and not has_prose:
        return "link-index"
    # nested sitemap/nav trees: links plus bare category bullets ("- Algebra")
    nav_like = link_only + sum(
        1
        for ln in body
        if _BARE_BULLET_NAME_RE.match(ln) or re.match(r"^\s*(?:[-*+])\s+[A-Z][\w &/,.-]{0,50}$", ln)
    )
    if (
        len(body) >= 6
        and link_only >= 3
        and nav_like / len(body) >= 0.9
        and descriptive == 0
        and not has_prose
    ):
        return "link-index"
    if all(_FIGURE_ONLY_RE.match(ln) for ln in body):
        return "figure-only"
    # a name list that sits next to a real prose sentence is content (config lists with intro text)
    if (
        len(body) >= 3
        and sum(1 for ln in body if _BARE_BULLET_NAME_RE.match(ln)) / len(body) >= 0.9
        and not has_prose
    ):
        return "bare-name-list"
    return None
