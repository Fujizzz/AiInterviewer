"""Corpus-expansion (batch 2): new clean profiles, git loader options and registry invariants.

Needs nothing beyond the CI lock file (pydantic / PyYAML / stdlib): no torch, no pgvector, no
requests, no database. Profile tests use small synthetic inputs in the shape of each doc dialect.
"""

from __future__ import annotations

from pathlib import Path

from rag.clean import clean_markdown
from rag.htmlmd import html_to_markdown
from rag.loaders import git_loader
from rag.profiles import apply_profile
from rag.registry import VALID_CLEAN_PROFILES, Source, load_registry

SOURCES_YAML = Path(__file__).resolve().parents[2] / "rag" / "data" / "sources.yaml"

BATCH2_PROFILES = {
    "writerside",
    "docc",
    "eleventy",
    "doxygen",
    "mkdocs",
    "flecs",
    "zola",
    "xdoc",
    "docusaurus",
    "myst",
    "mmlab",
}


def test_batch2_profiles_are_registered_and_callable():
    assert BATCH2_PROFILES <= VALID_CLEAN_PROFILES
    for name in BATCH2_PROFILES:
        assert isinstance(apply_profile("# T\n\nsome text here\n", name), str)


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------


def test_writerside_title_callouts_and_attributes():
    text = (
        "[//]: # (title: Sealed classes)\n\n"
        "<web-summary>ignore me</web-summary>\n\n"
        "Sealed classes restrict inheritance.\n\n"
        "> Direct subclasses are classes that inherit immediately.\n>\n"
        '{style="tip"}\n\n'
        "```kotlin\nsealed class A\n```\n"
        '{kotlin-runnable="true" validate="false"}\n\n'
        "## Declare {id=declare}\n\n"
        'Press <shortcut>Esc</shortcut> to cancel ![icon](x.png){type="joined"}.\n'
    )
    out = apply_profile(text, "writerside")
    assert out.lstrip().startswith("# Sealed classes")
    assert "web-summary" not in out and "ignore me" not in out
    assert "**Tip:**" in out and 'style="tip"' not in out
    assert "kotlin-runnable" not in out and "{id=" not in out and "{type=" not in out
    assert "Press Esc to cancel" in out
    assert "sealed class A" in out  # code survives


def test_docc_links_become_plain_words():
    out = apply_profile(
        "# ARC\n\nSee <doc:Closures> and <doc:Properties#Lazy-Stored-Properties>.\n", "docc"
    )
    assert "<doc:" not in out
    assert "Closures" in out and "Lazy Stored Properties" in out


def test_eleventy_dashimage_noscript_and_link_definitions():
    text = (
        "---\ntitle: Lists\n---\n\nIntro text about lists.\n\n"
        '<DashImage figure image="a.webp" caption="Cap" '
        'alt="A long alt text for the animation" />\n\n'
        "<noscript>\n  hidden\n</noscript>\n\n"
        '<?code-excerpt "lib/main.dart (x)"?>\n```dart\nvar x = 1;\n```\n\n'
        "[`ListTile`]: /ListTile-class.html\n"
        ":::version-note\n    Needs 3.27.\n"
    )
    out = apply_profile(text, "eleventy")
    assert "DashImage" not in out and "noscript" not in out and "code-excerpt" not in out
    assert "[`ListTile`]:" not in out
    assert "A long alt text for the animation" in out
    assert "var x = 1;" in out
    assert "**Version Note:**" in out


def test_doxygen_headings_math_code_and_refs():
    text = (
        "Optical Flow {#tutorial_optical_flow}\n============\n\n"
        "@tableofcontents\n\n@prev_tutorial{tutorial_a}\n\n"
        "Goal\n----\n\n"
        'Use @ref tutorial_other "the other tutorial" and \\f$I(x,y,t)\\f$ with\n\n'
        "\\f[f_x u + f_y v + f_t = 0\\f]\n\n"
        "@snippet samples/cpp/x.cpp body\n\n"
        "-#  first step\n"
        "@code{.py}\nimport cv2 as cv\n@endcode\n"
    )
    out = apply_profile(text, "doxygen")
    assert out.lstrip().startswith("# Optical Flow")
    assert "## Goal" in out
    assert "tableofcontents" not in out and "@snippet" not in out and "prev_tutorial" not in out
    assert "the other tutorial" in out
    assert "$I(x,y,t)$" in out and "$$ f_x u + f_y v + f_t = 0 $$" in out
    assert "1. first step" in out
    assert "```python\nimport cv2 as cv\n```" in out


def test_mkdocs_admonitions_tabs_and_jinja():
    text = (
        '{% include "macros/x.md" %}\n\n'
        '!!! tip "Speed up"\n\n    Use a smaller model.\n\n'
        '=== "Python"\n\n    ```python\n    a = 1\n    ```\n\n'
        '=== "CLI"\n\n    ```bash\n    yolo predict\n    ```\n\n'
        "Done.\n"
    )
    out = apply_profile(text, "mkdocs")
    assert "{%" not in out and "!!!" not in out and "===" not in out
    assert "**Speed up:**" in out and "Use a smaller model." in out
    assert "**Python**" in out and "**CLI**" in out
    assert "```python\na = 1\n```" in out  # tab body is dedented, fence intact


def test_flecs_snippet_tabs_keep_only_cpp():
    text = (
        "# Entities\n\nText.\n\n"
        '<div class="flecs-snippet-tabs">\n<ul>\n'
        '<li><b class="tab-title">C</b>\n\n```c\necs_new(world);\n```\n\n</li>\n'
        '<li><b class="tab-title">C++</b>\n\n```cpp\nworld.entity();\n```\n\n</li>\n'
        "</ul>\n</div>\n\nAfter.\n"
    )
    out = apply_profile(text, "flecs")
    assert "world.entity();" in out and "ecs_new" not in out and "flecs-snippet" not in out


def test_zola_callouts():
    text = '# Book\n\n{% callout(type="warning") %}\nBe careful.\n{% end %}\n\nBody.\n'
    out = apply_profile(text, "zola")
    assert "**Warning:**" in out and "{%" not in out and "Be careful." in out


def test_xdoc_sections_become_headings_and_code():
    xml = (
        '<?xml version="1.0"?>\n<!DOCTYPE document [<!ENTITY sect-num "16">]>\n'
        "<document><properties><title>User Manual: Best Practices</title></properties><body>\n"
        '<section name="&sect-num;. Best Practices" anchor="bp"></section>\n'
        '<section name="&sect-num;.1 Use the latest version" anchor="v">'
        "<p>Always upgrade&hellip;</p>\n"
        "<source>jmeter -n -t plan.jmx</source></section>\n</body></document>\n"
    )
    out = apply_profile(xml, "xdoc")
    assert "## Use the latest version" in out
    assert "Always upgrade..." in out
    assert "jmeter -n -t plan.jmx" in out and "<section" not in out and "&sect-num" not in out


def test_docusaurus_components_and_custom_directives():
    text = (
        "Intro.\n\n"
        '<Tabs\n  groupId="pm"\n  values={[\n    {label: "npm", value: "npm"},\n  ]\n}>\n\n'
        '<TabItem value="npm" label="npm">\n\n```bash\nnpm i\n```\n\n</TabItem>\n</Tabs>\n\n'
        '<DocsImage\n  src="/img/a.png"\n  alt="The test finds and clicks the button"\n/>\n\n'
        ":::cypress-config-example\n\n```ts\nbaseUrl: 'x'\n```\n"
    )
    out = apply_profile(text, "docusaurus")
    assert "<Tabs" not in out and "<TabItem" not in out and "DocsImage" not in out
    assert "**npm**" in out and "npm i" in out
    assert "The test finds and clicks the button" in out
    assert ":::cypress-config-example" not in out and "baseUrl: 'x'" in out


def test_myst_roles_directives_and_labels():
    text = (
        "(rand-notes)=\n\n# Randomness\n\n"
        "Use {func}`torch.manual_seed` and {ref}`the guide <rand-notes>`.\n\n"
        "```{code-block} python\nimport torch\n```\n\n"
        ":::{note}\nSeeds matter.\n:::\n\n"
        "```{toctree}\nindex\n```\n"
    )
    out = apply_profile(text, "myst")
    assert "(rand-notes)=" not in out and "{func}" not in out and "toctree" not in out
    assert "torch.manual_seed" in out and "the guide" in out
    assert "```python\nimport torch\n```" in out
    assert "**Note:**" in out and "Seeds matter." in out


def test_mmlab_drops_model_zoo_tables_but_keeps_prose():
    text = (
        "# BasicVSR\n\nBasicVSR uses bidirectional propagation.\n\n"
        "| Model | PSNR | Download |\n| --- | --- | --- |\n"
        "| [cfg](./basicvsr.py) | 31.4 | [model](https://download.openmmlab.com/x.pth) |\n"
        "| [cfg2](./basicvsr2.py) | 31.5 | [model](https://download.openmmlab.com/y.pth) |\n\n"
        "| Kept | Table |\n| --- | --- |\n| a | b |\n"
    )
    out = apply_profile(text, "mmlab")
    assert "bidirectional propagation" in out
    assert "download.openmmlab.com" not in out
    assert "| Kept | Table |" in out


# ---------------------------------------------------------------------------
# htmlmd (Google devsite pages)
# ---------------------------------------------------------------------------


def test_html_block_start_closes_paragraph_and_custom_elements_render_code_blocks():
    html = (
        '<article class="devsite-article-body"><p>Intro.</p>\n<p>\n\n<div></div>'
        '<devsite-code><pre syntax="Kotlin"><span>@Composable</span>\n'
        '<span>fun A() {}</span></pre><div class="devsite-github-link">x.kt</div></devsite-code>\n'
        "<devsite-recommendations><h2>Recommended for you</h2></devsite-recommendations>"
        "</article>"
    )
    md, found = html_to_markdown(
        html,
        main_selectors=[".devsite-article-body"],
        strip_selectors=["devsite-recommendations", ".devsite-github-link"],
    )
    assert found
    assert "```kotlin\n@Composable\nfun A() {}\n```" in md
    assert "Recommended" not in md and "x.kt" not in md


# ---------------------------------------------------------------------------
# git loader: non-GitHub permalink template and dangling symlinks
# ---------------------------------------------------------------------------


def _git_source(**kw) -> Source:
    base = dict(
        name="yocto-test",
        namespace="technical",
        priority="P1",
        url="https://git.example.org/yocto-docs",
        loader="git",
        license="CC-BY-SA-2.0-UK",
        auto_level="full",
        trust_level="official",
        refresh_cycle="yearly",
        domain="embedded",
        paths=["documentation/"],
        include=["documentation/**/*.rst"],
    )
    base.update(kw)
    return Source(**base)


def test_git_loader_refuses_non_github_url_without_permalink_template(tmp_path):
    res = git_loader.fetch(_git_source(), tmp_path)
    assert res.skipped and "permalink_template" in (res.skip_reason or "")


def test_git_loader_permalink_template_and_dangling_symlink(tmp_path, monkeypatch):
    repo = tmp_path / "yocto-test" / "_repo" / "documentation"
    repo.mkdir(parents=True)
    (repo / "a.rst").write_text("Title\n=====\n\nBody text of the manual.\n", encoding="utf-8")
    (repo / "dangling.rst").symlink_to(tmp_path / "does-not-exist.rst")

    def fake_run(cmd, cwd=None):
        return "main" if "--abbrev-ref" in cmd else "c0ffee1234"

    monkeypatch.setattr(git_loader, "_run", fake_run)
    src = _git_source(
        options={"permalink_template": "https://git.example.org/yocto-docs/tree/{rel}?id={commit}"}
    )
    res = git_loader.fetch(src, tmp_path)
    assert [d.path for d in res.docs] == ["documentation/a.rst"]
    assert res.docs[0].source_url == (
        "https://git.example.org/yocto-docs/tree/documentation/a.rst?id=c0ffee1234"
    )
    assert res.docs[0].doc_version == "c0ffee1234"


# ---------------------------------------------------------------------------
# registry invariants for batch 2
# ---------------------------------------------------------------------------

_P0_ARXIV = {
    "paper-attention-is-all-you-need",
    "paper-lora",
    "paper-pagedattention-vllm",
    "paper-rag",
    "paper-react",
    "paper-reflexion",
}


def test_new_arxiv_sources_only_ingest_papers_with_a_creative_commons_licence():
    sources = load_registry(SOURCES_YAML)
    arxiv = [s for s in sources if s.loader == "arxiv" and s.name not in _P0_ARXIV]
    assert arxiv, "batch 2 registers CC-licensed papers"
    for s in arxiv:
        if s.reference_only:
            continue
        lic = s.license.lower()
        assert lic.startswith("cc"), (s.name, s.license)
        assert "-nd" not in lic and "nd-" not in lic, (s.name, s.license)


def test_batch2_reference_only_sources_have_no_loader_that_chunks():
    sources = {s.name: s for s in load_registry(SOURCES_YAML)}
    for name in (
        "pact-docs",
        "k6-docs",
        "testng-docs",
        "google-testing-blog",
        "gstreamer-app-dev-manual",
        "arxiv-papers-reference-only-batch2",
    ):
        assert sources[name].reference_only is True, name
        assert sources[name].loader == "manual", name


def test_every_registered_source_has_a_loadable_profile():
    for s in load_registry(SOURCES_YAML):
        if s.reference_only or s.loader not in ("git", "crawler"):
            continue
        assert clean_markdown(apply_profile("# T\n\ntext text text\n", s.clean_profile)) is not None
