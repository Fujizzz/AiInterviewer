from rag.globs import is_selected, matches


def test_star_does_not_cross_directories():
    assert matches("docs/a.md", "docs/*.md")
    assert not matches("docs/sub/a.md", "docs/*.md")


def test_double_star_crosses_directories_and_matches_zero_dirs():
    assert matches("docs/a/b/c.md", "docs/**")
    assert matches("docs/a.md", "docs/**/a.md")
    assert matches("x/y/z.rst", "**/*.rst")


def test_brace_alternation():
    assert matches("m/bert.md", "m/{bert,gpt2}.md")
    assert not matches("m/t5.md", "m/{bert,gpt2}.md")


def test_include_exclude_semantics():
    inc, exc = ["docs/**"], ["docs/internal/**", "**/CHANGELOG*"]
    assert is_selected("docs/guide/a.md", inc, exc)
    assert not is_selected("docs/internal/a.md", inc, exc)
    assert not is_selected("docs/guide/CHANGELOG.md", inc, exc)
    assert not is_selected("other/a.md", inc, exc)
    assert is_selected("anything.md", [], [])  # empty include = everything
