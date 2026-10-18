"""Per-source cleaning profiles applied *before* the generic markdown cleaner
(`rag.clean.clean_markdown`). A profile turns a source-specific markup dialect
into plain markdown:

  markdown  default; also strips HF `[[autodoc]]` API stubs and `<hfoption>` wrappers
  rst       reStructuredText (Django .txt, pytorch/tutorials .rst, sphinx-gallery .py)
  d2l       d2lbook markdown: keep the PyTorch tab only, drop d2lbook roles
  mintlify  Mintlify/Starlight MDX: `:::python`/`:::js` blocks, Tabs/Accordion/Aside
  hugo      Hugo shortcodes ({{< >}}/{{% %}}) + embedded HTML tables (Kafka)
  mdn       mdn/content: {{macros}} resolved, frontmatter title -> H1, empty sections dropped
  html      raw HTML pages (crawler output is already converted; fragments only)
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable

from .htmlmd import convert_embedded_html, html_to_markdown

_FENCE_LINE = re.compile(r"^(\s*)(```|~~~)")


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------


def _outside_fences(text: str, fn: Callable[[str], str]) -> str:
    """Applies `fn` to the non-fenced segments only. A fence is closed only by a bare fence line of
    the same character (a `~~~~~~` squiggle line inside a ``` block is content)."""
    out: list[str] = []
    buf: list[str] = []
    in_fence = False
    opener = ""
    for ln in text.split("\n"):
        m = _FENCE_LINE.match(ln)
        if in_fence:
            buf.append(ln)
            if m and m.group(2)[0] == opener and re.fullmatch(r"\s*(`{3,}|~{3,})\s*", ln):
                out.append("\n".join(buf))
                buf = []
                in_fence = False
            continue
        if m:
            if buf:
                out.append(fn("\n".join(buf)))
            buf = [ln]
            in_fence = True
            opener = m.group(2)[0]
            continue
        buf.append(ln)
    if buf:
        out.append("\n".join(buf) if in_fence else fn("\n".join(buf)))
    return "\n".join(out)


def strip_relative_links_and_images(text: str) -> str:
    """`[text](relative/path)` -> `text`; `![alt](x)` -> alt text; keeps http(s) links."""

    def _img(m: re.Match) -> str:
        alt = m.group(1).strip()
        return f"*[figure: {alt}]*" if len(alt) > 12 else ""

    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2).strip()
        return m.group(0) if re.match(r"https?://", url) else label

    def _fn(seg: str) -> str:
        seg = re.sub(r"!\[([^\]]*)\]\([^)]*\)", _img, seg)
        seg = re.sub(r"\[([^\]]+)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)", _link, seg)
        return seg

    return _outside_fences(text, _fn)


# ---------------------------------------------------------------------------
# markdown (default)
# ---------------------------------------------------------------------------

_AUTODOC_BLOCK_RE = re.compile(
    r"^\[\[autodoc\]\][^\n]*\n(?:[ \t]+[^\n]*\n|[ \t]*\n(?=[ \t]+\S))*", re.M
)
_HF_WRAPPER_RE = re.compile(r"</?(?:hfoptions|hfoption|Tip|iframe|details)\b[^>]*>", re.I)
_SUMMARY_RE = re.compile(r"<summary\b[^>]*>(.*?)</summary>", re.S | re.I)


# Notebook-output HTML pasted into docs: pandas DataFrame tables and tqdm/Trainer
# progress widgets. Pure noise for retrieval.
_NB_DATAFRAME_RE = re.compile(r"<table\b[^>]*class=\"dataframe\"[^>]*>.*?</table>", re.S | re.I)
_NB_PROGRESS_RE = re.compile(
    r"<div>\s*<progress\b.*?</div>|<progress\b[^>]*>.*?</progress>", re.S | re.I
)


_MKDOCSTRINGS_RE = re.compile(r"^:::[ \t]+[\w.]+[ \t]*\n(?:[ \t]+[^\n]*\n)*", re.M)
_HEADING_ATTR_RE = re.compile(
    r"^(#{1,6}[ \t]+.*?)[ \t]*\{:?[ "
    r"\t]*(?:#[\w.-]+|\.[\w-]+|[\w-]+=(?:\"[^\"]*\"|'[^']*'|\S+))(?:[ \t]+[^}]*)?\}[ \t]*$",
    re.M,
)
_TWEET_RE = re.compile(
    r"<blockquote\b[^>]*class=\"[^\"]*twitter-tweet[^\"]*\"[^>]*>.*?</blockquote>(?:\s*<script[^>]*></script>)?",
    re.S | re.I,
)


_SNIPPET_MARKER_RE = re.compile(
    r"^[ \t]*(?:#|//)?[ \t]*--8<--[ \t]*\[(?:start|end):[^\]]*\][ \t]*$\n?", re.M
)
_EMPTY_FENCE_RE = re.compile(
    r"^[ \t]*(```|~~~)[\w+.-]*[ \t]*\n(?:[ \t]*\n)*[ \t]*\1[ \t]*$\n?", re.M
)


# README language switcher: `*[English](README.md) ∙ [日本語](README-ja.md) ∙ ...*`
_LANG_SWITCHER_RE = re.compile(
    r"^[ \t]*\*?\[English\]\([^)\n]*\)"
    r"(?:[ \t]*(?:[∙|·•]|\*\*\|\*\*)[ \t]*\[[^\]\n]+\]\([^)\n]*\))+[^\n]*$\n?",
    re.M,
)


_IMG_ATTR_RE = re.compile(r"(?<=\))\{:?[ \t]*(?:[#.][\w-]+|[\w-]+=)[^}\n]*\}")


_SLASH_BLOCK_RE = re.compile(r"^([ \t]*)(/{3,})[ \t]*(\S.*?)?[ \t]*$")


def _resolve_slash_blocks(text: str) -> str:
    """FastAPI/Typer markdown-extensions blocks (outside code fences):
         /// warning | Title        ->  **Title:**   (or **Warning:** when no title)
         ...
         ///                       (closer, dropped)
         //// tab | Python 3.10+   ->  **Python 3.10+**
         ////                      (closer, dropped)
    Opener and closer lines are told apart by the presence of a name; a stray closer is dropped
    too."""
    out: list[str] = []
    in_fence = False
    for ln in text.split("\n"):
        if ln.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            out.append(ln)
            continue
        m = None if in_fence else _SLASH_BLOCK_RE.match(ln)
        if not m:
            out.append(ln)
            continue
        indent, rest = m.group(1), m.group(3)
        if not rest:
            continue  # closer
        name, _, title = rest.partition("|")
        name, title = name.strip(), title.strip()
        if name.lower() == "tab":
            label = f"**{title or 'Tab'}**"
        else:
            label = f"**{title or name[:1].upper() + name[1:]}:**"
        out.extend(
            ["", f"{indent}{label}", ""] if out and out[-1].strip() else [f"{indent}{label}", ""]
        )
    return "\n".join(out)


# Self-closing MDX figure components with long (often multi-line) props:
#   <Diagram name="x" height={250} alt="A tree graph ..." />   <Illustration .../>   <Image .../>
_JSX_FIGURE_RE = re.compile(
    r"<(?:Diagram|Illustration|Image|Img|Video|Gif|Figure|Lightbox)\b"
    r"((?:[^>\"']|\"[^\"]*\"|'[^']*'|=>)*?)/?>",
)
_JSX_ALT_RE = re.compile(r"\b(?:alt|title)=(?:\"([^\"]*)\"|'([^']*)')")
_HTML_HEADING_RE = re.compile(r"^[ \t]*<h([1-6])\b[^>]*>(.*?)</h\1>[ \t]*$", re.M | re.I)
_TABLE_ROW_HTML_RE = re.compile(r"^[^\n]*\|[^\n]*$", re.M)


def _table_cell_html(seg: str) -> str:
    """`<ul><li>..</li></ul>` / `<br>` inside pipe-table rows -> plain text separators."""

    def _row(m: re.Match) -> str:
        r = m.group(0)
        if "<" not in r:
            return r
        r = re.sub(r"<br\s*/?>", "; ", r, flags=re.I)
        r = re.sub(r"<li>", "\u2022 ", r, flags=re.I)
        return re.sub(r"</?(?:ul|ol)>|</li>", "", r, flags=re.I)

    return _TABLE_ROW_HTML_RE.sub(_row, seg)


def _jsx_figure(m: re.Match) -> str:
    a = _JSX_ALT_RE.search(m.group(1))
    alt = ((a.group(1) or a.group(2)) if a else "").strip()
    return f"*[figure: {alt}]*\n\n" if len(alt) > 12 else ""


_VITEPRESS_OPEN_RE = re.compile(
    r"^:::[ \t]+(tip|info|warning|danger|note|caution|important|details)\b[ \t]*", re.M
)
_VITEPRESS_GROUP_RE = re.compile(r"^:::[ \t]*code-group[^\n]*\n?", re.M)


_DOCS_CALLOUT_RE = re.compile(r"<docs-callout\b[^>]*?\btitle=\"([^\"]*)\"[^>]*>", re.S)
_DOCS_TITLED_RE = re.compile(
    r"<docs-(?:step|card|tab|decorative-header|code-example)\b[^>]*?\b(?:title|label)=\"([^\"]*)\"[^>]*?/?>",
    re.S,
)
_DOCS_ANY_RE = re.compile(r"</?docs-[\w-]+\b[^>]*?/?>", re.S)
_KT_TAB_RE = re.compile(r"<tab\b[^>]*?\btitle=\"([^\"]*)\"[^>]*>", re.S)
_KT_BLOCK_RE = re.compile(
    r"<(note|tip|warning|tldr|deflist|def|primary-label|secondary-label)\b([^>]*)>", re.S
)
_KT_DROP_RE = re.compile(
    r"</?(?:tabs|tab|picture|deflist|def|tldr|primary-label|secondary-label)\b[^>]*?/?>", re.S
)
_VUE_DEMO_RE = re.compile(r"<div\b[^>]*\bclass=\"demo[^\"]*\"[^>]*>.*?</div>", re.S)
_TABLE_LEFTOVER_RE = re.compile(r"</?(?:table|thead|tbody|tfoot|tr)\b[^>]*>", re.I)
_CELL_OPEN_RE = re.compile(r"<t[dh]\b[^>]*>", re.I)
_CELL_CLOSE_RE = re.compile(r"</t[dh]>", re.I)


_INLINE_CODE_PARK_RE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def _park_inline_code(fn: Callable[[str], str]) -> Callable[[str], str]:
    """Wraps a prose transform so that `<tags>` inside inline code spans are never touched."""

    def _wrapped(seg: str) -> str:
        spans: list[str] = []

        def _park(m: re.Match) -> str:
            spans.append(m.group(0))
            return f"\x01IC{len(spans) - 1}\x01"

        out = fn(_INLINE_CODE_PARK_RE.sub(_park, seg))
        return re.sub(r"\x01IC(\d+)\x01", lambda m: spans[int(m.group(1))], out)

    return _wrapped


def _strip_demo_divs(seg: str) -> str:
    """Removes `<div class="demo ...">...</div>` (rendered live examples, nested divs counted)."""
    out: list[str] = []
    pos = 0
    open_rx = re.compile(r"<div\b[^>]*\bclass=\"demo[^\"]*\"[^>]*>", re.I)
    tag_rx = re.compile(r"<(/?)div\b[^>]*>", re.I)
    while True:
        m = open_rx.search(seg, pos)
        if not m:
            out.append(seg[pos:])
            break
        out.append(seg[pos : m.start()])
        depth, end = 1, m.end()
        for t in tag_rx.finditer(seg, m.end()):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                end = t.end()
                break
        else:
            end = len(seg)
        pos = end
    return "".join(out)


def _custom_components(seg: str) -> str:
    """Angular `<docs-*>`, Writerside (Kotlin) `<tabs>/<tab>/<note>`, `<picture>`, VitePress demo
    divs, and table tags left over when a table spans code fences."""
    seg = _strip_demo_divs(seg)
    seg = _DOCS_CALLOUT_RE.sub(lambda m: f"**{m.group(1)}:**\n", seg)
    seg = _DOCS_TITLED_RE.sub(lambda m: f"**{m.group(1)}**\n", seg)
    seg = _DOCS_ANY_RE.sub("", seg)
    seg = _KT_TAB_RE.sub(lambda m: f"**{m.group(1)}**\n", seg)
    seg = _KT_BLOCK_RE.sub(
        lambda m: (
            "**" + m.group(1).title() + ":** " if m.group(1) in ("note", "tip", "warning") else ""
        ),
        seg,
    )
    seg = _KT_DROP_RE.sub("", seg)
    seg = re.sub(r"</(?:note|tip|warning)>", "", seg)
    seg = _TABLE_LEFTOVER_RE.sub("", seg)
    seg = _CELL_CLOSE_RE.sub("\n", _CELL_OPEN_RE.sub("\n\n", seg))
    seg = re.sub(r"</?(?:ul|ol)\b[^>]*>", "\n", seg)  # list tags glued to a prose line
    seg = re.sub(r"[ \t]*<li\b[^>]*>[ \t]*", "\n- ", seg)
    return re.sub(r"</li>", "", seg)


_ONELINE_FENCE_RE = re.compile(r"^([ \t]*)```([^`\n]+?)```[ \t]*$", re.M)


_JSX_COMMENT_RE = re.compile(r"[ \t]*\{/\*.*?\*/\}", re.S)


def profile_markdown(text: str) -> str:
    # ```argocd repo add ...``` on one line is inline code, not a fence (it would flip fence parity)
    text = _ONELINE_FENCE_RE.sub(lambda m: f"{m.group(1)}`{m.group(2).strip()}`", text)
    text = _outside_fences(text, lambda s: _JSX_COMMENT_RE.sub("", s))  # `## Props {/*props*/}`
    text = _outside_fences(text, lambda s: _JSX_FIGURE_RE.sub(_jsx_figure, s))
    text = _outside_fences(text, _strip_demo_divs)  # VitePress live demos (duplicates of the code)
    text = _outside_fences(
        text,
        _park_inline_code(
            lambda s: _DOCS_ANY_RE.sub(
                "",
                _DOCS_CALLOUT_RE.sub(
                    lambda m: f"**{m.group(1)}:**\n",
                    _DOCS_TITLED_RE.sub(lambda m: f"**{m.group(1)}**\n", s),
                ),
            )
        ),
    )
    text = _outside_fences(
        text,
        _park_inline_code(lambda s: _table_cell_html(_HTML_HEADING_RE.sub(r"**\2**", s))),
    )  # <h3>Bad</h3> -> bold line; <ul><li> in table cells -> bullets
    # VitePress containers: `::: tip Title` ... `:::`  (and `::: code-group`)
    text = _outside_fences(
        text,
        lambda s: _VITEPRESS_OPEN_RE.sub(
            r":::\1 ",
            _VITEPRESS_GROUP_RE.sub(
                "", re.sub(r"^[ \t]*:{3,}(?=[ \t]*(?:\w|$))", ":::", s, flags=re.M)
            ),
        ),
    )
    text = _outside_fences(text, lambda s: _LANG_SWITCHER_RE.sub("", s))
    text = _SNIPPET_MARKER_RE.sub("", text)  # pymdownx `--8<-- [start:x]` / `[end:x]` markers
    text = _outside_fences(
        text, lambda s: _NB_PROGRESS_RE.sub("", _NB_DATAFRAME_RE.sub("", _TWEET_RE.sub("", s)))
    )
    text = _outside_fences(
        text, lambda s: _AUTODOC_BLOCK_RE.sub("", s + ("\n" if not s.endswith("\n") else ""))
    )
    text = _outside_fences(
        text, lambda s: _MKDOCSTRINGS_RE.sub("", s + ("\n" if not s.endswith("\n") else ""))
    )
    text = _outside_fences(
        text,
        _park_inline_code(
            lambda s: _HF_WRAPPER_RE.sub(
                "", _SUMMARY_RE.sub(lambda m: "**" + m.group(1).strip() + "**", s)
            )
        ),
    )
    text = _resolve_slash_blocks(
        text
    )  # /// warning | Title ... ///  and  //// tab | Title ... ////
    text = _resolve_lang_blocks(text)  # :::tip / :::note admonitions (Docusaurus, mkdocs)
    text = _outside_fences(
        text, lambda s: _HEADING_ATTR_RE.sub(r"\1", s)
    )  # `## Title { #id }` / `{: #id }`
    text = _outside_fences(
        text, lambda s: _IMG_ATTR_RE.sub("", s)
    )  # `![](x.png){ align="center" width="60%" }`
    text = convert_embedded_html(text)  # <div><img>, <table>, <figure> fragments
    text = _outside_fences(text, _park_inline_code(_custom_components))
    return _EMPTY_FENCE_RE.sub("", text)


# ---------------------------------------------------------------------------
# d2l
# ---------------------------------------------------------------------------

_D2L_TAB_NAMES = {"mxnet", "pytorch", "tensorflow", "jax", "all"}


def _tabs_from(arg: str) -> list[str]:
    return [t.strip().lower() for t in re.split(r"[,\s]+", arg) if t.strip()]


_TAB_COND_RE = re.compile(
    r"^(\s*)(?:if|elif)\s+(tab\.selected\([^)]*\)(?:\s+or\s+tab\.selected\([^)]*\))*)\s*:\s*$"
)


def resolve_tab_selected(code_lines: list[str]) -> list[str]:
    """d2l in-cell framework switches:
         if tab.selected('mxnet'):  ...   -> dropped (no pytorch in the condition)
         if tab.selected('pytorch'):  ... -> body unwrapped and dedented
    Conditions may be `or`-combinations (`tab.selected('mxnet') or tab.selected('pytorch')`)."""
    out: list[str] = []
    i, n = 0, len(code_lines)
    while i < n:
        m = _TAB_COND_RE.match(code_lines[i])
        if not m:
            out.append(code_lines[i])
            i += 1
            continue
        base = len(m.group(1))
        names = re.findall(r"['\"](\w+)['\"]", m.group(2))
        j = i + 1
        body: list[str] = []
        while j < n and (not code_lines[j].strip() or _indent_of(code_lines[j]) > base):
            body.append(code_lines[j])
            j += 1
        while body and not body[-1].strip():  # trailing blanks belong to what follows
            body.pop()
            j -= 1
        if "pytorch" in names or "all" in names:
            nonblank = [b for b in body if b.strip()]
            cut = min((_indent_of(b) for b in nonblank), default=base + 4) - base
            out.extend(resolve_tab_selected([b[cut:] if b.strip() else b for b in body]))
        i = j
    return out


_NUMREF_KIND = (
    ("sec", "section"),
    ("subsec", "section"),
    ("chap", "chapter"),
    ("fig", "figure"),
    ("img", "figure"),
    ("tab", "table"),
    ("table", "table"),
)


def _numref(label: str) -> str:
    if "_" in label:
        prefix, rest = label.split("_", 1)
        for pre, word in _NUMREF_KIND:
            if prefix == pre:
                return f"the {rest.replace('_', ' ').replace('-', ' ')} {word}"
        return f"the {label.replace('_', ' ').replace('-', ' ')} section"
    return f"the {label} section"


def profile_d2l(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    in_tab_block: bool | None = None  # None = not in; True = keep; False = drop
    while i < n:
        ln = lines[i]
        # prose tab blocks:  :begin_tab:`mxnet, pytorch` ... :end_tab:
        m = re.match(r"^:begin_tab:`([^`]*)`\s*$", ln.strip())
        if m and in_tab_block is None:
            tabs = _tabs_from(m.group(1))
            in_tab_block = ("pytorch" in tabs) or ("all" in tabs)
            i += 1
            continue
        if ln.strip() == ":end_tab:" and in_tab_block is not None:
            in_tab_block = None
            i += 1
            continue
        # fenced d2l cells
        if ln.strip() == "```toc":  # d2lbook table-of-contents block
            j = i + 1
            while j < n and not lines[j].startswith("```"):
                j += 1
            i = j + 1
            continue
        fm = re.match(r"^```\{\.(\w+)[^}]*\}\s*$", ln)
        if fm:
            j = i + 1
            while j < n and not lines[j].startswith("```"):
                j += 1
            body = lines[i + 1 : j]
            i = j + 1
            lead = next(
                (k for k, b in enumerate(body) if b.strip()), 0
            )  # a cell may open with blank lines
            first = body[lead].strip() if body else ""
            tm = re.match(
                r"^(?:#\s*)?(?:%%|#@)tab\s+(.+)$", first
            )  # one resnet cell is written `# %%tab jax`
            if tm:
                tabs = _tabs_from(tm.group(1))
                if "pytorch" not in tabs and "all" not in tabs:
                    continue
                body = body[lead + 1 :]
            elif any(b.startswith("%load_ext d2lbook") or "tab.interact_select" in b for b in body):
                continue
            lang = {"python": "python", "bash": "bash", "sh": "bash"}.get(fm.group(1), fm.group(1))
            if in_tab_block is False:
                continue
            body = resolve_tab_selected([re.sub(r"\s*#@save\s*$", "", b) for b in body])
            if any(b.strip() for b in body):
                out.append(f"```{lang}")
                out.extend(body)
                out.append("```")
            continue
        if in_tab_block is False:
            i += 1
            continue
        out.append(ln)
        i += 1
    text = "\n".join(out)

    def _prose(seg: str) -> str:
        seg = re.sub(r"^:label:`[^`]*`\s*$", "", seg, flags=re.M)
        seg = re.sub(r"^:width:`[^`]*`\s*$|^:height:`[^`]*`\s*$", "", seg, flags=re.M)
        seg = re.sub(r"^:eqlabel:`[^`]*`\s*$", "", seg, flags=re.M)
        seg = re.sub(
            r"!\[[^\]]*\]\(\.\./img/[^)]*\)", "", seg
        )  # figures (alt text is just a caption)
        seg = re.sub(r"^:(?:width|height|label|eqlabel):`[^`]*`\s*$", "", seg, flags=re.M)
        # "Section :numref:`sec_x`" would otherwise become "Section the x section"
        seg = re.sub(
            r"\b(?:section|figure|fig\.?|chapter|table|sec\.?)[ \t]+(?=:numref:`)",
            "",
            seg,
            flags=re.I,
        )
        seg = re.sub(r"\b(?:equation|eq\.?)[ \t]+(?=:eqref:`)", "", seg, flags=re.I)
        seg = re.sub(r":numref:`([^`]*)`", lambda m: _numref(m.group(1)), seg)
        seg = re.sub(r":eqref:`([^`]*)`", "the equation above", seg)
        seg = re.sub(r":citet:`[^`]*`", "the cited work", seg)
        seg = re.sub(r"\s*:cite[a-z]*:`[^`]*`", "", seg)
        seg = re.sub(r":(?:ref|label|math):`([^`]*)`", r"\1", seg)
        # bare labels (and `:ref:` targets, left verbatim above) (`... in sec_valueiter corresponds
        # to`), outside inline code
        seg = re.sub(
            r"(?<![\w`.:/-])((?:sub)?sec|chap|fig)_([a-z][\w-]*)\b(?!`)",
            lambda m: _numref(m.group(0)),
            seg,
        )
        seg = re.sub(r"\[\*\*(.+?)\*\*\]", r"\1", seg)  # d2l reveal markers
        seg = re.sub(r"\[([^\]\n]+)\]\(\.\./img/[^)]+\)", "", seg)
        return seg

    text = _outside_fences(text, _prose)
    text = strip_relative_links_and_images(text)
    return text


# ---------------------------------------------------------------------------
# mintlify / starlight MDX
# ---------------------------------------------------------------------------

_TITLED_COMPONENT_RE = re.compile(
    r"<(Tab|Accordion|AccordionGroup|Card|Step|Expandable|Callout|Aside|Note|Tip|Warning|Info|Check)\b([^>\n]*)>",
    re.I,
)


def _title_from_attrs(attrs: str) -> str:
    m = re.search(r'(?:title|label)=["\']([^"\']+)["\']', attrs)
    return m.group(1) if m else ""


def _resolve_lang_blocks(text: str) -> str:
    """Mintlify language-conditional blocks: keep `:::python`, drop `:::js`
    (and `:::ts`/`:::javascript`), unwrap everything else."""
    out: list[str] = []
    stack: list[str] = []  # 'keep' | 'drop' | 'other'
    in_fence = False
    for ln in text.split("\n"):
        if _FENCE_LINE.match(ln):
            in_fence = not in_fence
        if not in_fence:
            s = ln.strip()
            m = re.match(r"^:::(\w+)?(?:\[([^\]]*)\])?(?:[ \t]+(\S.*?))?\s*$", s)
            if m and not m.group(1) and m.group(3):
                m = None  # `::: fastapi.X` (mkdocstrings) is not an admonition
            if m:
                name = (m.group(1) or "").lower()
                if name:  # opener
                    if name in ("js", "javascript", "ts", "typescript"):
                        stack.append("drop")
                    elif name == "python":
                        stack.append("keep")
                    else:
                        stack.append("other")
                        label = (m.group(2) or m.group(3) or name).strip()
                        if not any(k == "drop" for k in stack[:-1]):
                            out.append(f"**{label[:1].upper() + label[1:]}:**")
                    continue
                else:  # closer (a stray one with an empty stack is dropped too)
                    if stack:
                        stack.pop()
                    continue
        if any(k == "drop" for k in stack):
            continue
        out.append(ln)
    return "\n".join(out)


def profile_mintlify(text: str) -> str:
    text = _resolve_lang_blocks(text)
    text = re.sub(
        r"[ \t]*(?:#|//)\s*\[!code[^\]]*\][ \t]*$", "", text, flags=re.M
    )  # code-annotation markers

    def _fn(seg: str) -> str:
        def _repl(m: re.Match) -> str:
            title = _title_from_attrs(m.group(2))
            return f"\n**{title}**\n" if title else ""

        seg = _TITLED_COMPONENT_RE.sub(_repl, seg)
        seg = re.sub(r"</?[A-Z][A-Za-z0-9]*(?:\s[^>\n]*)?/?>", "", seg)  # remaining JSX wrappers
        seg = re.sub(r"^\s*(import|export)\s+.*$", "", seg, flags=re.M)
        seg = re.sub(r"\{/\*.*?\*/\}", "", seg, flags=re.S)  # JSX comments
        seg = re.sub(
            r"<(?:img|video|source|iframe)\b[^>]*?/?>(?:\s*</(?:video|iframe)>)?",
            "",
            seg,
            flags=re.S | re.I,
        )
        seg = re.sub(r"@\[`?([^\]`]+)`?\]", r"`\1`", seg)  # Mintlify cross-refs @[`interrupt`]
        return seg

    text = _outside_fences(text, _fn)
    return strip_relative_links_and_images(text)


# ---------------------------------------------------------------------------
# hugo
# ---------------------------------------------------------------------------


def profile_hugo(text: str) -> str:
    text = re.sub(
        r"<!--.*?-->", lambda m: "" if len(m.group(0)) < 6000 else m.group(0), text, flags=re.S
    )  # license headers etc. first: their indented URLs must not become code
    # ```formula``` on one line is inline code, not a fence (it would flip fence parity)
    text = _ONELINE_FENCE_RE.sub(lambda m: f"{m.group(1)}`{m.group(2).strip()}`", text)

    # {{< clients-example ... >}} cli-session {{< /clients-example >}} -> fenced block
    def _example(m: re.Match) -> str:
        body = m.group(1).strip("\n")
        if "```" in body:
            return body
        return "```\n" + body + "\n```"

    text = re.sub(
        r"\{\{<\s*clients-example\b[^>]*>\}\}(.*?)\{\{<\s*/clients-example\s*>\}\}",
        _example,
        text,
        flags=re.S,
    )
    text = re.sub(
        r"\{\{%\s*redis-cli\b[^%]*%\}\}(.*?)\{\{%\s*/redis-cli\s*%\}\}", _example, text, flags=re.S
    )
    # kubernetes/website shortcodes: keep the visible text instead of deleting the whole tag
    text = re.sub(r"\{\{<\s*glossary_tooltip\b[^>]*?\btext=\"([^\"]+)\"[^>]*>\}\}", r"\1", text)
    text = re.sub(
        r"\{\{<\s*feature-state\b[^>]*?\bfeature_gate_name=\"([^\"]+)\"[^>]*?\bstate=\"([^\"]+)\"[^>]*>\}\}",
        r"**Feature state:** \1 (\2)",
        text,
    )
    text = re.sub(
        r"\{\{<\s*feature-state\b[^>]*?\bstate=\"([^\"]+)\"[^>]*>\}\}",
        r"**Feature state:** \1",
        text,
    )
    text = re.sub(r"\{\{[<%]\s*tab\b[^>%]*?\bname=\"([^\"]+)\"[^>%]*[>%]\}\}", r"**\1**\n", text)
    text = re.sub(r"\{\{<\s*(?:caution|warning)\s*>\}\}", "**Warning:** ", text)
    text = re.sub(r"\{\{<\s*(?:note|tip)\s*>\}\}", "**Note:** ", text)
    text = re.sub(
        r"\{\{<\s*(?:image|command-group|university-links|relref|ref)\b[^>]*>\}\}", "", text
    )
    text = re.sub(
        r"\{\{[<%]\s*/?[\w-]+.*?[>%]\}\}", "", text
    )  # any remaining shortcode (incl. include-html with {version})
    text = _SUMMARY_RE.sub(lambda m: "**" + m.group(1).strip() + "**", text)
    text = re.sub(r"</?details\b[^>]*>", "", text, flags=re.I)
    text = convert_embedded_html(text, markdown_cells=True)
    text = fence_indented_code(text)
    text = strip_relative_links_and_images(text)
    text = re.sub(
        r"\b((?:[\w.]+ ){1,3}details)[,;]? \1\b", r"\1", text
    )  # source typo: duplicated link text
    return text


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def _guess_code_lang(lines: list[str]) -> str:
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    if first.startswith(("$ ", "> ")):
        return "bash"
    body = "\n".join(lines)
    if re.search(r"[;{}]\s*$", body, re.M) and re.search(
        r"\b(?:public|private|import|new|class|void|final|KStream|KTable|String|Properties)\b", body
    ):
        return "java"
    return ""


def fence_indented_code(text: str) -> str:
    """Markdown 4-space-indented code blocks -> fenced blocks. A block is a run of
    lines indented >= 4 spaces (whitespace-only lines allowed inside) that follows a
    blank line and does not directly follow a list item/indented text. Applied
    outside existing fences only."""

    def _fn(seg: str) -> str:
        lines = seg.split("\n")
        out: list[str] = []
        i, n = 0, len(lines)

        def blank(k: int) -> bool:
            return not lines[k].strip()

        while i < n:
            ln = lines[i]
            starts_block = (
                ln.strip()
                and (ln.startswith("    ") or ln.startswith("\t"))
                and (i == 0 or blank(i - 1))
            )
            if starts_block:
                # previous non-blank line must not be a list item / more-indented context
                k = i - 1
                while k >= 0 and blank(k):
                    k -= 1
                prev = lines[k] if k >= 0 else ""
                if prev and (_LIST_ITEM_RE.match(prev) or prev.startswith("    ")):
                    starts_block = False
            if not starts_block:
                out.append(ln)
                i += 1
                continue
            j = i
            last_code = i
            while j < n and (blank(j) or lines[j].startswith("    ") or lines[j].startswith("\t")):
                if not blank(j):
                    last_code = j
                j += 1
            block = [
                (b[4:] if b.startswith("    ") else b.lstrip("\t")) if b.strip() else ""
                for b in lines[i : last_code + 1]
            ]
            out.append("")  # ensure a blank line before the fence
            out.append("```" + _guess_code_lang(block))
            out.extend(block)
            out.append("```")
            i = last_code + 1
        return "\n".join(out)

    return _outside_fences(text, _fn)


# ---------------------------------------------------------------------------
# reStructuredText
# ---------------------------------------------------------------------------

_ADORN_CHARS = set("=-~^\"'`#*+:._<>")
_ADMONITIONS = {
    "note",
    "warning",
    "tip",
    "important",
    "caution",
    "danger",
    "hint",
    "attention",
    "seealso",
    "admonition",
    "error",
    "todo",
}
_DROP_DIRECTIVES = {
    "toctree",
    "index",
    "currentmodule",
    "module",
    "automodule",
    "autoclass",
    "autofunction",
    "automethod",
    "autodata",
    "include",
    "literalinclude",
    "image",
    "figure",
    "only",
    "rst-class",
    "tabularcolumns",
    "contents",
    "sectionauthor",
    "highlight",
    "default-role",
    "cssclass",
    "role",
    "raw",
    "meta",
    "title",
    "sectnum",
    "centered",
    "hlist",
    "testsetup",
    "testcleanup",
    "ifconfig",
    "code-author",
    "moduleauthor",
    "container",
    "tab",
    "tabs",
    "tabbed",
    "customcarddeck",
    "customcalloutitem",
    "customimagehistory",
    "cardinfo",
    "graphviz",
    "tikz",
    "plot",
    "jupyter-execute",
    "toctree-expanded",
    "autosummary",
    "glossary-todo",
    "rubric-skip",
}
_CODE_DIRECTIVES = {
    "code-block",
    "sourcecode",
    "code",
    "parsed-literal",
    "testcode",
    "doctest",
    "productionlist",
    "prompt",
}
_VERSION_DIRECTIVES = {
    "versionadded": "New in",
    "versionchanged": "Changed in",
    "deprecated": "Deprecated since",
    "versionremoved": "Removed in",
}
_CODEY_ROLES = {
    "class",
    "meth",
    "func",
    "attr",
    "mod",
    "setting",
    "option",
    "data",
    "exc",
    "const",
    "tfilter",
    "ttag",
    "lookup",
    "djadmin",
    "djadminopt",
    "py:class",
    "py:meth",
    "py:func",
    "py:attr",
    "py:mod",
    "obj",
    "envvar",
    "command",
    "file",
    "program",
    "samp",
    "kbd",
    "member",
    "mimetype",
    "pep",
}

_DIRECTIVE_RE = re.compile(r"^(\s*)\.\.\s+([A-Za-z][\w:-]*)::\s*(.*)$")
_TARGET_RE = re.compile(r"^\s*\.\.\s+(_[^:]+:|\[[^\]]+\]|\|[^|]+\|\s+\w+::).*$")
_COMMENT_RE = re.compile(r"^(\s*)\.\.(\s.*)?$")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _collect_body(lines: list[str], i: int, base_indent: int) -> tuple[list[str], int]:
    """Lines after index i that are indented deeper than base_indent (or blank
    lines followed by such lines). Returns (raw_body_lines, next_index)."""
    body: list[str] = []
    j = i
    while j < len(lines):
        ln = lines[j]
        if ln.strip() == "":
            k = j
            while k < len(lines) and lines[k].strip() == "":
                k += 1
            if k < len(lines) and _indent_of(lines[k]) > base_indent:
                body.extend(lines[j:k])
                j = k
                continue
            break
        if _indent_of(ln) > base_indent:
            body.append(ln)
            j += 1
        else:
            break
    return body, j


def _dedent(lines: list[str]) -> list[str]:
    nonblank = [ln for ln in lines if ln.strip()]
    if not nonblank:
        return [""] * len(lines)
    m = min(_indent_of(ln) for ln in nonblank)
    return [ln[m:] if ln.strip() else "" for ln in lines]


def _rst_inline(text: str) -> str:
    def _role(m: re.Match) -> str:
        role, content = m.group(1), m.group(2)
        label_m = re.match(r"^(.*?)\s*<([^>]+)>$", content, re.S)
        if label_m:
            shown = label_m.group(1).strip() or label_m.group(2)
        else:
            shown = content.strip()
            if shown.startswith("~"):
                shown = shown.lstrip("~").split(".")[-1]
            shown = shown.lstrip("!")
        shown = re.sub(r"\s+", " ", shown).strip()
        if role == "math":
            return f"${shown}$"
        if role in _CODEY_ROLES:
            return f"`{shown}`"
        return shown

    text = re.sub(r":([A-Za-z][\w:]*):`([^`]+)`", _role, text)
    text = re.sub(r"``([^`]+)``", r"`\1`", text)
    text = re.sub(r"`([^`<]+?)\s*<[^>`]+>`_{1,2}", r"\1", text)  # `text <url>`_
    text = re.sub(r"(?<![`\w])`([^`]+)`_{1,2}(?!\w)", r"\1", text)  # `text`_
    text = re.sub(r"\[\d+\]_", "", text)
    text = re.sub(
        r"(?<![\w`])([A-Z][A-Za-z0-9-]*)_(?=[\s.,;:)]|$)", r"\1", text
    )  # named hyperlink refs: Python_
    text = re.sub(r"[ ]?\|[A-Za-z][\w-]*\|", "", text)  # substitution refs like |edit|
    return text


_GRID_BORDER_RE = re.compile(r"^\s*\+[-=:+]+\+\s*$")


def _grid_table_to_md(block: list[str]) -> list[str]:
    """reST grid table -> markdown pipe table (multi-line cells joined with spaces)."""
    borders = [i for i, ln in enumerate(block) if _GRID_BORDER_RE.match(ln)]
    if len(borders) < 2:
        return block
    cols = [i for i, ch in enumerate(block[borders[0]].rstrip()) if ch == "+"]
    rows: list[list[str]] = []
    header_end = None
    for a, b in zip(borders, borders[1:], strict=False):
        cells = [""] * (len(cols) - 1)
        for ln in block[a + 1 : b]:
            for ci in range(len(cols) - 1):
                seg = ln[cols[ci] + 1 : cols[ci + 1]] if len(ln) > cols[ci] else ""
                cells[ci] = (cells[ci] + " " + seg.strip()).strip()
        rows.append([c.replace("|", "\\|") for c in cells])
        if "=" in block[b] and header_end is None:
            header_end = len(rows)
    if not rows:
        return block
    head = rows[0]
    body = rows[1:]
    out = ["| " + " | ".join(head) + " |", "| " + " | ".join(["---"] * len(head)) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return out


def _defang_fences(content: list[str]) -> list[str]:
    """A code sample that itself contains a ``` / ~~~ line would close our fence early."""
    return [("\u200b" + ln) if re.match(r"^\s*(```|~~~)", ln) else ln for ln in content]


def _is_adorn(s: str) -> bool:
    st = s.strip()
    return (
        len(st) >= 3 and st[0] in _ADORN_CHARS and st == st[0] * len(st) and not s.startswith(" ")
    )


def _looks_like_title(title: str) -> bool:
    t = title.strip()
    if t[:1] in ")]}" or re.fullmatch(r"[\W_]+", t):
        return False
    # short titles such as "C++", "Q&A", "I/O" have no run of three letters
    return bool(re.search(r"[A-Za-z]{3}", t)) or (
        len(t) <= 20 and bool(re.search(r"[A-Za-z]", t)) and bool(re.fullmatch(r"[\w+&#/ .-]+", t))
    )


_CODE_PUNCT_RE = re.compile(r"[=(){}\[\]\\\"`;]")


def _underline_ok(title: str, underline: str, following: str) -> bool:
    """Underline must be >= title length. docutils also tolerates a *short* underline
    (>= 4 chars) — real docs have them — but only for prose-looking titles followed by
    a blank line, never for code-looking lines."""
    t, u = title.strip(), underline.strip()
    if len(u) >= len(t):
        return True
    return len(u) >= 4 and not _CODE_PUNCT_RE.search(t) and len(t) <= 120


def _convert_lines(
    lines: list[str], levels: dict[tuple[str, bool], int], allow_headings: bool = True
) -> list[str]:
    out: list[str] = []
    i, n = 0, len(lines)

    def emit_heading(title: str, style: tuple[str, bool]) -> None:
        if style not in levels:
            levels[style] = len(levels) + 1
        out.extend(["", "#" * min(levels[style], 6) + " " + title.strip(), ""])

    def prev_blank() -> bool:
        return not out or not out[-1].strip()

    while i < n:
        ln = lines[i]

        if allow_headings:
            # overline + title + underline
            if (
                _is_adorn(ln)
                and i + 2 < n
                and lines[i + 1].strip()
                and not _is_adorn(lines[i + 1])
                and _is_adorn(lines[i + 2])
                and ln.strip()[0] == lines[i + 2].strip()[0]
                and _looks_like_title(lines[i + 1])
            ):
                emit_heading(lines[i + 1], (ln.strip()[0], True))
                i += 3
                continue
            # title + underline: underline >= title length, title is real text,
            # separated from the previous paragraph by a blank line
            if (
                ln.strip()
                and not _is_adorn(ln)
                and not ln.startswith(" ")
                and i + 1 < n
                and _is_adorn(lines[i + 1])
                and _underline_ok(ln, lines[i + 1], lines[i + 2] if i + 2 < n else "")
                and not ln.lstrip().startswith("..")
                and _looks_like_title(ln)
                and prev_blank()
            ):
                emit_heading(ln, (lines[i + 1].strip()[0], False))
                i += 2
                continue

        if _GRID_BORDER_RE.match(ln):
            j = i
            while j < n and (
                lines[j].lstrip().startswith("+") or lines[j].lstrip().startswith("|")
            ):
                j += 1
            out.append("")
            out.extend(_grid_table_to_md(lines[i:j]))
            out.append("")
            i = j
            continue

        dm = _DIRECTIVE_RE.match(ln)
        if dm:
            indent, name, arg = len(dm.group(1)), dm.group(2).lower(), dm.group(3).strip()
            body_raw, j = _collect_body(lines, i + 1, indent)
            k = 0
            while k < len(body_raw) and re.match(r"^\s*:[\w-]+:(\s|$)", body_raw[k]):
                k += 1
            content = _dedent(body_raw[k:])
            while content and not content[0].strip():
                content = content[1:]
            while content and not content[-1].strip():
                content = content[:-1]
            if name in _CODE_DIRECTIVES or name == "mermaid":
                lang = (
                    arg.split()[0] if arg and name in ("code-block", "sourcecode", "code") else ""
                )
                lang = {
                    "pycon": "python",
                    "ipython": "python",
                    "console": "bash",
                    "doscon": "",
                    "django": "html",
                }.get(lang, lang)
                if name == "mermaid":
                    lang = "mermaid"
                out.extend(["", f"```{lang}", *_defang_fences(content), "```", ""])
            elif name in _VERSION_DIRECTIVES:
                label = _VERSION_DIRECTIVES[name]
                conv = _convert_lines(content, levels, allow_headings=False)
                while conv and not conv[0].strip():
                    conv = conv[1:]
                note = f"*{label} version {arg}.*"
                out.append("")
                if conv and not conv[0].startswith("```"):
                    out.append(f"{note} {conv[0]}".rstrip())
                    out.extend(conv[1:])
                else:
                    out.append(note)
                    out.extend(conv)
                out.append("")
            elif name in _ADMONITIONS:
                label = (
                    arg
                    if (name == "admonition" and arg)
                    else ("Note" if name == "admonition" else name.capitalize())
                )
                combined = ([arg] if (arg and name != "admonition") else []) + content
                conv = _convert_lines(combined, levels, allow_headings=False)
                while conv and not conv[0].strip():
                    conv = conv[1:]
                out.append("")
                if conv and not conv[0].startswith("```"):
                    out.append(f"**{label}:** {conv[0]}".rstrip())
                    out.extend(conv[1:])
                else:
                    out.append(f"**{label}:**")
                    out.extend(conv)
                out.append("")
            elif name in _DROP_DIRECTIVES:
                pass
            else:
                # descriptive / container directive (class::, method::, setting::, grid-item-card::,
                # tab-item::, ...)
                out.append("")
                if arg:
                    out.extend([f"**{arg}**", ""])
                out.extend(_convert_lines(content, levels, allow_headings=False))
                out.append("")
            i = j
            continue

        if _TARGET_RE.match(ln):
            i += 1
            continue
        cm = _COMMENT_RE.match(ln)
        if cm and not ln.strip().startswith(".. _"):
            _, j = _collect_body(lines, i + 1, len(cm.group(1)))
            i = j
            continue

        # literal block introduced by "::"
        if ln.rstrip().endswith("::") and not ln.lstrip().startswith(".."):
            stripped = ln.rstrip()
            if stripped.strip() == "::":
                intro = ""
            elif stripped.endswith(" ::"):
                intro = stripped[:-3].rstrip()
            else:
                intro = stripped[:-1]
            body_raw, j = _collect_body(lines, i + 1, _indent_of(ln))
            if body_raw and any(b.strip() for b in body_raw):
                if intro:
                    out.append(intro)
                content = _dedent(body_raw)
                while content and not content[0].strip():
                    content = content[1:]
                while content and not content[-1].strip():
                    content = content[:-1]
                lang = (
                    "python"
                    if any(
                        c.lstrip().startswith((">>>", "def ", "import ", "from ", "class "))
                        for c in content
                    )
                    else ""
                )
                out.extend(["", f"```{lang}", *_defang_fences(content), "```", ""])
                i = j
                continue
            out.append(intro or stripped)
            i += 1
            continue

        if ln.lstrip().startswith(">>> ") and (i == 0 or not lines[i - 1].strip()):
            j = i
            while j < n and lines[j].strip():
                j += 1
            block = _dedent(lines[i:j])
            out.extend(["", "```python", *block, "```", ""])
            i = j
            continue

        out.append(ln)
        i += 1
    return out


def _drop_stray_adornments(seg: str) -> str:
    """Solid adornment lines (`~~~~`, `----`, `````) that were not consumed as heading
    underlines would otherwise act as stray fence markers / setext headings."""
    return re.sub(r"^[ \t]*([~`=\-^\"#*+.:'<>_])\1{3,}[ \t]*$\n?", "", seg, flags=re.M)


def _drop_stray_adornments_outside_backtick_fences(text: str) -> str:
    """Like `_outside_fences(text, _drop_stray_adornments)`, but only ``` fences count:
    at this stage the only fences are the ones we generated, and a `~~~~~~` title
    underline must not be mistaken for a `~~~` fence opener (it would then survive
    and flip fence parity for the rest of the document)."""
    out: list[str] = []
    buf: list[str] = []
    in_fence = False
    for ln in text.split("\n"):
        if re.match(r"^\s*```", ln):
            if not in_fence and buf:
                out.append(_drop_stray_adornments("\n".join(buf)))
                buf = []
            in_fence = not in_fence
            out.append(ln)
            continue
        if in_fence:
            out.append(ln)
        else:
            buf.append(ln)
    if buf:
        out.append(_drop_stray_adornments("\n".join(buf)))
    return "\n".join(out)


def rst_to_markdown(text: str) -> str:
    lines = [ln.rstrip() for ln in text.expandtabs(4).split("\n")]
    out = _convert_lines(lines, {}, allow_headings=True)
    text_out = _drop_stray_adornments_outside_backtick_fences("\n".join(out))
    text_out = _outside_fences(
        text_out, _rst_inline
    )  # roles may span lines -> inline pass on whole text
    return re.sub(r"\n{3,}", "\n\n", text_out).strip() + "\n"


def _uncomment_docstring_sections(doc: list[str]) -> list[str]:
    """Some tutorials put gallery-style `#####` / `# Title` / `# -----` blocks inside the
    module docstring itself, where they are literal text. Turn them back into reST
    titles (and drop the bare `#####` separator lines)."""
    out: list[str] = []
    i, n = 0, len(doc)
    while i < n:
        ln = doc[i]
        if re.match(r"^#{6,}\s*$", ln):
            i += 1
            continue
        m = re.match(r"^#[ \t]?(\S.*)$", ln)
        if m and i + 1 < n and re.match(r"^#[ \t]?([-=~^*])\1{2,}\s*$", doc[i + 1]):
            title = m.group(1).strip()
            out.extend(
                ["", title, re.sub(r"^#[ \t]?", "", doc[i + 1]).strip()[0] * max(len(title), 4)]
            )
            i += 2
            continue
        out.append(ln)
        i += 1
    return out


def sphinx_gallery_to_rst(py_text: str) -> str:
    """sphinx-gallery .py tutorial -> reST text with code-block directives:
    module docstring = prose; `# %%` / `####` comment blocks = prose; the rest = code."""
    lines = py_text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    # skip shebang/encoding/license preamble comments
    while i < n and (
        lines[i].startswith("#!") or lines[i].startswith("# -*-") or lines[i].strip() == ""
    ):
        i += 1
    m = re.match(r'^[rR]?("""|\'\'\')(.*)$', lines[i]) if i < n else None
    if m:
        q = m.group(1)
        first = m.group(2)
        doc: list[str] = []
        if first.strip().endswith(q) and len(first.strip()) >= 3:
            doc.append(first.strip()[:-3])
            i += 1
        else:
            if first.strip():
                doc.append(first)
            i += 1
            while i < n and q not in lines[i]:
                doc.append(lines[i])
                i += 1
            if i < n:
                doc.append(lines[i].split(q)[0])
                i += 1
        out.extend(_uncomment_docstring_sections(doc))
        out.append("")
    code: list[str] = []

    def flush_code() -> None:
        nonlocal code
        while code and not code[0].strip():
            code = code[1:]
        while code and not code[-1].strip():
            code = code[:-1]
        if code:
            out.append(".. code-block:: python")
            out.append("")
            out.extend("    " + c if c.strip() else "" for c in code)
            out.append("")
        code = []

    while i < n:
        ln = lines[i]
        if re.match(r"^#\s*%%", ln) or re.match(r"^#{6,}\s*$", ln):
            flush_code()
            i += 1
            if re.match(r"^#\s*%%\s*$", ln) is None and re.match(r"^#\s*%%\s*\S", ln):
                out.append(re.sub(r"^#\s*%%\s*", "", ln))
            while i < n and (lines[i].startswith("#") and not re.match(r"^#\s*%%", lines[i])):
                if re.match(r"^#{6,}\s*$", lines[i]):
                    break
                out.append(re.sub(r"^#\s?", "", lines[i]))
                i += 1
            out.append("")
            continue
        code.append(ln)
        i += 1
    flush_code()
    return "\n".join(out)


_INVISIBLE_BLOCK_RE = re.compile(
    r"^[^\n]*INVISIBLE_CODE_BLOCK[^\n]*\n.*?^[^\n]*INVISIBLE_CODE_BLOCK[^\n]*(?:\n|$)", re.S | re.M
)


def profile_rst(text: str, path: str = "") -> str:
    if path.endswith((".md", ".mdx", ".markdown")):  # markdown pages inside an rst-profile source
        return profile_markdown(text)
    text = _INVISIBLE_BLOCK_RE.sub(
        "", text
    )  # pytorch/tutorials: code that is executed but hidden in the rendered page
    # upstream typo "```name``" at the start of a line (inline literal with an extra backtick)
    text = re.sub(r"^([ \t]*)```([^`\n]+)``(?!`)", r"\1``\2``", text, flags=re.M)
    if path.endswith(".py"):
        text = sphinx_gallery_to_rst(text)
    return rst_to_markdown(text)


_CJK_BULLET_RE = re.compile(
    r"^[ \t]*[-*][ \t]+[^\w\n]*[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af][^\n]*$\n?", re.M
)


def profile_html(text: str, path: str = "", options: dict | None = None) -> str:
    """A source file that *is* HTML (e.g. Kafka's generated config fragments in
    apache/kafka-site): converted as a whole document. A title heading derived from
    the file name (+ optional `options.title_prefix`) is prepended when the
    converted text has no leading heading."""
    meta = re.match(r"\s*<!--\{(.*?)\}-->", text, re.S)  # golang/website: <!--{ "Title": "..." }-->
    page_title = ""
    if meta:
        tm = re.search(r'"Title":\s*"([^"]+)"', meta.group(1))
        page_title = tm.group(1).strip() if tm else ""
        text = text[meta.end() :]
    # golang/website templates: {{raw `...`}} wraps a verbatim code sample; other actions are noise
    text = re.sub(
        r"\{\{raw `(.*?)`\}\}",
        lambda m: "\n```\n" + textwrap.dedent(m.group(1)).strip("\n") + "\n```\n",
        text,
        flags=re.S,
    )
    text = re.sub(r"\{\{(?:code|play|image|pkg|link|ref)\b[^}]*\}\}", "", text)
    if path.endswith((".md", ".mdx")):  # markdown pages in an html-profile source
        body = profile_markdown(text)
        if page_title and not re.match(r"\s*#{1,2}\s", body):
            body = f"# {page_title}\n\n{body.lstrip()}"
        return body
    if not text.lstrip().startswith("<"):
        text = _CJK_BULLET_RE.sub("", text)  # translated "see also" links (English-only corpus)
        return convert_embedded_html(text)
    md, _ = html_to_markdown(text)
    if not re.match(r"\s*#{1,2}\s", md):
        raw_stem = re.sub(r"\.html?$", "", path.rsplit("/", 1)[-1])
        title = page_title or ((options or {}).get("titles") or {}).get(raw_stem)
        if not title:
            stem = raw_stem.replace("_", " ").replace("-", " ").strip().title()
            title = f"{(options or {}).get('title_prefix', '')}{stem}"
        md = f"# {title}\n\n{md}"
    return md


# ---------------------------------------------------------------------------
# mdn  (mdn/content: markdown + {{macros}} + frontmatter title, no H1 in the body)
# ---------------------------------------------------------------------------

_MDN_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_MDN_MACRO_RE = re.compile(
    r"\{\{\s*([A-Za-z_][\w:-]*)\s*(?:\(((?:[^()]|\([^()]*\))*)\))?\s*\}\}", re.S
)
_MDN_STR_ARG_RE = re.compile(r"""\s*(?:"([^"]*)"|'([^']*)'|([^,\s]+))\s*(?:,|$)""")
# macro name (lower case) -> index of the argument that is the visible text (-1 = last one)
_MDN_NAME_MACROS = {
    "domxref",
    "jsxref",
    "cssxref",
    "httpheader",
    "httpmethod",
    "httpstatus",
    "glossary",
    "event",
    "svgelement",
    "svgattr",
    "htmlattrxref",
    "domeventxref",
    "htmlelement",
    "cssinfo",
    "webextapiref",
    "jsapixref",
}
_MDN_SECTION_DROP = {"specifications", "browser compatibility", "see also", "try it"}
_MDN_FENCE_INFO_RE = re.compile(r"^([ \t]*(?:```|~~~))([\w+#.-]*)[^\n]*$", re.M)
_MDN_CALLOUT_RE = re.compile(r"^>\s*\[!(NOTE|WARNING|CALLOUT|TIP|IMPORTANT|CAUTION)\][ \t]*$", re.M)


def _mdn_macro(m: re.Match) -> str:
    name = m.group(1).lower()
    if name == "rfc":
        nums = re.findall(r"\d+", m.group(2) or "")
        return f"RFC {nums[0]}" if nums else ""
    if name not in _MDN_NAME_MACROS:
        return ""
    args = [
        next(g for g in a.groups() if g is not None)
        for a in _MDN_STR_ARG_RE.finditer(m.group(2) or "")
    ]
    if not args:
        return ""
    if name == "htmlelement":
        return args[1] if len(args) >= 2 and args[1] else f"`<{args[0]}>`"
    if name == "glossary" and len(args) >= 2:
        return args[1]
    if name in ("domxref", "jsxref", "cssxref", "httpheader", "event", "domeventxref"):
        return args[1] if len(args) >= 2 and args[1] and not args[1].startswith("#") else args[0]
    return args[0]


def _mdn_drop_sections(text: str) -> str:
    """Removes whole sections whose heading is in _MDN_SECTION_DROP (link lists, empty
    macro-only sections), up to the next heading of the same or a higher level."""
    out: list[str] = []
    skip_level = 0
    for ln in text.split("\n"):
        m = re.match(r"^(#{1,6})[ \t]+(.*?)[ \t]*$", ln)
        if m:
            level = len(m.group(1))
            if skip_level and level <= skip_level:
                skip_level = 0
            if not skip_level and re.sub(r"\W+", " ", m.group(2)).strip().lower() in (
                _MDN_SECTION_DROP
            ):
                skip_level = level
                continue
        if not skip_level:
            out.append(ln)
    return "\n".join(out)


def profile_mdn(text: str) -> str:
    title = ""
    fm = _MDN_FM_RE.match(text)
    if fm:
        tm = re.search(r"^title:\s*(.+?)\s*$", fm.group(1), re.M)
        if tm:
            title = tm.group(1).strip().strip("\"'")
        text = text[fm.end() :]
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(  # inline MathML: keep the TeX annotation
        r"<math\b[^>]*>.*?<annotation encoding=\"TeX\">(.*?)</annotation>.*?</math>",
        lambda m: f"${m.group(1).strip()}$",
        text,
        flags=re.S,
    )

    def _prose(seg: str) -> str:
        seg = _MDN_MACRO_RE.sub(_mdn_macro, seg)
        seg = re.sub(r"``([^`\n]+)``", r"`\1`", seg)  # macro that was already in backticks
        seg = _MDN_CALLOUT_RE.sub(lambda m: "> **" + m.group(1).title() + ":**", seg)
        return seg

    text = _outside_fences(text, _prose)
    text = _MDN_FENCE_INFO_RE.sub(lambda m: m.group(1) + m.group(2), text)
    text = profile_markdown(text)
    text = strip_relative_links_and_images(text)
    text = _outside_fences(text, _mdn_drop_sections)
    if title and not re.match(r"\s*#\s", text):
        text = f"# {title}\n\n{text.lstrip()}"
    return text


# ---------------------------------------------------------------------------
# asciidoc  (Spring Framework / Spring Boot / Debezium ... Antora pages)
# ---------------------------------------------------------------------------

_ADOC_ADMON = {
    "NOTE": "Note",
    "TIP": "Tip",
    "IMPORTANT": "Important",
    "WARNING": "Warning",
    "CAUTION": "Caution",
}
_ADOC_ADMON_LINE_RE = re.compile(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):[ \t]+(.*)$")
_ADOC_HEADING_RE = re.compile(r"^(={1,6})[ \t]+(\S.*?)(?:[ \t]+=+)?$")
_ADOC_ATTR_RE = re.compile(r"^:[\w!-]+!?:.*$")
_ADOC_DELIM_RE = re.compile(r"^(-{4,}|\.{4,}|={4,}|\*{4,}|_{4,}|\+{4,}|/{4,}|--)$")
_ADOC_BLOCKATTR_RE = re.compile(r"^\[([^\]]*)\]$")
_ADOC_LISTITEM_RE = re.compile(r"^([ \t]*)(\*{1,5}|-|\.{1,5})[ \t]+(\S.*)$")
_ADOC_DESC_RE = re.compile(r"^([^\s:|`][^:|`]*?)(:{2,4})(?:[ \t]+(.*))?$")
_ADOC_CODE_STYLES = {"source", "listing", "literal"}


def _adoc_inline(t: str) -> str:
    t = re.sub(
        r"\bimage:+[^\s\[]+\[([^\]]*)\]",
        lambda m: f"*[figure: {m.group(1)}]*" if len(m.group(1)) > 12 else "",
        t,
    )
    t = re.sub(
        r"\b(?:link|xref):+([^\s\[]+)\[([^\]]*)\]",
        lambda m: (
            (m.group(2) or m.group(1))
            if not m.group(1).startswith(("http", "mailto"))
            else (f"[{m.group(2) or m.group(1)}]({m.group(1)})")
        ),
        t,
    )
    t = re.sub(
        r"(?<![\w/\"'(])(https?://[^\s\[\]<>]+)\[([^\]]*)\]",
        lambda m: f"[{m.group(2) or m.group(1)}]({m.group(1)})",
        t,
    )
    t = re.sub(r"\[\[[\w:.-]+\]\]", "", t)  # inline anchors
    t = re.sub(r"`\+([^+`\n]+)\+`", r"`\1`", t)  # `+literal+`
    t = re.sub(r"<<([^,>]+),([^>]+)>>", r"\2", t)
    t = re.sub(r"<<([^>]+)>>", lambda m: m.group(1).replace("-", " ").replace("_", " "), t)
    t = re.sub(r"\bfootnote:\[[^\]]*\]", "", t)
    t = re.sub(r"\[\.[\w. -]+\]#([^#\n]+)#", r"\1", t)  # [.small]#text# role spans
    t = re.sub(r"\b(?:kbd|btn|menu):\[([^\]]*)\]", r"`\1`", t)
    t = re.sub(r"(?<![\w*])\*([^*\s][^*\n]*?)\*(?![\w*])", r"**\1**", t)
    t = re.sub(r"\+\+\+?([^+\n]+?)\+\+\+?", r"`\1`", t)
    # unresolved attribute references `{spring-boot-version}`; literal `{id}` in code spans stays
    parts = re.split(r"(`[^`\n]*`)", t)
    parts = [
        p if p.startswith("`") else re.sub(r"(?<![\w{])\{[a-z][\w.-]*\}", "", p) for p in parts
    ]
    return "".join(parts)


def adoc_to_markdown(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    pending_style = ""  # last [style,lang] block attribute line
    pending_title = ""
    pending_cols = 0
    while i < n:
        ln = lines[i]
        s = ln.rstrip()
        st = s.strip()
        # comments
        if st.startswith("////"):
            i += 1
            while i < n and not lines[i].strip().startswith("////"):
                i += 1
            i += 1
            continue
        if st.startswith("//") and not st.startswith("///"):
            i += 1
            continue
        # conditionals / includes / toc / anchors
        if re.match(r"^(ifn?def|ifeval|endif)::", st) or re.match(
            r"^(include|include-code|toc|include-partial)::", st
        ):
            i += 1
            continue
        if (
            re.match(r"^\[\[[^\]]+\]\]$", st)
            or re.match(r"^\[#[^\]]+\]$", st)
            or st in ("[discrete]", "[%collapsible]")
        ):
            i += 1
            continue
        if _ADOC_ATTR_RE.match(st):
            i += 1
            continue
        if re.match(r"^image::[^\[]+\[([^\]]*)\]", st):
            alt = re.match(r"^image::[^\[]+\[([^\]]*)\]", st).group(1).split(",")[0]
            if len(alt) > 12:
                out.append(f"*[figure: {alt}]*")
            i += 1
            continue
        m = _ADOC_BLOCKATTR_RE.match(st)
        if m and not st.startswith("[["):
            attrs = m.group(1)
            style = attrs.split(",")[0].strip().lower()
            cm = re.search(r'cols="?([^",\]]+(?:,[^",\]]+)*)"?', attrs)
            if cm and "cols=" in attrs:
                spec = cm.group(1)
                mm = re.match(r"^(\d+)\*", spec)
                pending_cols = (
                    int(mm.group(1)) if mm else len([c for c in spec.split(",") if c.strip()])
                )
            if re.match(r"^(source|listing|literal)\b", style) or style.startswith(","):
                parts = [p.strip() for p in attrs.split(",")]
                lang = (
                    parts[1]
                    if len(parts) > 1 and parts[0].lower() == "source"
                    else (parts[0].lstrip(",") if parts and parts[0].startswith(",") else "")
                )
                pending_style = "code:" + re.sub(r"[^\w+#.-]", "", lang or "")
            elif style.upper() in _ADOC_ADMON:
                pending_style = "admon:" + _ADOC_ADMON[style.upper()]
            elif style in ("quote", "verse", "sidebar", "example", "abstract"):
                pending_style = "plain"
            elif style.startswith("%") or style in ("tabs", "cols") or "=" in attrs:
                pass
            else:
                pending_style = pending_style
            i += 1
            continue
        # block title
        if re.match(r"^\.[A-Za-z0-9][^.\n]*$", st) and not re.match(r"^\.{1,5}[ \t]", s):
            pending_title = st[1:].strip()
            i += 1
            continue
        # delimited blocks
        if _ADOC_DELIM_RE.match(st) and st != "--" or st == "--":
            delim = st
            j = i + 1
            body: list[str] = []
            while j < n and lines[j].strip() != delim:
                body.append(lines[j])
                j += 1
            i = j + 1
            kind = pending_style
            pending_style = ""
            is_code = delim.startswith(("----", "....")) or kind.startswith("code:")
            if delim.startswith("++++") or delim.startswith("////"):
                continue
            if pending_title:
                out.extend(["", f"**{pending_title}**", ""])
                pending_title = ""
            if is_code:
                lang = kind.split(":", 1)[1] if kind.startswith("code:") else ""
                out.extend(["", f"```{lang}"] + [b.rstrip() for b in body] + ["```", ""])
            else:
                inner = adoc_to_markdown("\n".join(body))
                if kind.startswith("admon:"):
                    out.extend(
                        [
                            "",
                            f"**{kind.split(':', 1)[1]}:** "
                            + inner.strip().replace("\n\n", "\n\n"),
                            "",
                        ]
                    )
                else:
                    out.extend(["", inner.strip(), ""])
            continue
        # table
        if st.startswith("|==="):
            j = i + 1
            body = []
            while j < n and not lines[j].strip().startswith("|==="):
                body.append(lines[j])
                j += 1
            i = j + 1
            cells: list[str] = []
            first_row_cells = 0
            seen_blank_after_first = False
            for b in body:
                bs = b.rstrip()
                if not bs.strip():
                    if cells and first_row_cells == 0:
                        first_row_cells = len(cells)
                    continue
                if bs.lstrip().startswith("|"):
                    parts = bs.lstrip()[1:].split(" |") if bs.count("|") > 1 else [bs.lstrip()[1:]]
                    for p in parts:
                        cells.append(p.strip().lstrip("|").strip())
                    if (
                        first_row_cells == 0
                        and len(bs.split("|")) > 2
                        and not seen_blank_after_first
                    ):
                        first_row_cells = len(cells)
                elif cells:
                    cells[-1] = (cells[-1] + " " + bs.strip()).strip()
            ncols = pending_cols or first_row_cells or 1
            pending_cols = 0
            if cells:
                rows = [cells[k : k + ncols] for k in range(0, len(cells), ncols)]
                if pending_title:
                    out.extend(["", f"**{pending_title}**"])
                    pending_title = ""
                out.append("")
                for ri, row in enumerate(rows):
                    row = row + [""] * (ncols - len(row))
                    out.append(
                        "| " + " | ".join(_adoc_inline(c).replace("|", "\\|") for c in row) + " |"
                    )
                    if ri == 0:
                        out.append("|" + " --- |" * ncols)
                out.append("")
            continue
        # headings
        m = _ADOC_HEADING_RE.match(st)
        if m:
            out.extend(["", "#" * len(m.group(1)) + " " + _adoc_inline(m.group(2)), ""])
            pending_style = ""
            i += 1
            continue
        # admonition paragraph
        m = _ADOC_ADMON_LINE_RE.match(st)
        if m:
            out.append(f"**{_ADOC_ADMON[m.group(1)]}:** {_adoc_inline(m.group(2))}")
            i += 1
            continue
        # literal paragraph under a code style
        if pending_style.startswith("code:") and st:
            j = i
            body = []
            while j < n and lines[j].strip():
                body.append(lines[j])
                j += 1
            lang = pending_style.split(":", 1)[1]
            out.extend(["", f"```{lang}"] + [b.rstrip() for b in body] + ["```", ""])
            pending_style = ""
            i = j
            continue
        if pending_style.startswith("admon:") and st:
            out.append(f"**{pending_style.split(':', 1)[1]}:** " + _adoc_inline(st))
            pending_style = ""
            i += 1
            continue
        # lists
        m = _ADOC_LISTITEM_RE.match(s)
        if m:
            marker, body_t = m.group(2), m.group(3)
            depth = len(marker) - 1 if marker != "-" else 0
            if marker.startswith("."):
                out.append("   " * depth + "1. " + _adoc_inline(body_t))
            else:
                out.append("  " * depth + "- " + _adoc_inline(body_t))
            i += 1
            continue
        if st == "+":  # list continuation
            i += 1
            continue
        m = _ADOC_DESC_RE.match(st)
        if m and not st.startswith(("http", "|")) and len(m.group(1)) < 80:
            term, rest = m.group(1).strip(), (m.group(3) or "").strip()
            out.append(f"- **{_adoc_inline(term)}**" + (f": {_adoc_inline(rest)}" if rest else ""))
            i += 1
            continue
        if pending_title and st:
            out.extend(["", f"**{pending_title}**", ""])
            pending_title = ""
        out.append(_adoc_inline(s))
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n") + "\n"


def profile_asciidoc(text: str) -> str:
    return profile_markdown(adoc_to_markdown(text))


# ---------------------------------------------------------------------------
# msdocs  (Microsoft Learn markdown: `> [!NOTE]`, `:::image:::`, `[!INCLUDE]`, yml-driven pages)
# ---------------------------------------------------------------------------

_MS_IMAGE_RE = re.compile(r":::image\b([^\n]*?)(?::::)?[ \t]*$", re.M)
_MS_ALT_RE = re.compile(r'alt-text="([^"]*)"')
_MS_CALLOUT_RE = re.compile(
    r"^>[ \t]*\[!(NOTE|TIP|WARNING|IMPORTANT|CAUTION|ERROR|WARNING)\][ \t]*$", re.M
)
_MS_INCLUDE_RE = re.compile(r"^[ \t]*(?:>[ \t]*)?\[!INCLUDE\b[^\n]*$", re.M)
_MS_ROW_RE = re.compile(
    r"^[ \t]*:::(?:row|column|column-end|row-end|zone|zone-end)\b[^\n]*$",
    re.M,
)
_MS_VIDEO_RE = re.compile(r"^>[ \t]*\[!VIDEO\b[^\n]*$", re.M)


def profile_msdocs(text: str, path: str = "") -> str:
    fm = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    title = ""
    if fm:
        tm = re.search(r"^title:\s*(.+?)\s*$", fm.group(1), re.M)
        if tm:
            title = tm.group(1).strip().strip("\"'")
        text = text[fm.end() :]

    def _img(m: re.Match) -> str:
        a = _MS_ALT_RE.search(m.group(1))
        alt = a.group(1).strip() if a else ""
        return f"*[figure: {alt}]*" if len(alt) > 12 else ""

    def _prose(seg: str) -> str:
        seg = _MS_IMAGE_RE.sub(_img, seg)
        seg = _MS_INCLUDE_RE.sub("", seg)
        seg = _MS_VIDEO_RE.sub("", seg)
        seg = _MS_ROW_RE.sub("", seg)
        seg = re.sub(  # <ul><li>..</li></ul> inside pipe-table cells
            r"^\|.*$",
            lambda m: re.sub(
                r"</?(?:ul|ol)>|</li>",
                "",
                re.sub(r"<li>", "\u2022 ", re.sub(r"<br\s*/?>", "; ", m.group(0))),
            ),
            seg,
            flags=re.M,
        )
        seg = _MS_CALLOUT_RE.sub(lambda m: "> **" + m.group(1).title() + ":**", seg)
        return seg

    text = _outside_fences(text, _prose)
    text = profile_markdown(text)
    text = strip_relative_links_and_images(text)
    if not title and path.endswith("-content.md"):
        # pattern pages are `x.yml` wrappers that include `x-content.md`: the title lives in the yml
        stem = path.rsplit("/", 1)[-1][: -len("-content.md")].replace("-", " ")
        title = stem.title() + (" pattern" if "/patterns/" in path else "")
    if title and not re.search(r"^#\s", text, re.M):
        text = f"# {title}\n\n{text.lstrip()}"
    return text


# ---------------------------------------------------------------------------
# jekyll  (OpenSearch, Apache Spark docs: Liquid tags + kramdown attribute lists)
# ---------------------------------------------------------------------------

_LIQ_HIGHLIGHT_RE = re.compile(r"\{%-?\s*highlight\s+([\w+#-]*)[^%]*-?%\}", re.I)
_LIQ_ENDHIGHLIGHT_RE = re.compile(r"\{%-?\s*endhighlight\s*-?%\}", re.I)
_LIQ_BLOCK_DROP_RE = re.compile(
    r"\{%-?\s*(comment|raw)\s*-?%\}(.*?)\{%-?\s*end\1\s*-?%\}", re.S | re.I
)
_LIQ_TAG_RE = re.compile(r"\{%-?.*?-?%\}", re.S)
_LIQ_VAR_RE = re.compile(r"\{\{\s*[^{}\n]*?\s*\}\}")
_KRAMDOWN_ATTR_LINE_RE = re.compile(r"^[ \t]*\{:\s*[^}\n]*\}[ \t]*$\n?", re.M)
_KRAMDOWN_CALLOUT_RE = re.compile(
    r"^(?P<body>[^\n]+)\n\{:\s*\.(?P<kind>note|warning|tip|important)\s*\}", re.M
)


_GH_NAMES = {
    "prodname_actions": "GitHub Actions",
    "prodname_dotcom": "GitHub",
    "prodname_github": "GitHub",
    "github": "GitHub",
    "prodname_ghe_server": "GitHub Enterprise Server",
    "prodname_ghe_cloud": "GitHub Enterprise Cloud",
    "prodname_copilot": "GitHub Copilot",
    "prodname_actions_runner_controller": "Actions Runner Controller",
    "prodname_container_registry": "GitHub Container Registry",
    "prodname_registry": "GitHub Packages",
    "prodname_pages": "GitHub Pages",
    "prodname_codeql": "CodeQL",
    "prodname_dependabot": "Dependabot",
    "prodname_code_scanning": "code scanning",
    "prodname_secret_scanning": "secret scanning",
    "prodname_marketplace": "GitHub Marketplace",
    "prodname_vscode": "Visual Studio Code",
    "prodname_cli": "GitHub CLI",
    "prodname_dependabot_alerts": "Dependabot alerts",
    "prodname_github_app": "GitHub App",
    "prodname_oauth_app": "OAuth app",
    "prodname_actions_runner": "GitHub Actions runner",
}
_GH_DATA_RE = re.compile(r"\{%-?\s*data\s+variables\.[\w.]*?\.(\w+)\s*-?%\}")
_GH_ADMON_RE = re.compile(r"\{%-?\s*(note|warning|tip|important|danger|caution)\s*-?%\}", re.I)


def _liquid_github_names(seg: str) -> str:
    seg = _GH_DATA_RE.sub(lambda m: _GH_NAMES.get(m.group(1), m.group(1).replace("_", " ")), seg)
    return _GH_ADMON_RE.sub(lambda m: f"**{m.group(1).title()}:** ", seg)


def profile_jekyll(text: str) -> str:
    def _liq(seg: str) -> str:
        def _raw(m: re.Match) -> str:
            return m.group(2) if m.group(1).lower() == "raw" else ""

        seg = _LIQ_BLOCK_DROP_RE.sub(_raw, seg)
        seg = _KRAMDOWN_CALLOUT_RE.sub(
            lambda m: f"**{m.group('kind').title()}:** {m.group('body')}", seg
        )
        seg = _KRAMDOWN_ATTR_LINE_RE.sub("", seg)
        return seg

    text = re.sub(r"\{%-?\s*highlight\s+([\w+#-]*)[^%]*-?%\}", lambda m: f"```{m.group(1)}", text)
    text = _LIQ_ENDHIGHLIGHT_RE.sub("```", text)
    text = _outside_fences(text, _liq)
    text = _outside_fences(text, _liquid_github_names)
    text = _outside_fences(text, lambda s: _LIQ_VAR_RE.sub("", _LIQ_TAG_RE.sub("", s)))
    return profile_markdown(text)


# ---------------------------------------------------------------------------
# quarto  (Quarto / R Markdown books: OpenIntro IMS, Interpretable ML)
# ---------------------------------------------------------------------------

_QMD_CODE_CHUNK_RE = re.compile(r"^```\{[^}\n]*\}[^\n]*\n.*?^```[ \t]*$\n?", re.M | re.S)
_QMD_DIV_OPEN_RE = re.compile(r"^(:{3,})[ \t]*\{([^}\n]*)\}[ \t]*$")
_QMD_DIV_PLAIN_RE = re.compile(r"^:{3,}[ \t]*([A-Za-z][\w-]*)[ \t]*$")
_QMD_DIV_CLOSE_RE = re.compile(r"^:{3,}[ \t]*$")
_QMD_LABELS = {
    "callout-note": "Note",
    "callout-tip": "Tip",
    "callout-warning": "Warning",
    "callout-important": "Important",
    "callout-caution": "Caution",
    "workedexample": "Worked example",
    "guidedpractice": "Guided practice",
    "important": "Important",
    "data": "Data",
    "chapterintro": "",
    "pronunciation": "",
    "tip": "Tip",
    "exercises": "",
    "onebox": "",
    "caution": "Caution",
    "content-box": "",
}


def _qmd_divs(text: str) -> str:
    out: list[str] = []
    for ln in text.split("\n"):
        m = _QMD_DIV_OPEN_RE.match(ln)
        if m:
            attrs = m.group(2)
            cls = re.findall(r"\.([\w-]+)", attrs)
            title = re.search(r'title="([^"]*)"', attrs)
            label = next((_QMD_LABELS[c] for c in cls if c in _QMD_LABELS), "")
            if title:
                label = title.group(1)
            if label:
                out.extend(["", f"**{label}:**"])
            continue
        if _QMD_DIV_PLAIN_RE.match(ln) or _QMD_DIV_CLOSE_RE.match(ln):
            continue
        out.append(ln)
    return "\n".join(out)


def profile_quarto(text: str) -> str:
    text = re.sub(r"\A---\s*\n.*?\n---[ \t]*\n", "", text, count=1, flags=re.S)
    text = _QMD_CODE_CHUNK_RE.sub("", text)
    text = _outside_fences(text, _qmd_divs)
    text = _outside_fences(
        text,
        lambda s: re.sub(
            r"\\(?:index|vspace|clearpage|pagebreak|newpage|noindent|hfill)\b(?:\{[^}]*\})?",
            "",
            re.sub(
                r"`r [^`\n]*`",
                "",
                re.sub(
                    r"@(fig|tbl|eq|sec|lst|exr)-[\w-]+",
                    lambda m: {"fig": "the figure", "tbl": "the table", "eq": "the equation"}.get(
                        m.group(1), "the section"
                    ),
                    re.sub(r"\[-?@[\w:-]+(?:;\s*@[\w:-]+)*\]", "", s),
                ),
            ),
        ),
    )
    text = _outside_fences(
        text,
        lambda s: re.sub(
            r"\{[^{}\n]*(?:#fig-|#tbl-|#sec-|\.unnumbered|\.unlisted)[^{}\n]*\}", "", s
        ),
    )
    return profile_markdown(text)


def profile_notebook(text: str) -> str:
    """Jupyter-book style notebooks (markdown + code cells joined by the git loader): markdown
    cleanup plus relative image / link removal."""
    return strip_relative_links_and_images(profile_markdown(text))


# ---------------------------------------------------------------------------
# Writerside (Kotlin / Kotlin Multiplatform docs), DocC (Swift book), Eleventy (Flutter docs)
# ---------------------------------------------------------------------------

_WS_TITLE_RE = re.compile(r"\A\s*\[//\]: # \(title: (.*?)\)[ \t]*\n")
_WS_COMMENT_RE = re.compile(r"^\[//\]: # .*$\n?", re.M)
_WS_QUOTE_STYLE_RE = re.compile(
    r"((?:^[ \t]*>[^\n]*\n)+)[ \t]*\{[ \t]*style[ \t]*=[ \t]*\"(note|tip|warning)\""
    r"[ \t]*\}[ \t]*$\n?",
    re.M,
)
_WS_ATTR_RE = r"\{(?:[ \t]*[\w-]+[ \t]*=[ \t]*(?:\"[^\"]*\"|[^\s}]+))+[ \t]*\}"
_WS_ATTR_LINE_RE = re.compile(rf"^[ \t]*{_WS_ATTR_RE}[ \t]*$\n?", re.M)
_WS_ATTR_INLINE_RE = re.compile(rf"(?<=[)\w\]`*]){_WS_ATTR_RE}")
_WS_DROP_BLOCK_RE = re.compile(
    r"<(?:web-summary|seealso|list|panels|compare|video|snippet)\b[^>]*?(?:/>|>.*?</(?:web-summary|seealso|list|panels|compare|video|snippet)>)",
    re.S,
)
_WS_DROP_TAG_RE = re.compile(r"<(?:show-structure|no-index|include|anchor)\b[^>]*?/?>", re.S)
_WS_SHORTCUT_RE = re.compile(r"</?(?:shortcut|code|control|ui-path|math)>")
_WS_PROCEDURE_RE = re.compile(r"<procedure\b[^>]*?\btitle=\"([^\"]*)\"[^>]*>", re.S)
_WS_STEP_RE = re.compile(r"</?(?:procedure|step|chapter|category|topic)\b[^>]*>", re.S)


def _ws_quote(m: re.Match) -> str:
    lines = m.group(1).rstrip("\n").split("\n")
    while lines and lines[-1].strip() in (">", ""):
        lines.pop()
    if not lines:
        return ""
    first = re.sub(r"^([ \t]*>[ \t]?)", r"\1", lines[0])
    first = (
        first.replace(">", f"> **{m.group(2).title()}:**", 1)
        if first.lstrip().startswith(">")
        else first
    )
    return "\n".join([first, *lines[1:]]) + "\n"


def _writerside_prose(seg: str) -> str:
    seg = _WS_QUOTE_STYLE_RE.sub(_ws_quote, seg)
    seg = _WS_DROP_BLOCK_RE.sub("", seg)
    seg = _WS_DROP_TAG_RE.sub("", seg)
    seg = _WS_PROCEDURE_RE.sub(lambda m: f"**{m.group(1)}**\n", seg)
    seg = _WS_STEP_RE.sub("", seg)
    seg = _WS_SHORTCUT_RE.sub("", seg)
    return _WS_ATTR_INLINE_RE.sub("", _WS_ATTR_LINE_RE.sub("", seg))


def profile_writerside(text: str) -> str:
    m = _WS_TITLE_RE.match(text)
    if m:
        text = f"# {m.group(1).strip()}\n\n" + text[m.end() :]
    text = _WS_COMMENT_RE.sub("", text)
    text = _outside_fences(text, _park_inline_code(_writerside_prose))
    return profile_markdown(text)


_DOCC_LINK_RE = re.compile(r"<doc:([\w./-]+)(?:#([\w-]+))?>")


def _docc_link(m: re.Match) -> str:
    name = (m.group(2) or m.group(1).rsplit("/", 1)[-1]).replace("-", " ")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def profile_docc(text: str) -> str:
    text = _outside_fences(text, lambda s: _DOCC_LINK_RE.sub(_docc_link, s))
    text = re.sub(
        r"^[ \t]*@(?:Comment|Metadata|Options)\b[^\n]*\{[^}]*\}[ \t]*$\n?", "", text, flags=re.M
    )
    return profile_markdown(text)


_DASH_IMAGE_RE = re.compile(r"<DashImage\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)/?>", re.S)
_ELEVENTY_ATTR_RE = re.compile(r"\b(alt|caption)=\"([^\"]*)\"")
_ELEVENTY_DROP_RE = re.compile(r"</?(?:noscript|YouTubeEmbed|Video|font)\b[^>]*?/?>", re.S)
_CODE_EXCERPT_RE = re.compile(r"^[ \t]*<\?code-excerpt\b.*?\?>[ \t]*$\n?", re.M | re.S)
_REF_DEF_RE = re.compile(r"^[ \t]*\[[^\]\n]+\]:[ \t]+\S+[^\n]*$\n?", re.M)
_ELEVENTY_H_RE = re.compile(r"<h([1-6])>(.*?)</h\1>")
_ELEVENTY_NOTE_RE = re.compile(
    r"^[ \t]*:::(version-note|note|tip|warning|important|secondary)\b.*$", re.M
)


def _dash_image(m: re.Match) -> str:
    attrs = dict(_ELEVENTY_ATTR_RE.findall(m.group(1)))
    alt = (attrs.get("alt") or attrs.get("caption") or "").strip()
    return f"*[figure: {alt}]*\n" if len(alt) > 12 else ""


def profile_eleventy(text: str) -> str:
    text = _outside_fences(text, _park_inline_code(lambda s: _DASH_IMAGE_RE.sub(_dash_image, s)))
    text = _outside_fences(text, _park_inline_code(lambda s: _ELEVENTY_DROP_RE.sub("", s)))
    text = _outside_fences(text, _park_inline_code(lambda s: _ELEVENTY_H_RE.sub(r"**\2**", s)))
    text = _CODE_EXCERPT_RE.sub("", text)  # `<?code-excerpt "lib/main.dart (x)"?>` before a fence
    text = _outside_fences(
        text, lambda s: _REF_DEF_RE.sub("", s)
    )  # `[Name]: /path` link definitions
    text = _outside_fences(
        text,
        lambda s: _ELEVENTY_NOTE_RE.sub(
            lambda m: f"**{m.group(1).replace('-', ' ').title()}:**", s
        ),
    )
    return profile_jekyll(text)


# ---------------------------------------------------------------------------
# Doxygen-flavoured markdown (OpenCV tutorials, CMSIS pages)
# ---------------------------------------------------------------------------

_DOX_HEADING_ATTR_RE = re.compile(r"^(.+?)[ \t]*\{#[\w.-]+\}[ \t]*$", re.M)
_DOX_SETEXT_RE = re.compile(r"^(?!#)(\S[^\n]*)\n(={3,}|-{3,})[ \t]*$", re.M)
_DOX_MATH_BLOCK_RE = re.compile(r"[@\\]f\[(.*?)[@\\]f\]", re.S)
_DOX_MATH_INLINE_RE = re.compile(r"[@\\]f\$(.*?)[@\\]f\$", re.S)
_DOX_CODE_RE = re.compile(
    r"^([ \t]*)[@\\]code(?:\{\.?([\w+-]*)\})?[ \t]*\n(.*?)^[ \t]*[@\\]endcode[ \t]*$", re.S | re.M
)
_DOX_DROP_LINE_RE = re.compile(
    r"^[ \t]*[@\\](?:tableofcontents|prev_tutorial|next_tutorial|add_toc_entry|snippet|"
    r"snippetlineno|"
    r"include|includelineno|dontinclude|skip|until|line|subpage|ingroup|defgroup|anchor|cond|endcond|"
    r"add_toggle\w*|end_toggle|tabs|endtabs|tab_ctab|htmlonly|endhtmlonly|latexonly|endlatexonly|"
    r"image|dot|enddot|cite|file|mainpage|addtogroup|copydoc|ref_tutorial|youtube|htmlinclude)\b[^\n]*$\n?",
    re.M,
)
_DOX_REF_RE = re.compile(r"[@\\]ref[ \t]+([\w:#./-]+)(?:[ \t]+\"([^\"]*)\")?")
_DOX_LABEL_RE = re.compile(
    r"^([ \t]*)[@\\](note|warning|attention|remark|sa|see|todo)\b[ \t]*", re.M
)
_DOX_INLINE_RE = re.compile(r"[@\\](?:b|em|a)[ \t]+(\S+)")
_DOX_CODEWORD_RE = re.compile(r"[@\\](?:c|p)[ \t]+(\S+)")
_DOX_SECTION_RE = re.compile(
    r"^[ \t]*[@\\](page|section|subsection|subsubsection)[ \t]+[\w.-]+[ \t]+(.+)$", re.M
)
_DOX_NUMBERED_RE = re.compile(r"^([ \t]*)-#[ \t]+", re.M)
_DOX_PAR_RE = re.compile(r"^[ \t]*[@\\](?:par|brief)[ \t]*(.*)$", re.M)


def _dox_code(m: re.Match) -> str:
    lang = {"py": "python", "cpp": "cpp", "c": "c"}.get(m.group(2) or "", m.group(2) or "")
    body = textwrap.dedent(m.group(3)).strip("\n")
    return f"```{lang}\n{body}\n```"


def profile_doxygen(text: str) -> str:
    text = _DOX_CODE_RE.sub(_dox_code, text)
    text = _DOX_HEADING_ATTR_RE.sub(r"\1", text)
    text = _DOX_SETEXT_RE.sub(
        lambda m: ("# " if m.group(2)[0] == "=" else "## ") + m.group(1).strip(), text
    )
    math: list[str] = []

    def _park_math(m: re.Match, display: bool) -> str:
        body = " ".join(m.group(1).split())
        math.append(f"\n\n$$ {body} $$\n\n" if display else f"${body}$")
        return f"\x02M{len(math) - 1}\x02"

    text = _DOX_MATH_BLOCK_RE.sub(lambda m: _park_math(m, True), text)
    text = _DOX_MATH_INLINE_RE.sub(lambda m: _park_math(m, False), text)

    def _prose(seg: str) -> str:
        seg = _DOX_DROP_LINE_RE.sub("", seg)
        seg = _DOX_SECTION_RE.sub(
            lambda m: ("# " if m.group(1) == "page" else "## ") + m.group(2).strip(), seg
        )
        seg = _DOX_REF_RE.sub(lambda m: m.group(2) or m.group(1).split("_", 1)[-1], seg)
        seg = _DOX_LABEL_RE.sub(lambda m: f"{m.group(1)}**{m.group(2).title()}:** ", seg)
        seg = _DOX_PAR_RE.sub(
            lambda m: f"**{m.group(1).strip()}**" if m.group(1).strip() else "", seg
        )
        seg = _DOX_NUMBERED_RE.sub(r"\g<1>1. ", seg)
        seg = _DOX_INLINE_RE.sub(r"**\1**", seg)
        return _DOX_CODEWORD_RE.sub(r"`\1`", seg)

    text = _outside_fences(text, _park_inline_code(_prose))
    text = re.sub(r"\x02M(\d+)\x02", lambda m: math[int(m.group(1))], text)
    return profile_markdown(text)


# ---------------------------------------------------------------------------
# MkDocs Material (Ultralytics, ...): admonitions `!!! tip "Title"` and content tabs `=== "Python"`
# ---------------------------------------------------------------------------

_MKDOCS_OPEN_RE = re.compile(
    r"^(?P<ind>[ \t]*)(?:(?P<adm>!!!|\?\?\?\+?)[ \t]*(?P<kind>[\w-]+)"
    r"(?:[ \t]+\"(?P<title>[^\"]*)\")?"
    r"|===\+?[ \t]+\"(?P<tab>[^\"]*)\")[ \t]*$"
)


def _mkdocs_blocks(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    in_fence = False
    while i < n:
        ln = lines[i]
        if _FENCE_LINE.match(ln):
            in_fence = not in_fence
            out.append(ln)
            i += 1
            continue
        m = None if in_fence else _MKDOCS_OPEN_RE.match(ln)
        if not m:
            out.append(ln)
            i += 1
            continue
        base = len(m.group("ind").expandtabs(4))
        j = i + 1
        body: list[str] = []
        while j < n:
            cur = lines[j]
            if cur.strip() == "":
                body.append("")
                j += 1
                continue
            if len(cur.expandtabs(4)) - len(cur.expandtabs(4).lstrip()) >= base + 4:
                body.append(cur.expandtabs(4)[base + 4 :])
                j += 1
                continue
            break
        while body and body[-1] == "":
            body.pop()
        label = m.group("tab") or m.group("title") or (m.group("kind") or "").title()
        head = f"**{label}**" if m.group("tab") else f"**{label}:**"
        out.append(m.group("ind") + head)
        out.append("")
        out.extend(
            (m.group("ind") + b) if b else "" for b in _mkdocs_blocks("\n".join(body)).split("\n")
        )
        out.append("")
        i = j
    return "\n".join(out)


_JINJA_LINE_RE = re.compile(
    r"^[ \t]*\{%-?[ \t]*(?:include|from|import|set|if|endif|for|endfor|macro|endmacro)\b"
    r".*?%\}[ \t]*$\n?",
    re.M,
)


_CANVAS_RE = re.compile(r"<canvas\b[^>]*>(?:</canvas>)?[ \t]*\n?", re.I)


_MKDOCSTRINGS_BLOCK_RE = re.compile(
    r"^:::[ \t]*[\w.]+[ \t]*\n(?:[ \t]+[^\n]*\n|[ \t]*\n(?=[ \t]+\S))*", re.M
)
_ATTR_ONLY_LINE_RE = re.compile(r"^[ \t]*\{[ \t]*[.#][^}\n]*\}[ \t]*$\n?", re.M)


def profile_mkdocs(text: str) -> str:
    text = _outside_fences(
        text,
        lambda s: _MKDOCSTRINGS_BLOCK_RE.sub(
            "",
            _VIDEO_RE.sub(
                "", _ATTR_ONLY_LINE_RE.sub("", _CANVAS_RE.sub("", _JINJA_LINE_RE.sub("", s)))
            ),
        ),
    )
    return profile_markdown(_mkdocs_blocks(text))


# ---------------------------------------------------------------------------
# Flecs docs: `<div class="flecs-snippet-tabs"><ul><li><b class="tab-title">C++</b> ...`
# (one tab per language)
# ---------------------------------------------------------------------------

_FLECS_TABS_RE = re.compile(r"<div class=\"flecs-snippet-tabs\">(.*?)</ul>\s*</div>", re.S)
_FLECS_TAB_RE = re.compile(r"<li><b class=\"tab-title\">([^<]*)</b>(.*?)</li>", re.S)


def _flecs_tabs(m: re.Match) -> str:
    tabs = _FLECS_TAB_RE.findall(m.group(1))
    if not tabs:
        return ""
    pick = next((body for name, body in tabs if name.strip() == "C++"), tabs[0][1])
    return "\n" + pick.strip() + "\n"


def profile_flecs(text: str) -> str:
    return profile_markdown(_FLECS_TABS_RE.sub(_flecs_tabs, text))


# ---------------------------------------------------------------------------
# Zola (Bevy website): `{% callout(type="warning") %} ... {% end %}`
# ---------------------------------------------------------------------------

_ZOLA_OPEN_RE = re.compile(r"^[ \t]*\{%[ \t]*callout\((?:type=\"(\w+)\")?\)[ \t]*%\}[ \t]*$", re.M)
_ZOLA_END_RE = re.compile(r"^[ \t]*\{%[ \t]*end[ \t]*%\}[ \t]*$\n?", re.M)


def profile_zola(text: str) -> str:
    text = _outside_fences(
        text,
        lambda s: _ZOLA_END_RE.sub(
            "", _ZOLA_OPEN_RE.sub(lambda m: f"**{(m.group(1) or 'note').title()}:**", s)
        ),
    )
    return profile_hugo(text)


# ---------------------------------------------------------------------------
# Apache xdoc XML (JMeter user manual): <section name="..."> / <subsection> / <source> / <p>
# ---------------------------------------------------------------------------

_XDOC_ENTITY_RE = re.compile(r"&(?:sect-num|hellip|rarr|larr|nbsp|copy|reg|trade|ndash|mdash);")
_XDOC_ENTITIES = {
    "hellip": "...",
    "rarr": " -> ",
    "larr": " <- ",
    "nbsp": " ",
    "ndash": "-",
    "mdash": "-",
}


def profile_xdoc(text: str, path: str = "", options: dict | None = None) -> str:
    text = re.sub(r"<!DOCTYPE[^\[>]*(?:\[.*?\])?\s*>", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = _XDOC_ENTITY_RE.sub(lambda m: _XDOC_ENTITIES.get(m.group(0)[1:-1], ""), text)
    title = ""
    tm = re.search(r"<title>(.*?)</title>", text, re.S)
    if tm:
        title = re.sub(r"\s+", " ", tm.group(1)).strip()
    text = re.sub(r"<properties>.*?</properties>", "", text, flags=re.S)

    def _head(level: int):
        def _f(m: re.Match) -> str:
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            return f"<h{level}>{name}</h{level}>"

        return _f

    text = re.sub(r"<section\b[^>]*\bname=\"([^\"]*)\"[^>]*>", _head(2), text)
    text = re.sub(r"<subsection\b[^>]*\bname=\"([^\"]*)\"[^>]*>", _head(3), text)
    text = re.sub(r"<component\b[^>]*\bname=\"([^\"]*)\"[^>]*>", _head(3), text)
    text = re.sub(r"</(?:section|subsection|component)>", "", text)
    text = re.sub(r"<source\b[^>]*>", "<pre>", text).replace("</source>", "</pre>")
    text = re.sub(
        r"</?(?:description|properties|note|example|figure|complink|ch_title|ch_section)\b[^>]*>",
        "",
        text,
    )
    text = re.sub(r"<(?:img|complink|figure|link)\b[^>]*/?>", "", text)
    md, _ = html_to_markdown(text, main_selectors=["body"])
    md = re.sub(r"^(#{1,6}) [.\d]+[ \t]+", r"\1 ", md, flags=re.M)
    if title and not re.match(r"\s*#{1,2}\s", md):
        md = f"# {title}\n\n{md}"
    return md


# ---------------------------------------------------------------------------
# Docusaurus MDX with React components (Cypress, Playwright, Testing Library)
# ---------------------------------------------------------------------------

_JSX_OPEN_RE = re.compile(r"<(/?)([A-Z]\w*)\b")
_DOCUSAURUS_STD = {"tip", "note", "info", "warning", "danger", "caution", "important", "details"}
_DOCUSAURUS_CUSTOM_RE = re.compile(r"^[ \t]*:::([a-z][\w-]*)[ \t]*$", re.M)


def _jsx_tag_end(seg: str, i: int) -> int:
    """Index just past the `>` closing a JSX tag that starts at `i` (quotes, {braces} respected)."""
    depth, quote, n = 0, "", len(seg)
    while i < n:
        c = seg[i]
        if quote:
            if c == quote:
                quote = ""
        elif c in "\"'`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ">" and depth <= 0:
            return i + 1
        i += 1
    return n


def _strip_jsx_components(seg: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _JSX_OPEN_RE.finditer(seg):
        if m.start() < pos:
            continue
        end = _jsx_tag_end(seg, m.end())
        tag = seg[m.start() : end]
        out.append(seg[pos : m.start()])
        name = m.group(2)
        repl = ""
        if not m.group(1):
            lab = re.search(r"\b(?:label|title|alt|callout)=(?:\"([^\"]*)\"|'([^']*)')", tag)
            text = (lab.group(1) or lab.group(2)) if lab else ""
            if name in ("TabItem", "Tab") and text:
                repl = f"\n**{text}**\n"
            elif name in ("DocsImage", "Icon", "DocsVideo") and len(text) > 12:
                repl = f"*[figure: {text}]*"
        out.append(repl)
        pos = end
    out.append(seg[pos:])
    return "".join(out)


_VIDEO_RE = re.compile(
    r"<video\b.*?</video>[ \t]*\n?|<ins\b[^>]*?/>[ \t]*", re.S | re.I
)  # also RN colour swatches


def profile_docusaurus(text: str) -> str:
    text = _outside_fences(
        text, _park_inline_code(lambda s: _VIDEO_RE.sub("", _strip_jsx_components(s)))
    )
    text = _outside_fences(
        text,
        lambda s: _DOCUSAURUS_CUSTOM_RE.sub(
            lambda m: m.group(0) if m.group(1) in _DOCUSAURUS_STD else "", s
        ),
    )
    return profile_markdown(text)


# ---------------------------------------------------------------------------
# MyST markdown (PyTorch docs, Pandera): {role}`text`, ```{directive}, :::{admonition}
# ---------------------------------------------------------------------------

_MYST_ROLE_RE = re.compile(r"\{(?:[\w:+-]+)\}`([^`\n]+)`")
_MYST_LABEL_RE = re.compile(r"^\([\w.:-]+\)=[ \t]*$\n?", re.M)
_MYST_FENCE_OPEN_RE = re.compile(r"^([ \t]*)(`{3,}|:{3,})\{([\w:-]+)\}[ \t]*(.*)$")
_MYST_ADMONITIONS = {
    "note",
    "tip",
    "warning",
    "important",
    "caution",
    "attention",
    "hint",
    "danger",
    "seealso",
    "admonition",
    "versionadded",
    "versionchanged",
    "deprecated",
}
_MYST_DROP = {
    "toctree",
    "eval-rst",
    "autoclass",
    "autofunction",
    "automodule",
    "autosummary",
    "literalinclude",
    "image",
    "figure",
    "contents",
    "currentmodule",
    "module",
    "include",
    "grid",
    "grid-item-card",
    "tab-set",
    "tab-item",
    "raw",
    "only",
    "testsetup",
    "testcleanup",
}


def _myst_role(m: re.Match) -> str:
    body = m.group(1).strip()
    lab = re.match(r"^(.*?)\s*<([^>]+)>$", body, re.S)
    if lab:
        return lab.group(1)
    return body.lstrip("~.")


def _myst_blocks(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        m = _MYST_FENCE_OPEN_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        ind, fence, name, arg = m.groups()
        closer = fence[0] * len(fence)
        j = i + 1
        body: list[str] = []
        while j < n and not lines[j].strip().startswith(closer):
            body.append(lines[j])
            j += 1
        i = j + 1
        while body and re.match(r"^\s*:[\w-]+:", body[0]):
            body.pop(0)  # directive options
        if name in ("code-block", "code", "sourcecode", "code-cell", "highlight"):
            out.append(f"{ind}```{arg.strip().split()[0] if arg.strip() else ''}")
            out.extend(b for b in body if not re.match(r"^\s*:[\w-]+:", b))
            out.append(f"{ind}```")
        elif name.lower() in _MYST_ADMONITIONS:
            label = arg.strip() if name == "admonition" and arg.strip() else name.title()
            out.append(f"{ind}**{label}:**")
            out.append("")
            out.extend(_myst_blocks("\n".join(body)).split("\n"))
            out.append("")
        elif name in _MYST_DROP:
            continue
        else:
            out.extend(_myst_blocks("\n".join(body)).split("\n"))
    return "\n".join(out)


def profile_myst(text: str) -> str:
    text = _myst_blocks(text)
    text = _MYST_LABEL_RE.sub("", text)
    text = _outside_fences(text, lambda s: _MYST_ROLE_RE.sub(_myst_role, s))
    return profile_markdown(text)


# ---------------------------------------------------------------------------
# OpenMMLab READMEs: drop model-zoo result tables (checkpoint / config download links)
# ---------------------------------------------------------------------------

_MMLAB_LINK_RE = re.compile(r"download\.openmmlab\.com|\]\(\./[\w./-]+\.py\)")


def _drop_zoo_tables(seg: str) -> str:
    lines = seg.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            block = lines[i:j]
            hits = sum(1 for ln in block if _MMLAB_LINK_RE.search(ln))
            if hits and hits >= max(1, (len(block) - 2) // 2):
                i = j
                continue
            out.extend(block)
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


_CJK_LINK_RE = re.compile(
    r"[,;:]?[ \t]*\[[^\]\n]*[\u3040-\u30ff\u3400-\u9fff][^\]\n]*\]\([^)\n]*\)"
)


def profile_mmlab(text: str) -> str:
    """OpenMMLab / BasicSR READMEs: model-zoo tables dropped, links with CJK labels (mirror
    downloads, translated pages) removed."""
    text = _outside_fences(text, lambda s: _CJK_LINK_RE.sub("", _drop_zoo_tables(s)))
    return profile_markdown(text)


# ---------------------------------------------------------------------------
# Playwright docs: per-language fences (```bash js / ```java) and `######\n* langs: x` blocks
# ---------------------------------------------------------------------------

_PW_LANG_SECTION_RE = re.compile(
    r"^######[ \t]*\n\*[ \t]+langs:[ \t]*([^\n]*)\n(.*?)(?=^######|^#{1,5}[ \t]|\Z)", re.S | re.M
)
_PW_OTHER_LANGS = ("java", "python", "csharp", "dotnet")
_PW_FENCE_RE = re.compile(r"^([ \t]*)(`{3,})([^\n`]*)\n(.*?)^\1\2[ \t]*$\n?", re.S | re.M)


def _pw_section(m: re.Match) -> str:
    langs = [x.strip() for x in m.group(1).split(",")]
    return m.group(2) if ("js" in langs or not m.group(1).strip()) else ""


def _pw_fence(m: re.Match) -> str:
    info = m.group(3).split()
    sel = [w.lower() for w in info]
    if any(w in _PW_OTHER_LANGS or w.startswith(("python", "java", "csharp")) for w in sel):
        return ""
    return m.group(0)


def profile_playwright(text: str) -> str:
    text = _PW_LANG_SECTION_RE.sub(_pw_section, text)
    text = _PW_FENCE_RE.sub(_pw_fence, text)
    text = re.sub(r"^######[ \t]*$\n?", "", text, flags=re.M)
    return profile_docusaurus(text)


PROFILES = {
    "markdown": lambda text, path="", options=None: profile_markdown(text),
    "rst": lambda text, path="", options=None: profile_rst(text, path),
    "d2l": lambda text, path="", options=None: profile_d2l(text),
    "mintlify": lambda text, path="", options=None: profile_mintlify(profile_markdown(text)),
    "hugo": lambda text, path="", options=None: profile_hugo(text),
    "html": profile_html,
    "asciidoc": lambda text, path="", options=None: profile_asciidoc(text),
    "msdocs": lambda text, path="", options=None: profile_msdocs(text, path),
    "jekyll": lambda text, path="", options=None: profile_jekyll(text),
    "quarto": lambda text, path="", options=None: profile_quarto(text),
    "notebook": lambda text, path="", options=None: profile_notebook(text),
    "mdn": lambda text, path="", options=None: profile_mdn(text),
    "writerside": lambda text, path="", options=None: profile_writerside(text),
    "docc": lambda text, path="", options=None: profile_docc(text),
    "eleventy": lambda text, path="", options=None: profile_eleventy(text),
    "doxygen": lambda text, path="", options=None: profile_doxygen(text),
    "mkdocs": lambda text, path="", options=None: profile_mkdocs(text),
    "flecs": lambda text, path="", options=None: profile_flecs(text),
    "zola": lambda text, path="", options=None: profile_zola(text),
    "xdoc": profile_xdoc,
    "docusaurus": lambda text, path="", options=None: profile_docusaurus(text),
    "myst": lambda text, path="", options=None: profile_myst(text),
    "mmlab": lambda text, path="", options=None: profile_mmlab(text),
    "playwright": lambda text, path="", options=None: profile_playwright(text),
}


_FM_TITLE_RE = re.compile(r"\A---\s*\n(.*?)\n---[ \t]*\n", re.S)


def inject_frontmatter_title(text: str) -> str:
    """Opt-in (`options.frontmatter_title: true`): the generic cleaner drops YAML frontmatter, which
    loses the page title for docs sites that keep it only there (Next.js, Hugo, Astro...). Prepends
    `# <title>` when the body has no H1/H2 before its first prose."""
    fm = _FM_TITLE_RE.match(text)
    if not fm:
        return text
    tm = re.search(r"^title:[ \t]*(.+?)[ \t]*$", fm.group(1), re.M)
    if not tm:
        return text
    title = tm.group(1).strip().strip("\"'")
    body = text[fm.end() :]
    first = next((ln for ln in body.split("\n") if ln.strip()), "")
    if not title or re.match(r"#{1,2}\s", first):
        return text
    if "<" in title and not title.startswith("`"):
        title = (
            f"`{title}`"  # `<Fragment> (<>...</>)`: tag-shaped titles must survive the JSX stripper
        )
    return text[: fm.end()] + f"# {title}\n\n" + body


def apply_profile(
    text: str, profile: str = "markdown", path: str = "", options: dict | None = None
) -> str:
    fn = PROFILES.get(profile)
    if fn is None:
        raise ValueError(f"unknown clean_profile {profile!r}")
    if (options or {}).get("frontmatter_title"):
        text = inject_frontmatter_title(text)
    return fn(text, path, options)
