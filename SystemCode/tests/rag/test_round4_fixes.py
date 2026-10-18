"""Validation round 4: fastapi code includes, slash admonitions, residual HTML, `{#id}` headings."""

from rag.clean import clean_markdown, strip_heading_ids, unescape_entities
from rag.htmlmd import convert_embedded_html
from rag.loaders.base import RawDoc
from rag.loaders.git_loader import resolve_code_includes, substitute_code_includes_in_text
from rag.profiles import profile_d2l, profile_markdown

PB = "https://github.com/o/r/blob/abc"
SRC = (
    "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n@app.get('/')\ndef root():\n    return "
    "{}\n"
)


def inc(text, cache=None, **kw):
    cache = {"docs_src/a/t001.py": SRC} if cache is None else cache
    return substitute_code_includes_in_text(text, "docs/en", cache, permalink_base=PB, **kw)


def test_code_include_is_inlined_as_python_fence_and_hl_is_dropped():
    out, c = inc("Intro\n\n{* ../../docs_src/a/t001.py hl[5:9] *}\n\nAfter")
    assert c == {"inlined": 1, "too_large": 0, "unresolved": 0}
    assert "```python\nfrom fastapi import FastAPI" in out and "hl[" not in out and "{*" not in out
    assert out.count("```") == 2 and out.rstrip().endswith("After")
    assert "def root():" in out and "return {}" in out  # full file kept, hl is not a range


def test_code_include_honours_ln_ranges_and_marks_gaps():
    out, c = inc("{* ../../docs_src/a/t001.py ln[1:3,6] hl[1] *}")
    assert c["inlined"] == 1
    body = out.split("\n")[1:-1]
    assert body == [
        "from fastapi import FastAPI",
        "",
        "app = FastAPI()",
        "# ...",
        "@app.get('/')",
        "# ...",
    ]


def test_code_include_title_language_and_indent():
    cache = {"docs_src/a/x.js": "console.log(1)\n", "docs_src/a/x.html": "<p>x</p>\n"}
    out, _ = inc('//// tab\n  {* ../../docs_src/a/x.js title["app/main.js"] *}\n', cache)
    assert "**app/main.js**" in out and "  ```javascript\n  console.log(1)\n  ```" in out
    out, _ = inc("{* ../../docs_src/a/x.html *}", cache)
    assert out.startswith("```html")


def test_oversized_code_include_becomes_one_line_permalink_note():
    out, c = inc(
        "{* ../../docs_src/a/t001.py hl[1] *}",
        {"docs_src/a/t001.py": "x = 1\n" * 4000},
        max_tokens=100,
    )
    assert c["too_large"] == 1 and "```" not in out
    assert out == f"(full content omitted for size; see upstream: {PB}/docs_src/a/t001.py)"


def test_unresolved_code_include_never_leaves_placeholder_text():
    out, c = inc("{* ../../docs_src/missing.py hl[1] *}", {})
    assert c["unresolved"] == 1 and "{*" not in out and "hl[" not in out
    assert (
        out
        == f"(code example not available in this checkout; see upstream: {PB}/docs_src/missing.py)"
    )
    out, c = inc("{* ../../../../etc/passwd *}")  # escaping the repo is unresolvable, not read
    assert c["unresolved"] == 1 and "{*" not in out


def test_legacy_include_inside_fence_is_filled_in_place():
    out, c = inc('```Python hl_lines="1"\n{!> ../../docs_src/a/t001.py!}\n```\n')
    assert (
        c["inlined"] == 1 and out.count("```") == 2 and "app = FastAPI()" in out and "{!" not in out
    )


def test_resolve_code_includes_reads_files_from_checkout(tmp_path):
    (tmp_path / "docs_src").mkdir()
    (tmp_path / "docs_src" / "e.py").write_text("print('hi')\n")
    docs = [
        RawDoc(
            path="docs/en/docs/a.md",
            text="x\n\n{* ../../docs_src/e.py hl[1] *}\n",
            doc_version="v",
            source_url="u",
        )
    ]
    notes = resolve_code_includes(tmp_path, docs, "docs/en", permalink_base=PB)
    assert "print('hi')" in docs[0].text and "1 inlined" in notes[0] and "0 unresolved" in notes[0]


def test_slash_admonitions_and_tabs_become_bold_labels():
    out = profile_markdown(
        "Intro\n/// warning | Careful now\nBody\n///\n\n//// tab | Python 3.10+\n\n/// "
        "tip\nHi\n///\n\n////\n\n"
        "```python\n/// literal\n```\n"
    )
    assert "///" not in out.replace("/// literal", "")
    assert (
        "**Careful now:**" in out
        and "**Python 3.10+**" in out
        and "**Tip:**" in out
        and "Body" in out
    )
    assert "/// literal" in out  # inside a fence: untouched


def test_abbr_and_dfn_expand_once_and_inline_code_is_untouched():
    out = convert_embedded_html(
        'The <abbr title="Software Development Kits">SDKs</abbr> and <dfn title="converting data | '
        'x">serialization</dfn>; '
        '<abbr title="HTTP">HTTP</abbr>; `<abbr title="x">y</abbr>`.'
    )
    assert (
        "SDKs (Software Development Kits)" in out and "serialization (converting data / x)" in out
    )
    assert "HTTP;" in out and "HTTP (HTTP)" not in out and '`<abbr title="x">y</abbr>`' in out
    assert "<abbr" not in out.replace('`<abbr title="x">y</abbr>`', "")


def test_head_script_nobr_button_removed_or_unwrapped():
    out = convert_embedded_html(
        'Intro\n\n<head>\n  <link rel="canonical" href="x" />\n  <title>T</title>\n</head>\n\n'
        '| NVIDIA | <nobr>🟢</nobr> |\n\n<script async defer src="a.js"></script> Star Watch\n\n'
        '<button class="b" id="x"> Preview </button>\n<meta http-equiv="refresh" content="0; '
        'url=x">\n'
    )
    for gone in (
        "<head",
        "<link",
        "<title",
        "<script",
        "<nobr",
        "<button",
        "<meta",
        "canonical",
        "a.js",
    ):
        assert gone not in out
    assert "🟢" in out and "Star Watch" in out and "Preview" in out


def test_entities_are_unescaped_in_prose_but_not_in_code_or_table_pipes():
    out = clean_markdown(
        "Done &#x2705; &amp; &ndash; a&nbsp;b. Token “&lt;cls&gt;”.\n\n`&lt;keep&gt;` | a &#124; "
        "b\n\n```html\n&lt;x&gt; &amp;\n```\n"
    )
    assert "✅ & – a b." in out
    assert "“`<cls>`”" in out and "&lt;cls" not in out
    assert "`&lt;keep&gt;`" in out and "&#124;" in out and "&lt;x&gt; &amp;" in out


def test_unescape_entities_never_creates_a_raw_tag():
    assert "`<div>`" in unescape_entities("write &lt;div&gt; here")


def test_heading_id_suffix_no_space_form_dotted_and_indented():
    src = (
        "## dsl.store.format {#dsl-store-format}\n\n### redis.call {#redis.call}\n\n #### "
        "group.instance.id {#group-instance-id}\n\n## Spaced { #a }\n\ntext {#not-a-heading}\n"
    )
    out = strip_heading_ids(src)
    assert (
        "## dsl.store.format\n" in out
        and "### redis.call\n" in out
        and "#### group.instance.id\n" in out
    )
    assert "{#dsl" not in out and "{#redis" not in out and "{#group" not in out
    assert "text {#not-a-heading}" in out  # only headings are rewritten
    full = clean_markdown(src)
    assert (
        "{#" in full and "## dsl.store.format\n" in full
    )  # `{#...}` inside body text is left alone


def test_heading_ids_are_stripped_before_chunking_so_titles_are_clean():
    from rag.chunking import chunk_markdown_doc

    text = clean_markdown(
        "# Top {#top}\n\n## dsl.store.format {#dsl-store-format}\n\n" + "Body sentence here. " * 40
    )
    secs = chunk_markdown_doc(text)
    assert all("{#" not in h for s in secs for h in s.heading_path)


def test_d2l_cell_with_hash_prefixed_tab_marker_is_dropped_for_other_frameworks():
    src = (
        "```{.python .input}\n# %%tab jax\nx = 1\n```\n\n```{.python .input}\n#@tab pytorch\ny = "
        "2\n```\n"
    )
    out = profile_d2l(src)
    assert "x = 1" not in out and "%%tab" not in out and "y = 2" in out


def test_d2l_tab_marker_after_leading_blank_line_is_found_and_removed():
    src = (
        "```{.python .input}\n\n%%tab pytorch, tensorflow, jax\nx = 1\n```\n\n```{.python .input}\n"
        "\n%%tab mxnet\ny = 2\n```\n"
    )
    out = profile_d2l(src)
    assert "x = 1" in out and "%%tab" not in out and "y = 2" not in out


def test_heading_id_inside_indented_admonition_is_stripped():
    assert (
        strip_heading_ids("    #### Launching the OpenAI server {#launching-the-openai-server}\n")
        == "    #### Launching the OpenAI server\n"
    )
