"""HTML -> markdown (stdlib only).

Used by the generic crawler loader (whole pages) and by the `hugo`/`html`
cleaning profiles (HTML fragments embedded in markdown, e.g. Kafka's docs).

Design: a small tolerant DOM built on `html.parser`, a minimal selector
engine (`tag`, `#id`, `.class`, `tag.class`, `tag#id`, comma lists) used to
pick the main content container and strip nav/sidebar/footer nodes, and a
renderer that preserves headings, code blocks, lists and tables.
"""

from __future__ import annotations

import html as html_lib
import re
import textwrap
from collections.abc import Iterable
from html.parser import HTMLParser

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
DEFAULT_STRIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "head",
    "form",
    "button",
    "select",
}
# tags the *embedded fragment* converter is allowed to touch (everything else,
# e.g. Java generics like <String> or <K,V> in prose, must be left alone).
KNOWN_HTML_TAGS = {
    "a",
    "abbr",
    "acronym",
    "b",
    "blockquote",
    "br",
    "caption",
    "cite",
    "code",
    "dd",
    "del",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "tt",
    "u",
    "ul",
    "kbd",
    "samp",
    "var",
    "small",
    "center",
    "figure",
    "figcaption",
    "details",
    "summary",
    "section",
    "article",
    "picture",
    "video",
    "source",
}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: Node | None = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node | str] = []
        self.parent = parent

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def iter_nodes(self) -> Iterable[Node]:
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.iter_nodes()

    def text(self) -> str:
        out: list[str] = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text())
        return "".join(out)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("[document]")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        # implicit closes for the common optional-end-tag cases
        if tag in ("li", "dt", "dd", "tr", "td", "th", "p"):
            self._implicit_close(tag)
            node.parent = self.cur
        self.cur.children.append(node)
        if tag not in VOID_TAGS:
            self.cur = node

    def _implicit_close(self, tag: str) -> None:
        closes = {
            "li": {"li"},
            "dt": {"dt", "dd"},
            "dd": {"dt", "dd"},
            "tr": {"tr", "td", "th"},
            "td": {"td", "th"},
            "th": {"td", "th"},
            "p": {"p"},
        }[tag]
        n = self.cur
        while n is not None and n.tag != "[document]":
            if n.tag in closes:
                self.cur = n.parent or self.root
                return
            if n.tag in (
                "ul",
                "ol",
                "table",
                "dl",
                "div",
                "body",
                "section",
                "article",
                "blockquote",
            ):
                return  # do not climb out of a container
            n = n.parent

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != "[document]":
            if n.tag == tag:
                self.cur = n.parent or self.root
                return
            n = n.parent
        # unmatched end tag: ignore

    def handle_data(self, data):
        if data:
            self.cur.children.append(data)


def parse_html(markup: str) -> Node:
    b = _TreeBuilder()
    b.feed(markup)
    b.close()
    return b.root


# ---------------------------------------------------------------------------
# selectors
# ---------------------------------------------------------------------------

_SEL_RE = re.compile(r"^(?P<tag>[a-zA-Z][a-zA-Z0-9]*)?(?P<rest>(?:[#.][\w-]+)*)$")


def _match_simple(node: Node, sel: str) -> bool:
    m = _SEL_RE.match(sel.strip())
    if not m:
        return False
    tag = m.group("tag")
    if tag and node.tag != tag.lower():
        return False
    for kind, val in re.findall(r"([#.])([\w-]+)", m.group("rest") or ""):
        if kind == "#" and node.attrs.get("id") != val:
            return False
        if kind == "." and val not in node.classes:
            return False
    return bool(tag or m.group("rest"))


def matches_selector(node: Node, selectors: Iterable[str]) -> bool:
    return any(_match_simple(node, s) for s in selectors)


def find_first(root: Node, selectors: Iterable[str]) -> Node | None:
    sels = list(selectors)
    for n in root.iter_nodes():
        if matches_selector(n, sels):
            return n
    return None


def strip_nodes(root: Node, selectors: Iterable[str]) -> int:
    sels = list(selectors)
    removed = 0

    def _walk(n: Node) -> None:
        nonlocal removed
        keep: list[Node | str] = []
        for c in n.children:
            if isinstance(c, Node) and (c.tag in DEFAULT_STRIP_TAGS or matches_selector(c, sels)):
                removed += 1
                continue
            if isinstance(c, Node):
                _walk(c)
            keep.append(c)
        n.children = keep

    _walk(root)
    return removed


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

LONG_ROW_CHARS = 1200
# When True, <td>/<th> text is Markdown embedded in HTML (Kafka/Hugo docs): keep its
# newlines, bullets and 4-space code instead of flattening it to one line.
_CTX = {"markdown_cells": False}
_WS = re.compile(r"[ \t\r\n\f\v ]+")
ADMONITION_CLASSES = {
    "note",
    "warning",
    "tip",
    "important",
    "caution",
    "danger",
    "hint",
    "attention",
    "seealso",
}


def _inline(node: Node, keep_links: bool = False) -> str:
    out: list[str] = []
    for c in node.children:
        if isinstance(c, str):
            out.append(_WS.sub(" ", c))
            continue
        t = c.tag
        if t == "br":
            out.append("\n")
        elif t in ("code", "tt", "kbd", "samp", "var") or (
            t == "span" and ("literal" in c.classes or "code" in c.classes)
        ):
            inner = _WS.sub(" ", c.text()).strip()
            out.append(f"`{inner}`" if inner else "")
        elif t == "pre":
            inner = _WS.sub(" ", html_lib.unescape(c.text())).strip()
            out.append(f"`{inner}`" if inner else "")
        elif t in ("em", "i", "cite") or (t == "span" and "emphasis" in c.classes):
            inner = _inline(c, keep_links).strip()
            out.append(f"*{inner}*" if inner else "")
        elif t in ("strong", "b"):
            inner = _inline(c, keep_links).strip()
            out.append(f"**{inner}**" if inner else "")
        elif t == "a":
            inner = _inline(c, keep_links)
            href = c.attrs.get("href", "")
            if keep_links and href and not href.startswith("#") and inner.strip():
                out.append(f"[{inner.strip()}]({href})")
            else:
                out.append(inner)
        elif t == "img":
            alt = c.attrs.get("alt", "").strip()
            out.append(f"[image: {alt}]" if len(alt) > 8 else "")
        elif t in DEFAULT_STRIP_TAGS:
            continue
        else:
            out.append(_inline(c, keep_links))
    return "".join(out)


def _cell_markdown(cell: Node, keep_links: bool) -> str:
    """Cell content as multi-line markdown: text nodes keep their lines, inline tags
    are converted, <pre> becomes a fenced block."""
    parts: list[str] = []
    for c in cell.children:
        if isinstance(c, str):
            parts.append(c.replace("\u00a0", " "))
        elif c.tag == "pre":
            parts.append("\n\n" + _render_pre(c) + "\n\n")
        elif c.tag in ("p", "div", "ul", "ol", "table", "blockquote", "dl"):
            inner = Node("[document]")
            inner.children = [c]
            parts.append("\n\n" + node_to_markdown(inner, keep_links) + "\n\n")
        else:
            parts.append(_inline(_wrap(c), keep_links))
    text = "".join(parts)
    text = "\n".join(ln.rstrip() for ln in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def _record_markdown(rows: list[list[str]], head: list[str]) -> str:
    recs = []
    for r in rows:
        first = r[0].strip()
        key_lines = [ln for ln in first.split("\n") if ln.strip()]
        key = re.sub(r"\*+", "", key_lines[0]).strip() if key_lines else ""
        extra = textwrap.dedent("\n".join(key_lines[1:]))
        parts = [f"**{key}**" if key else ""]
        if extra:
            parts.append(extra)
        for h, v in zip(head[1:], r[1:], strict=False):
            v = v.strip()
            if not v:
                continue
            label = f"{h}:" if h else ""
            if label and v.startswith(("```", "    ")):
                parts.extend([label, v])
            elif label:
                parts.append(f"{label} {v}")
            else:
                parts.append(v)
        recs.append("\n\n".join(p for p in parts if p))
    return "\n\n".join(recs)


def _kv_table(table: Node, keep_links: bool) -> str | None:
    """`<tr><th>Key:</th><td>value</td></tr>` definition tables (Kafka generated
    config docs) -> bullet lines, skipping empty values."""
    trs = [n for n in table.iter_nodes() if n.tag == "tr"]
    if not trs:
        return None
    lines = []
    for tr in trs:
        cells = [c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]
        if len(cells) != 2 or cells[0].tag != "th" or cells[1].tag != "td":
            return None
        k = _blocks_to_inline(cells[0], keep_links).rstrip(":").strip()
        v = _blocks_to_inline(cells[1], keep_links)
        if v:
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _render_table(table: Node, keep_links: bool) -> str:
    kv = _kv_table(table, keep_links)
    if kv is not None:
        return kv
    rows: list[list[str]] = []
    header_rows = 0
    for tr in [n for n in table.iter_nodes() if n.tag == "tr"]:
        cells = []
        is_header = False
        for cell in [c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]:
            txt = (
                _cell_markdown(cell, keep_links)
                if _CTX["markdown_cells"]
                else _blocks_to_inline(cell, keep_links)
            )
            cells.append(txt if _CTX["markdown_cells"] else txt.replace("|", "\\|"))
            if cell.tag == "th":
                is_header = True
            span = (
                int(cell.attrs.get("colspan", "1") or 1)
                if (cell.attrs.get("colspan", "1") or "1").isdigit()
                else 1
            )
            cells.extend([""] * (span - 1))
        if not cells:
            continue
        rows.append(cells)
        if is_header and len(rows) == 1:
            header_rows = 1
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    body_rows = rows[1:] if header_rows else rows
    multiline = _CTX["markdown_cells"] and any(
        ("\n" in c.strip() or "```" in c) for r in body_rows for c in r
    )
    too_long = max((sum(len(c) for c in r) for r in body_rows), default=0) > LONG_ROW_CHARS
    if multiline or too_long:
        # Long / multi-paragraph / code-bearing cells would become one giant,
        # unreadable pipe-table line, so each row is rendered as a record:
        # "**first cell**" then "<header>: <cell markdown>" with code kept as blocks.
        head = [" ".join(c.split()) for c in rows[0]] if header_rows else [""] * width
        return _record_markdown(body_rows, head)
    if _CTX["markdown_cells"]:  # single-line cells: flatten any stray whitespace
        rows = [[" ".join(c.split()).replace("|", "\\|") for c in r] for r in rows]
    if not header_rows:
        head = [""] * width
        body = rows
    else:
        head, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _blocks_to_inline(node: Node, keep_links: bool) -> str:
    """Flatten a cell/summary's content to one line of text."""
    return _WS.sub(" ", _inline_deep(node, keep_links)).strip()


def _inline_deep(node: Node, keep_links: bool) -> str:
    parts: list[str] = []
    for c in node.children:
        if isinstance(c, str):
            parts.append(c)
        elif c.tag in ("p", "div", "li", "br", "ul", "ol"):
            parts.append(" " + _inline_deep(c, keep_links) + " ")
        else:
            parts.append(_inline(_wrap(c), keep_links))
    return "".join(parts)


def _wrap(c: Node) -> Node:
    w = Node("[w]")
    w.children = [c]
    return w


_LIST_BLOCK_TAGS = {
    "p",
    "pre",
    "ul",
    "ol",
    "dl",
    "div",
    "table",
    "blockquote",
    "figure",
    "section",
    "details",
    "center",
}


def _render_dd(term: str, dd: Node, keep_links: bool) -> str:
    """<dd> as blocks, not one flattened line: a definition holding a <pre> synopsis,
    several paragraphs or a nested list keeps that structure under its bold term."""
    inner: list[str] = []
    _render_blocks(dd, keep_links, inner, 1)
    inner = [b for b in inner if b.strip()]
    label = f"**{term}**" if term else ""
    if len(inner) <= 1 and not any("\n" in b for b in inner):
        body = inner[0] if inner else ""
        return f"{label}: {body}".strip(": ") if label else body
    body = "\n\n".join(inner)
    return f"{label}:\n\n{body}" if label else body


def _render_list(node: Node, depth: int, keep_links: bool) -> list[str]:
    """List items keep their children *in source order*: a run of inline content (or a <p>)
    is a paragraph, <pre> a fenced block, nested lists/tables/divs are rendered as blocks;
    everything after the first line is indented under the marker so the item stays one unit
    (a <li> with <p> + <pre> + <p> must not collapse into one long line with its code moved
    to the end)."""
    lines: list[str] = []
    ordered = node.tag == "ol"
    n = 0
    for li in [c for c in node.children if isinstance(c, Node) and c.tag == "li"]:
        n += 1
        marker = f"{n}." if ordered else "-"
        indent = "  " * depth
        pad = indent + " " * (len(marker) + 1)
        blocks: list[list[str]] = []  # each block is a list of lines
        run: list[str] = []

        def flush_run(run: list[str] = run, blocks: list[list[str]] = blocks) -> None:
            txt = _WS.sub(" ", "".join(run)).strip()
            if txt:
                blocks.append([txt])
            run.clear()

        for c in li.children:
            if isinstance(c, str):
                run.append(c)
            elif c.tag not in _LIST_BLOCK_TAGS:
                run.append(_inline(_wrap(c), keep_links))
            else:
                flush_run()
                if c.tag == "pre":
                    blocks.append(_render_pre(c).split("\n"))
                elif c.tag in ("ul", "ol"):
                    blocks.append(_render_list(c, 0, keep_links))
                elif c.tag == "p":
                    txt = _inline(c, keep_links).strip()
                    txt = re.sub(r"[ \t]*\n[ \t]*", " ", _WS.sub(" ", txt)).strip()
                    if txt:
                        blocks.append([txt])
                else:
                    inner: list[str] = []
                    _render_blocks(c, keep_links, inner, depth + 1)
                    md = "\n\n".join(inner).strip("\n")
                    if md:
                        blocks.append(md.split("\n"))
        flush_run()
        if not blocks:
            lines.append(f"{indent}{marker}")
            continue
        first, rest = blocks[0], blocks[1:]
        lines.append(f"{indent}{marker} {first[0]}".rstrip())
        lines.extend((pad + ln) if ln.strip() else "" for ln in first[1:])
        for blk in rest:
            lines.extend((pad + ln) if ln.strip() else "" for ln in blk)
    return lines


def _render_pre(node: Node) -> str:
    lang = ""
    code_node = node
    for c in node.children:
        if isinstance(c, Node) and c.tag == "code":
            code_node = c
            break
    for cls in code_node.classes + node.classes:
        m = re.match(r"(?:language|lang|highlight)-([\w+-]+)$", cls)
        if m:
            lang = m.group(1)
            break
    body = html_lib.unescape(code_node.text()).strip("\n").replace(" ", " ")
    fence = "```"
    while fence in body:
        fence += "`"
    return f"{fence}{lang}\n{body}\n{fence}"


def _render_blocks(node: Node, keep_links: bool, out: list[str], depth: int = 0) -> None:
    pending_inline: list[str] = []

    def flush() -> None:
        txt = _WS.sub(" ", "".join(pending_inline)).strip()
        if txt:
            out.append(txt)
        pending_inline.clear()

    for c in node.children:
        if isinstance(c, str):
            pending_inline.append(c)
            continue
        t = c.tag
        if re.fullmatch(r"h[1-6]", t):
            flush()
            title = _WS.sub(" ", _inline(c, False)).strip()
            if title:
                out.append("#" * int(t[1]) + " " + title)
        elif t == "p":
            flush()
            txt = _inline(c, keep_links).strip()
            if txt:
                out.append(
                    re.sub(
                        r"[ \t]*\n[ \t]*",
                        "\n",
                        _WS.sub(lambda m: "\n" if "\n" in m.group(0) else " ", txt),
                    ).strip()
                )
        elif t == "pre":
            flush()
            out.append(_render_pre(c))
        elif t in ("ul", "ol"):
            flush()
            lis = [x for x in c.children if isinstance(x, Node) and x.tag == "li"]
            if lis and all(
                any(isinstance(y, Node) and re.fullmatch(r"h[1-6]", y.tag) for y in li.children)
                for li in lis
            ):
                # <ul> used as a container of titled sections (e.g. Kafka's config-list):
                # render each <li> as a block, not as a bullet.
                for li in lis:
                    _render_blocks(li, keep_links, out, depth + 1)
            else:
                out.append("\n".join(_render_list(c, 0, keep_links)))
        elif t == "dl":
            flush()
            items: list[str] = []
            term = ""
            for d in [x for x in c.children if isinstance(x, Node)]:
                if d.tag == "dt":
                    term = _blocks_to_inline(d, keep_links)
                elif d.tag == "dd":
                    items.append(_render_dd(term, d, keep_links))
            if items:
                out.append("\n\n".join(items))
        elif t == "table":
            flush()
            md = _render_table(c, keep_links)
            if md:
                out.append(md)
        elif t == "blockquote":
            flush()
            inner: list[str] = []
            _render_blocks(c, keep_links, inner, depth + 1)
            out.append("\n".join("> " + ln if ln else ">" for ln in "\n\n".join(inner).split("\n")))
        elif t == "hr" or t == "br":
            flush()
        elif t in DEFAULT_STRIP_TAGS:
            continue
        elif t in (
            "div",
            "section",
            "article",
            "main",
            "body",
            "html",
            "[document]",
            "header",
            "aside",
            "details",
            "figure",
            "center",
            "td",
            "th",
            "tr",
            "tbody",
            "thead",
        ):
            flush()
            adm = [k for k in c.classes if k in ADMONITION_CLASSES]
            inner = []
            _render_blocks(c, keep_links, inner, depth + 1)
            if adm and inner:
                # drop a duplicated "Note"/"Warning" title heading, then prefix label
                label = adm[0].capitalize()
                first = inner[0]
                if (
                    re.fullmatch(r"#+\s*" + label, first, re.I)
                    or first.strip().lower() == label.lower()
                ):
                    inner = inner[1:]
                if inner:
                    inner[0] = f"**{label}:** {inner[0]}"
            out.extend(inner)
        else:
            pending_inline.append(_inline(_wrap(c), keep_links))
    flush()


def node_to_markdown(node: Node, keep_links: bool = False) -> str:
    out: list[str] = []
    _render_blocks(node, keep_links, out)
    text = "\n\n".join(b for b in out if b.strip())
    text = text.replace(" ", " ")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_markdown(
    markup: str,
    main_selectors: list[str] | None = None,
    strip_selectors: list[str] | None = None,
    keep_links: bool = False,
    markdown_cells: bool = False,
) -> tuple[str, bool]:
    """Returns (markdown, main_container_found). When `main_selectors` is given
    but nothing matches, the whole document is converted and the flag is False
    (callers can warn: boilerplate may leak)."""
    prev = _CTX["markdown_cells"]
    _CTX["markdown_cells"] = markdown_cells
    try:
        return _html_to_markdown(markup, main_selectors, strip_selectors, keep_links)
    finally:
        _CTX["markdown_cells"] = prev


def _html_to_markdown(markup, main_selectors, strip_selectors, keep_links):
    root = parse_html(markup)
    found = True
    target = root
    if main_selectors:
        node = find_first(root, main_selectors)
        if node is None:
            found = False
        else:
            target = node
    if strip_selectors:
        strip_nodes(target, strip_selectors)
    else:
        strip_nodes(target, [])
    return node_to_markdown(target, keep_links), found


# ---------------------------------------------------------------------------
# HTML fragments embedded in markdown (Kafka docs etc.)
# ---------------------------------------------------------------------------

_BLOCK_OPEN_RE = re.compile(
    r"^[ \t]*<(table|ul|ol|dl|div|pre|blockquote|p|figure|center)\b[^>]*>", re.I | re.M
)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_INLINE_TAG_RE = re.compile(
    r"</?(a|code|em|strong|b|i|br|span|tt|kbd|sup|sub|u|small)\b[^<>]*?/?>", re.I
)


def _find_matching_close(text: str, tag: str, start: int) -> int:
    """Index just past the matching </tag> for the <tag> opened at `start`
    (depth-counted); -1 if unbalanced."""
    rx = re.compile(rf"<(/?){re.escape(tag)}\b[^>]*?(/?)>", re.I)
    depth = 0
    for m in rx.finditer(text, start):
        if m.group(2):  # self closing
            continue
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return m.end()
    return -1


def convert_embedded_html(text: str, keep_links: bool = False, markdown_cells: bool = False) -> str:
    """Converts block-level HTML fragments (outside fenced code) to markdown
    and unwraps simple inline tags. Only *known HTML tag names* are touched, so
    prose like `KStream<String, Long>` is never mangled."""
    lines = text.split("\n")
    # split into fence / non-fence segments so we never rewrite code
    segments: list[tuple[bool, str]] = []
    buf: list[str] = []
    in_fence = False
    for ln in lines:
        if _FENCE_RE.match(ln):
            if not in_fence:
                if buf:
                    segments.append((False, "\n".join(buf)))
                buf = [ln]
                in_fence = True
            else:
                buf.append(ln)
                segments.append((True, "\n".join(buf)))
                buf = []
                in_fence = False
            continue
        buf.append(ln)
    if buf:
        segments.append((in_fence, "\n".join(buf)))

    out_parts: list[str] = []
    for is_fence, seg in segments:
        if is_fence:
            out_parts.append(seg)
            continue
        seg = _SVG_RE.sub(
            _svg_to_text, seg
        )  # inline SVG drawings: keep the aria-label/<title> as the caption
        pos, pieces = 0, []
        while True:
            m = _BLOCK_OPEN_RE.search(seg, pos)
            if not m:
                pieces.append(seg[pos:])
                break
            tag = m.group(1).lower()
            start = m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
            end = _find_matching_close(seg, tag, start)
            if end == -1:
                pieces.append(seg[pos : m.end()])
                pos = m.end()
                continue
            pieces.append(seg[pos:start])
            md, _ = html_to_markdown(
                _escape_foreign_tags(seg[start:end]),
                keep_links=keep_links,
                markdown_cells=markdown_cells,
            )
            pieces.append("\n\n" + md + "\n\n")
            pos = end
        seg2 = "".join(pieces)
        seg2, ph = _protect_inline_code(seg2)  # tags shown in `inline code` are content, not markup
        seg2 = _convert_inline_known_tags(seg2, keep_links)
        seg2 = _strip_residual_html(seg2)
        seg2 = _restore_inline_code(seg2, ph)
        out_parts.append(seg2)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_parts))


_KNOWN_TAG_ALT = "|".join(sorted(KNOWN_HTML_TAGS, key=len, reverse=True))
_FOREIGN_LT_RE = re.compile(rf"<(?!/?(?:{_KNOWN_TAG_ALT})\b|!--)(?=[A-Za-z/])", re.I)


def _escape_foreign_tags(fragment: str) -> str:
    """`Map<String, KStream<String>>` inside an HTML fragment must survive:
    only known HTML tag names are left as tags, any other `<` is escaped."""
    return _FOREIGN_LT_RE.sub("&lt;", fragment)


_IMG_TAG_RE = re.compile(r"<img\b((?:[^<>\"']|\"[^\"]*\"|'[^']*')*)/?>", re.I)
_FIGCAPTION_RE = re.compile(r"<figcaption\b[^<>]*>(.*?)</figcaption>", re.S | re.I)
_EMPTY_EMBED_RE = re.compile(
    r"<(iframe|video|audio|embed|object)\b[^<>]*>\s*</\1>|<(?:embed|source|track)\b[^<>]*/?>", re.I
)
_ANCHOR_RE = re.compile(r"<a\b[^<>]*\bid=[^<>]*?(?:/>|>\s*</a>)", re.I)
# wrapper tags left alone on a line (their partner sits in another fence/segment,
# e.g. mkdocs `<div class="termy">` around a fenced console session)
_WRAPPER_LINE_RE = re.compile(
    r"^[ \t>]*</?(?:div|center|figure|section|details|span|p|article|aside|main)\b[^<>]*>[ "
    r"\t]*$\n?",
    re.I | re.M,
)


def _img_to_text(m: re.Match) -> str:
    alt = re.search(r"\balt=(?:\"([^\"]*)\"|'([^']*)')", m.group(1))
    text = html_lib.unescape((alt.group(1) or alt.group(2) or "") if alt else "").strip()
    return f"[image: {text}]" if len(text) > 8 else ""


_INLINE_TABLE_RE = re.compile(r"<table\b[^<>]*>.*?</table>", re.S | re.I)
_ONE_LINE_BLOCK_RE = re.compile(
    r"<(div|center)\b[^<>]*>((?:(?!</?(?:div|center)\b)[^\n])*?)</\1>", re.I
)


_SVG_RE = re.compile(r"<svg\b[^<>]*>.*?</svg>", re.S | re.I)


def _svg_to_text(m: re.Match) -> str:
    svg = m.group(0)
    label = re.search(r"aria-label=(?:\"([^\"]*)\"|'([^']*)')", svg[:1500])
    title = re.search(r"<title[^>]*>(.*?)</title>", svg, re.S | re.I)
    text = html_lib.unescape(
        (label.group(1) or label.group(2)) if label else (title.group(1) if title else "")
    ).strip()
    text = _WS.sub(" ", text)
    return f"[image: {text}]" if len(text) > 8 else ""


def _flatten_table(m: re.Match) -> str:
    """A <table> embedded mid-line (typically inside a markdown pipe-table cell, where a
    real table cannot live): rendered as one line, columns joined with ` / `."""
    md, _ = html_to_markdown(m.group(0))
    rows = []
    for ln in md.split("\n"):
        if not ln.strip().startswith("|") or re.fullmatch(r"\|(?:\s*:?-{3,}:?\s*\|)+", ln.strip()):
            continue
        cells = [
            c.strip().replace("\\|", "/") for c in re.split(r"(?<!\\)\|", ln.strip().strip("|"))
        ]
        rows.append(" / ".join(c for c in cells if c))
    if not rows:
        return ""
    head, body = (rows[0], rows[1:]) if len(rows) > 1 else ("", rows)
    return ((f"({head}) " if head else "") + "; ".join(body)).strip()


_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _protect_inline_code(seg: str) -> tuple[str, list[str]]:
    ph: list[str] = []

    def _sub(m: re.Match) -> str:
        ph.append(m.group(0))
        return f"\x01IC{len(ph) - 1}\x01"

    return _INLINE_CODE_RE.sub(_sub, seg), ph


def _restore_inline_code(seg: str, ph: list[str]) -> str:
    return re.sub(r"\x01IC(\d+)\x01", lambda m: ph[int(m.group(1))], seg)


_HEAD_RE = re.compile(r"<head\b[^<>]*>.*?</head>", re.S | re.I)
_SCRIPT_RE = re.compile(r"<script\b[^<>]*>.*?</script>|<script\b[^<>]*/>", re.S | re.I)
_ABBR_RE = re.compile(r"<(abbr|dfn)\b([^<>]*)>(.*?)</\1>", re.S | re.I)
_UNWRAP_TAGS_RE = re.compile(
    r"</?(?:nobr|button|form|textarea|label|fieldset|legend)\b[^<>]*>", re.I
)
_VOID_RESIDUE_RE = re.compile(
    r"<(?:meta\b[^<>]*=[^<>]*|link\b[^<>]*\b(?:rel|href)=[^<>]*|hr\b[^<>]*|input\b[^<>]*)/?>", re.I
)


def _abbr_to_text(m: re.Match) -> str:
    """`<abbr title="Software Development Kits">SDKs</abbr>` -> `SDKs (Software Development Kits)`;
    the expansion is written once (no repeat when the text already contains/equals it)."""
    inner = _WS.sub(" ", re.sub(r"<[^<>]+>", "", m.group(3))).strip()
    t = re.search(r"\btitle=(?:\"([^\"]*)\"|'([^']*)')", m.group(2))
    expansion = html_lib.unescape((t.group(1) or t.group(2)) if t else "").strip().replace("|", "/")
    expansion = _WS.sub(" ", expansion)
    if (
        not expansion
        or not inner
        or expansion.lower() == inner.lower()
        or expansion.lower() in inner.lower()
    ):
        return inner
    return f"{inner} ({expansion})"


def _strip_residual_html(seg: str) -> str:
    """Whatever block conversion did not cover, outside code fences: <img> (keep
    meaningful alt text only), <figcaption> (keep caption), id-only anchors, and
    orphaned wrapper-tag lines."""
    seg = _HEAD_RE.sub("", seg)
    seg = _SCRIPT_RE.sub("", seg)
    seg = _ABBR_RE.sub(_abbr_to_text, seg)
    seg = _UNWRAP_TAGS_RE.sub(" ", seg)
    seg = _VOID_RESIDUE_RE.sub("", seg)
    seg = _INLINE_TABLE_RE.sub(_flatten_table, seg)
    seg = _ONE_LINE_BLOCK_RE.sub(lambda m: f"\n\n{m.group(2).strip()}\n\n", seg)
    seg = _FIGCAPTION_RE.sub(
        lambda m: "\n\n" + _WS.sub(" ", _INLINE_TAG_RE.sub("", m.group(1))).strip() + "\n\n", seg
    )
    seg = _IMG_TAG_RE.sub(_img_to_text, seg)
    seg = _ANCHOR_RE.sub("", seg)
    seg = _EMPTY_EMBED_RE.sub("", seg)
    seg = _WRAPPER_LINE_RE.sub("", seg)
    return seg


def _convert_inline_known_tags(seg: str, keep_links: bool) -> str:
    # <code>x</code> -> `x`; <a href=..>t</a> -> t ; <br> -> newline; others unwrap
    seg = re.sub(
        r"<code[^<>]*>(.*?)</code>",
        lambda m: f"`{html_lib.unescape(m.group(1))}`",
        seg,
        flags=re.S | re.I,
    )
    seg = re.sub(r"<(?:strong|b)\b[^<>]*>(.*?)</(?:strong|b)>", r"**\1**", seg, flags=re.S | re.I)
    seg = re.sub(r"<(?:em|i)\b[^<>]*>(.*?)</(?:em|i)>", r"*\1*", seg, flags=re.S | re.I)
    if keep_links:
        seg = re.sub(
            r"<a\b[^<>]*href=\"([^\"]*)\"[^<>]*>(.*?)</a>", r"[\2](\1)", seg, flags=re.S | re.I
        )
    seg = re.sub(r"<br\s*/?>", "\n", seg, flags=re.I)
    seg = _INLINE_TAG_RE.sub("", seg)
    seg = re.sub(r"</?p\b[^<>]*>", "\n\n", seg, flags=re.I)
    return seg
