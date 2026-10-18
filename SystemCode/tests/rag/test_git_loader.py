from rag.loaders.base import RawDoc
from rag.loaders.git_loader import (
    extract_snippet_section,
    find_needed_snippets,
    resolve_snippet_includes,
    substitute_snippet_includes_in_text,
)


def test_find_needed_snippets_collects_paths_and_sections():
    docs = [
        RawDoc(
            path="a.md",
            text='```\n--8<-- "vllm/envs.py:env-vars-definition"\n```',
            doc_version="x",
            source_url="u",
        ),
        RawDoc(path="b.md", text='--8<-- "docs/install.inc.md"', doc_version="x", source_url="u"),
    ]
    needed = find_needed_snippets(docs)
    assert needed["vllm/envs.py"] == {"env-vars-definition"}
    assert needed["docs/install.inc.md"] == set()


def test_extract_snippet_section_finds_start_end_markers():
    content = (
        "before\n"
        "# --8<-- [start:my-section]\n"
        "line one\n"
        "line two\n"
        "# --8<-- [end:my-section]\n"
        "after\n"
    )
    snippet = extract_snippet_section(content, "my-section")
    assert "line one" in snippet
    assert "line two" in snippet
    assert "before" not in snippet
    assert "after" not in snippet


def test_extract_snippet_section_missing_marker_returns_none():
    assert extract_snippet_section("no markers here", "my-section") is None


def test_substitute_inlines_small_resolved_snippet():
    text = '```python\n--8<-- "pkg/mod.py:bit"\n```'
    file_cache = {
        "pkg/mod.py": "# --8<-- [start:bit]\nvalue = 1\n# --8<-- [end:bit]\n",
    }
    new_text, resolved, not_found, too_large = substitute_snippet_includes_in_text(text, file_cache)
    assert resolved == 1
    assert not_found == 0
    assert too_large == 0
    assert "value = 1" in new_text
    assert "--8<--" not in new_text


def test_substitute_strips_directive_when_file_not_found():
    text = '--8<-- "missing/file.py:section"'
    new_text, resolved, not_found, too_large = substitute_snippet_includes_in_text(
        text, {"missing/file.py": None}
    )
    assert resolved == 0
    assert not_found == 1
    assert "--8<--" not in new_text


def test_substitute_strips_directive_when_section_missing_from_file():
    text = '--8<-- "pkg/mod.py:nope"'
    file_cache = {"pkg/mod.py": "no markers in this file at all"}
    new_text, resolved, not_found, too_large = substitute_snippet_includes_in_text(text, file_cache)
    assert resolved == 0
    assert not_found == 1
    assert "--8<--" not in new_text


def test_substitute_strips_directive_when_target_too_large():
    text = '--8<-- "pkg/big.py"'
    huge_content = "x = 1\n" * 5000  # way over the token cap
    new_text, resolved, not_found, too_large = substitute_snippet_includes_in_text(
        text, {"pkg/big.py": huge_content}, max_tokens=100
    )
    assert resolved == 0
    assert too_large == 1
    assert "--8<--" not in new_text
    assert "x = 1" not in new_text


def test_substitute_does_not_touch_text_without_directives():
    text = "just some normal prose, no snippet includes here"
    new_text, resolved, not_found, too_large = substitute_snippet_includes_in_text(text, {})
    assert new_text == text
    assert (resolved, not_found, too_large) == (0, 0, 0)


# ---------------------------------------------------------------------------
# regression: a directive can resolve to text that itself contains further
# directives (vllm's docker.md includes a section of gpu.md whose body
# contains nested includes of gpu.cuda.inc.md/etc.) — this must not leave
# literal unresolved "--8<--" text behind.
# ---------------------------------------------------------------------------


def test_resolve_snippet_includes_follows_nested_includes(tmp_path):
    repo_dir = tmp_path
    (repo_dir / "b.md").write_text(
        "# --8<-- [start:section-b]\n"
        "nested include follows:\n"
        '--8<-- "c.md"\n'
        "# --8<-- [end:section-b]\n",
        encoding="utf-8",
    )
    (repo_dir / "c.md").write_text("content from c", encoding="utf-8")

    doc_a = RawDoc(
        path="a.md",
        text='top level include:\n--8<-- "b.md:section-b"\n',
        doc_version="v",
        source_url="u",
    )
    docs = [doc_a]

    notes = resolve_snippet_includes(repo_dir, docs)

    assert "content from c" in doc_a.text
    assert "--8<--" not in doc_a.text
    assert notes and "resolved inline" in notes[0]


def test_resolve_snippet_includes_gives_up_gracefully_on_a_cycle(tmp_path):
    repo_dir = tmp_path
    # x.md includes y.md, y.md includes x.md: must terminate, not hang.
    (repo_dir / "x.md").write_text('--8<-- "y.md"', encoding="utf-8")
    (repo_dir / "y.md").write_text('--8<-- "x.md"', encoding="utf-8")

    doc = RawDoc(path="entry.md", text='--8<-- "x.md"', doc_version="v", source_url="u")
    notes = resolve_snippet_includes(repo_dir, [doc])

    assert notes  # produced a summary, did not hang or raise
    assert isinstance(doc.text, str)


# ---------------------------------------------------------------------------
# declarative scoping helpers (include/exclude -> sparse-checkout patterns)
# ---------------------------------------------------------------------------

from rag.loaders.git_loader import _sparse_patterns  # noqa: E402


def test_sparse_patterns_expand_braces_and_push_down_dir_excludes():
    pats = _sparse_patterns(
        ["docs/source/en/*.md", "docs/source/en/model_doc/{bert,gpt2}.md", "docs/guide/"],
        exclude=["docs/source/en/model_doc/**", "docs/**/*.tmp", "docs/{a,b}/**"],
    )
    assert "docs/source/en/model_doc/bert.md" in pats and "docs/source/en/model_doc/gpt2.md" in pats
    assert "docs/guide/" in pats and "docs/source/en/*.md" in pats
    assert "!docs/source/en/model_doc/" in pats and "!docs/a/" in pats and "!docs/b/" in pats
    assert not any(
        p.startswith("!") and "*" in p for p in pats
    )  # glob excludes are post-filtered, not pushed down


def test_sparse_patterns_full_checkout_signal():
    assert _sparse_patterns(["."]) is None
