from rag.clean import (
    clean_markdown,
    count_fences,
    dedupe_chunks,
    dedupe_paragraphs,
    is_junk_text,
    protect_fences,
    restore_fences,
    strip_frontmatter,
    strip_html_comments,
    strip_mdx_jsx,
    strip_style_script,
)


def test_strip_frontmatter_removes_yaml_block():
    text = "---\ntitle: Foo\n---\n# Heading\nbody\n"
    out = strip_frontmatter(text)
    assert "title: Foo" not in out
    assert "# Heading" in out


def test_strip_html_comments():
    text = "before <!-- hidden comment\nspanning lines --> after"
    out = strip_html_comments(text)
    assert "hidden comment" not in out
    assert "before" in out and "after" in out


def test_strip_mdx_jsx_removes_import_export_and_tags():
    text = "import Foo from 'bar'\n\n<Tabs>\ncontent\n</Tabs>\n\nexport const x = 1\n"
    out = strip_mdx_jsx(text)
    assert "import Foo" not in out
    assert "export const" not in out
    assert "<Tabs>" not in out
    assert "content" in out


def test_dedupe_paragraphs_drops_consecutive_duplicates():
    text = "para one\n\npara one\n\npara two"
    out = dedupe_paragraphs(text)
    assert out.count("para one") == 1
    assert "para two" in out


def test_clean_markdown_end_to_end():
    text = "---\ntitle: X\n---\n\n<!-- comment -->\n# Heading\n\nEdit this page\n\nreal content\n"
    out = clean_markdown(text)
    assert "title: X" not in out
    assert "comment" not in out
    assert "Edit this page" not in out
    assert "real content" in out


def test_dedupe_chunks_keeps_first_drops_later_exact_duplicates():
    texts = ["Hello world", "Something else", "hello   world"]
    mask = dedupe_chunks(texts)
    assert mask == [True, True, False]


# ---------------------------------------------------------------------------
# regression: heredoc inside a fenced code block must never be eaten by the
# JSX-tag stripper, even when a real HTML/JSX-shaped tag appears later in
# the same document (the original bug: `[^>]*` with DOTALL matched from the
# second `<` of `cat <<EOF` all the way to an unrelated later `>`, deleting
# ~3000 chars of two real YAML code blocks in vllm's docs/deployment/k8s.md)
# ---------------------------------------------------------------------------


def test_heredoc_in_fence_survives_and_is_not_eaten_by_a_later_tag():
    text = (
        "# Deploy\n\n"
        "Apply the manifest:\n\n"
        "```bash\n"
        "cat <<EOF |kubectl apply -f -\n"
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n"
        "  name: example\n"
        "EOF\n"
        "```\n\n"
        "Then run the second manifest:\n\n"
        "```bash\n"
        "cat <<EOF |kubectl apply -f -\n"
        "apiVersion: v1\n"
        "kind: Secret\n"
        "EOF\n"
        "```\n\n"
        "<details>\n"
        "some collapsible aside\n"
        "</details>\n\n"
        "visible content after the tag\n"
    )
    out = clean_markdown(text)

    # both heredocs, in full, must survive verbatim
    assert "kind: ConfigMap" in out
    assert "kind: Secret" in out
    assert out.count("cat <<EOF") == 2
    assert out.count("EOF\n```") == 2

    # nothing between/after the fences was swallowed
    assert "Then run the second manifest" in out
    assert "visible content after the tag" in out

    # fence markers must balance (4 fence lines: 2 opens + 2 closes)
    assert count_fences(text) == count_fences(out) == 4


def test_protect_fences_round_trips_exactly():
    text = "before\n\n```python\nx = 1\n```\n\nafter"
    protected, placeholders = protect_fences(text)
    assert "```" not in protected
    assert restore_fences(protected, placeholders) == text


def test_jsx_tag_does_not_match_double_angle_bracket_heredoc_marker():
    # `<<EOF` must never be read as `<EOF ...>` by the JSX tag stripper,
    # even outside of a fence (belt-and-suspenders on top of fence protection).
    text = "run: cat <<EOF\nsome text\nEOF\nmore text <Note>real jsx</Note> tail"
    out = strip_mdx_jsx(text)
    assert "cat <<EOF" in out
    assert "some text" in out
    assert "<Note>" not in out
    assert "real jsx" in out
    assert "tail" in out


def test_jsx_strip_skips_match_over_max_chars_and_warns(capsys):
    # A single-line match that would delete a suspiciously large amount of
    # text is left alone (and logged) instead of silently deleted.
    long_attr = "x" * 250
    text = f"<Weird {long_attr}>payload</Weird>"
    out = strip_mdx_jsx(text)
    assert "<Weird" in out  # opening tag match was skipped, left in place
    captured = capsys.readouterr()
    assert "WARNING" in captured.out


def test_strip_style_script_removes_style_block():
    text = "before\n<style>\nth { min-width: 0 !important; }\n</style>\nafter"
    out = strip_style_script(text)
    assert "min-width" not in out
    assert "before" in out and "after" in out


# ---------------------------------------------------------------------------
# post-chunk junk filter
# ---------------------------------------------------------------------------


def test_is_junk_text_bare_mkdocstrings_directive():
    assert is_junk_text("::: fastapi.encoders.jsonable_encoder") is True


def test_is_junk_text_mkdocstrings_with_short_description_is_still_junk():
    text = (
        "OpenAPI Pydantic models used to generate and validate the generated OpenAPI.\n\n::: "
        "fastapi.openapi.models"
    )
    assert is_junk_text(text) is True


def test_is_junk_text_bare_jinja_include():
    assert is_junk_text("{% include 'index_body' %}") is True


def test_is_junk_text_bare_style_tag_leftover():
    assert is_junk_text("<style>\n</style>") is True


def test_is_junk_text_real_prose_is_not_junk():
    text = (
        "FastAPI automatically converts the request body to the declared type, "
        "validates it, and generates OpenAPI documentation for it. This is real, "
        "retrievable technical content that should never be dropped."
    )
    assert is_junk_text(text) is False


def test_is_junk_text_empty_is_junk():
    assert is_junk_text("   \n\n  ") is True


# ---------------------------------------------------------------------------
# table normalisation + extra junk categories + guarded stripping
# ---------------------------------------------------------------------------

from rag.clean import junk_category, normalize_tables  # noqa: E402


def test_normalize_tables_collapses_padding_and_separators():
    t = (
        "| Method                 | CPU      |\n|------------------------|:--------:|\n| "
        "[AQLM](./aqlm)         | yes      |"
    )
    out = normalize_tables(t).split("\n")
    assert out[0] == "| Method | CPU |"
    assert out[1] == "| --- | :---: |"
    assert out[2] == "| [AQLM](./aqlm) | yes |"
    assert normalize_tables("not a | table row") == "not a | table row"


def test_junk_categories():
    assert junk_category("::: pkg.mod") == "doc-tool-directive-only"
    short = "Defines the planner statistics view."
    assert junk_category(short, token_count=7, namespace="technical") == "too-short"
    assert (
        junk_category(short, token_count=7, namespace="question") is None
    )  # short questions are legitimate
    links = "\n".join(f"- [Page {i}](page{i}.md)" for i in range(6))
    assert junk_category(links) == "link-index"
    assert junk_category("Below is the producer configuration:", token_count=8) == "dangling-intro"
    assert (
        junk_category(
            "This tutorial has been deprecated. There is a new tutorial on this "
            "topic.\n\nRedirecting in 3 seconds...",
            token_count=18,
        )
        == "redirect-stub"
    )
    dump = "```python\n" + "\n".join(f"[{i}, {i}, {i}]," for i in range(900)) + "\n```"
    assert junk_category(dump, token_count=2500) == "code-data-dump"
    prose = (
        "Real documentation text explaining how the planner chooses an index scan versus a "
        "sequential scan, with details."
    )
    assert junk_category(prose, token_count=60, namespace="technical") is None
    assert junk_category("## Heading only\n\n### Another") == "heading-only"


def test_guarded_strip_never_swallows_fence_placeholders():
    src = "Mentions <!-- in prose\n\n```py\ncode\n```\n\nand later --> closes it.\n"
    out = clean_markdown(src)
    assert "code" in out and count_fences(out) == 2


def test_nested_nav_tree_is_link_index_but_question_namespace_is_exempt():
    nav = (
        "- Home\n    - [Main Page](index.md)\n    - [Navigation](navigation.md)\n- Algebra\n    - "
        "Fundamentals\n"
        "        - [Binary Exponentiation](algebra/binary-exp.md)\n        - "
        "[Euclid](algebra/euclid.md)\n        - [Sieve](algebra/sieve.md)"
    )
    assert junk_category(nav, token_count=90, namespace="technical") == "link-index"
    links = "\n".join(f"- [Problem {i}](https://leetcode.com/problems/p{i}/)" for i in range(5))
    assert junk_category(links, token_count=60, namespace="technical") == "link-index"
    assert junk_category(links, token_count=60, namespace="question") is None
