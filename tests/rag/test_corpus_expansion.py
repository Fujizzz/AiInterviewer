"""Corpus-expansion (batch 1) cleaners, the STIX parser and registry invariants.

Only imports modules that need nothing beyond the CI lock file (pydantic / PyYAML / stdlib): no
torch, no pgvector, no sentence-transformers, no database.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rag.clean import clean_markdown, count_fences, strip_mdx_jsx
from rag.loaders.stix import parse_bundle
from rag.profiles import (
    adoc_to_markdown,
    apply_profile,
    inject_frontmatter_title,
    profile_hugo,
    profile_markdown,
    rst_to_markdown,
)
from rag.registry import VALID_CLEAN_PROFILES, load_registry, validate_registry

SOURCES_YAML = Path(__file__).resolve().parents[2] / "rag" / "data" / "sources.yaml"


# ---------------------------------------------------------------------------
# mdn
# ---------------------------------------------------------------------------

MDN = """\
---
title: "Promise.all()"
slug: Web/JavaScript/Reference/Global_Objects/Promise/all
---

{{JSRef}}

The **`Promise.all()`** method takes {{jsxref("Promise", "promises")}}, {{domxref("fetch()")}}
and {{HTMLElement("div")}}; see {{Glossary("CORS")}} and {{rfc(7235)}}.

{{EmbedInteractiveExample("pages/js/promise-all.html")}}

> [!NOTE]
> Rejects as soon as one input rejects.

```js example-good
const p = Promise.all([a, b]);
```

## Specifications

{{Specifications}}

## See also

- [Promise](/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise)

## Syntax

Tail text.
"""


def test_mdn_profile_resolves_macros_and_title():
    out = apply_profile(MDN, "mdn")
    assert out.startswith("# Promise.all()")
    assert "promises" in out and "fetch()" in out and "`<div>`" in out and "CORS" in out
    assert "RFC 7235" in out
    assert "{{" not in out and "EmbedInteractiveExample" not in out
    assert "> **Note:**" in out
    assert "```js\n" in out  # info string reduced to the language
    assert "Specifications" not in out and "See also" not in out
    assert "## Syntax" in out and "Tail text." in out


def test_mdn_profile_keeps_tex_annotation_of_inline_mathml():
    t = (
        "---\ntitle: S\n---\nSee <math><semantics><mi>A</mi>"
        '<annotation encoding="TeX">A \\setminus B</annotation></semantics></math> here.\n'
    )
    assert "$A \\setminus B$" in apply_profile(t, "mdn")


# ---------------------------------------------------------------------------
# asciidoc
# ---------------------------------------------------------------------------

ADOC = """\
= Dependency Injection
:page-section-summary-toc: 1

[[beans]]
== The IoC Container

Dependency injection is a process. See xref:core/beans/intro.adoc[the introduction] and
link:https://spring.io[Spring].

NOTE: Constructor-based DI is preferred.

[source,java,indent=0]
----
public class A {
    private final B b;
}
----

.Bean scopes
[cols="1,3"]
|===
| Scope | Description

| `singleton`
| Single instance per container.

| `prototype`
| New instance per request.
|===

* first
** nested

include::partial$x.adoc[]
// a comment
"""


def test_adoc_converts_headings_code_tables_admonitions():
    out = adoc_to_markdown(ADOC)
    assert out.startswith("# Dependency Injection")
    assert "## The IoC Container" in out
    assert "the introduction" in out and "[Spring](https://spring.io)" in out
    assert "**Note:** Constructor-based DI is preferred." in out
    assert "```java\npublic class A {" in out
    assert "| Scope | Description |" in out and "| --- | --- |" in out
    assert "| `singleton` | Single instance per container. |" in out
    assert "- first\n  - nested" in out
    assert "include::" not in out and "// a comment" not in out and ":page-section" not in out
    assert count_fences(out) % 2 == 0


def test_adoc_table_cells_get_inline_conversion():
    t = "|===\n| Name | Meaning\n\n| [[x]]<<x, `+slot.name+`>> | The slot\n|===\n"
    out = adoc_to_markdown(t)
    assert "`slot.name`" in out and "[[" not in out and "<<" not in out


# ---------------------------------------------------------------------------
# msdocs
# ---------------------------------------------------------------------------


def test_msdocs_profile_callouts_images_includes_and_table_lists():
    t = """\
---
title: Retry pattern
---

[!INCLUDE [cloud](../includes/x.md)]

> [!NOTE]
> Be careful.

:::image type="content" source="_images/a.png" alt-text="Diagram showing the retry flow":::

| Threat | Mitigation |
| --- | --- |
| Spoofing | <ul><li>Use TLS</li><li>Use mTLS</li></ul> |
"""
    out = apply_profile(t, "msdocs")
    assert out.startswith("# Retry pattern")
    assert "[!INCLUDE" not in out and ":::image" not in out
    assert "> **Note:**" in out
    assert "*[figure: Diagram showing the retry flow]*" in out
    assert "<ul>" not in out and "• Use TLS" in out


def test_msdocs_content_include_pages_get_a_title_from_the_file_name():
    from rag.profiles import profile_msdocs

    out = profile_msdocs("Body text.\n\n## Solution\n\nx\n", "docs/patterns/valet-key-content.md")
    assert out.startswith("# Valet Key pattern")


# ---------------------------------------------------------------------------
# jekyll / liquid
# ---------------------------------------------------------------------------


def test_jekyll_profile_liquid_and_kramdown():
    t = """\
---
title: Mappings
---
{% comment %}hidden{% endcomment %}
{% highlight scala %}
val x = 1
{% endhighlight %}
{% include_example foo scala/Foo.scala %}
Use {% data variables.product.prodname_actions %} for CI. {{ site.version }}
A paragraph that needs a callout.
{: .note}
{: .highlight }
"""
    out = apply_profile(t, "jekyll")
    assert "```scala\nval x = 1\n```" in out
    assert "hidden" not in out and "include_example" not in out and "{{" not in out
    assert "GitHub Actions" in out
    assert "**Note:** A paragraph that needs a callout." in out
    assert "{:" not in out


# ---------------------------------------------------------------------------
# quarto / notebook
# ---------------------------------------------------------------------------


def test_quarto_profile_drops_r_chunks_and_maps_divs():
    t = """\
---
title: Inference
---

# Inference {#sec-inference}

```{r}
#| label: fig-x
ggplot(df)
```

::: {.workedexample data-latex=""}
A sample of 826 borrowers; see @fig-x and @sec-other [@smith2020].
:::

```python
keep = 1
```
"""
    out = apply_profile(t, "quarto")
    assert "ggplot" not in out and "{r}" not in out
    assert "**Worked example:**" in out
    assert "@fig" not in out and "smith2020" not in out and "{#sec-" not in out
    assert "```python\nkeep = 1\n```" in out


def test_notebook_profile_strips_relative_images():
    out = apply_profile(
        "![image.png](./data/img/x.png)\n\nText with [a link](other.ipynb).", "notebook"
    )
    assert "x.png" not in out and "a link" in out and "other.ipynb" not in out


# ---------------------------------------------------------------------------
# hugo (kubernetes/website) + markdown additions
# ---------------------------------------------------------------------------


def test_hugo_keeps_glossary_tooltip_text_and_tab_names():
    t = (
        'A {{< glossary_tooltip text="Pod" term_id="pod" >}} is small.\n\n'
        '{{< feature-state feature_gate_name="X" state="stable" for_k8s_version="v1.20" >}}\n\n'
        '{{< tabs name="t" >}}\n{{% tab name="Linux" %}}\nhi\n{{% /tab %}}\n{{< /tabs >}}\n'
    )
    out = profile_hugo(t)
    assert "A Pod is small." in out
    assert "**Feature state:** X (stable)" in out
    assert "**Linux**" in out and "{{" not in out


def test_vitepress_containers_and_html_headings():
    t = "::: tip Note\nUse this.\n:::\n\n  :::: details More\nBody\n  ::::\n\n<h3>Bad</h3>\n"
    out = profile_markdown(t)
    assert ":::" not in out
    assert "**Note:**" in out and "**Bad**" in out and "<h3>" not in out


def test_mdx_figure_components_become_figure_notes():
    t = (
        '<Diagram name="render_tree" height={250} width={500} alt="A tree graph with five nodes, '
        'each node is a component.">\n\nAn example React render tree.\n\n</Diagram>\n\n'
        '<Lightbox src="/img/a.png" width="100%" title="Metric tiles showing run statistics" />\n'
    )
    out = clean_markdown(profile_markdown(t))
    assert "<Diagram" not in out and "<Lightbox" not in out
    assert "*[figure: A tree graph" in out
    assert "An example React render tree." in out


def test_inline_code_tags_survive_mdx_stripping():
    out = strip_mdx_jsx("Use the `<Transition>` component and `<KeepAlive />` too. <Note>x</Note>")
    assert "`<Transition>`" in out and "`<KeepAlive />`" in out
    assert "<Note>" not in out


def test_frontmatter_title_is_opt_in_and_skips_pages_with_a_heading():
    t = "---\ntitle: Pods\n---\nIntro text.\n"
    assert "# Pods" not in apply_profile(t, "markdown", options={})
    assert "# Pods" in apply_profile(t, "markdown", options={"frontmatter_title": True})
    already = "---\ntitle: X\n---\n# Already\n"
    assert inject_frontmatter_title(already) == already


def test_rst_math_role_keeps_formula_and_graphviz_is_dropped():
    t = "Cost is :math:`O(n^2)`.\n\n.. graphviz::\n\n   digraph g { a -> b }\n\nAfter.\n"
    out = rst_to_markdown(t)
    assert "$O(n^2)$" in out and "digraph" not in out and "After." in out


# ---------------------------------------------------------------------------
# STIX
# ---------------------------------------------------------------------------


def _bundle() -> dict:
    ref = {
        "source_name": "mitre-attack",
        "external_id": "T1059",
        "url": "https://attack.mitre.org/techniques/T1059",
    }
    return {
        "type": "bundle",
        "objects": [
            {
                "type": "attack-pattern",
                "name": "Command and Scripting Interpreter",
                "description": "Abuse <code>cmd.exe</code> (Citation: Foo 2020) to run.",
                "external_references": [ref],
                "kill_chain_phases": [
                    {"kill_chain_name": "mitre-attack", "phase_name": "execution"}
                ],
                "x_mitre_platforms": ["Windows", "Linux"],
                "x_mitre_detection": "Monitor process creation.",
            },
            {
                "type": "attack-pattern",
                "name": "Old thing",
                "description": "revoked",
                "revoked": True,
                "external_references": [{**ref, "external_id": "T9999"}],
            },
            {
                "type": "x-mitre-tactic",
                "name": "Execution",
                "description": "The adversary is trying to run malicious code.",
                "external_references": [
                    {
                        **ref,
                        "external_id": "TA0002",
                        "url": "https://attack.mitre.org/tactics/TA0002",
                    }
                ],
            },
            {
                "type": "intrusion-set",
                "name": "APT1",
                "description": "group",
                "external_references": [ref],
            },
        ],
    }


def test_stix_parser_keeps_techniques_and_tactics_only():
    docs = parse_bundle(json.dumps(_bundle()))
    assert [d.attack_id for d in docs] == ["T1059", "TA0002"]
    tech = docs[0]
    assert tech.kind == "technique" and tech.url.endswith("/T1059")
    assert "`cmd.exe`" in tech.text and "Citation" not in tech.text
    assert "**Tactics:** execution" in tech.text and "**Platforms:** Windows, Linux" in tech.text
    assert "## Detection" in tech.text


# ---------------------------------------------------------------------------
# registry invariants for the expanded corpus
# ---------------------------------------------------------------------------


def test_registry_profiles_are_all_registered_and_applicable():
    sources = load_registry(SOURCES_YAML)
    assert validate_registry(sources) == []
    for s in sources:
        assert s.clean_profile in VALID_CLEAN_PROFILES
        apply_profile("# T\n\ntext\n", s.clean_profile if s.clean_profile != "html" else "markdown")


def test_every_ingestable_source_records_a_licence_that_allows_reuse():
    sources = load_registry(SOURCES_YAML)
    for s in sources:
        assert s.license and s.license.strip(), s.name
        if s.reference_only:
            continue
        lic = s.license.lower()
        # ND (no derivatives) and BUSL are never ingested; chunking is a derivative
        assert not re.search(r"\bnd\b|noderiv|no-deriv|-nd-|-nd\b", lic), s.name
        assert "busl" not in lic and "proprietary" not in lic, s.name
        assert not lic.startswith("unknown") and "unspecified" not in lic, s.name


def test_qa_banks_are_not_ingested_in_this_batch():
    sources = {s.name: s for s in load_registry(SOURCES_YAML)}
    for name in (
        "front-end-interview-handbook",
        "devops-exercises",
        "data-science-interviews",
        "ml-interviews-alirezadir",
        "ml-interviews-book",
    ):
        assert sources[name].reference_only is True


# ---------------------------------------------------------------------------
# Wikipedia loader (pure functions + fetch with a faked API)
# ---------------------------------------------------------------------------

WIKI_EXTRACT = """\
CAP theorem, also named Brewer's theorem, states that a distributed data store cannot provide
more than two of consistency, availability and partition tolerance at the same time.


== Definition ==
The three guarantees are {\\displaystyle C} consistency and availability.

=== Partition tolerance ===
The system keeps working despite dropped messages.

== See also ==
Eventual consistency

=== Sub of see also ===
dropped too

== References ==
1. Brewer

== Criticism ==
It is often misunderstood.
"""


def test_wikipedia_extract_to_markdown_keeps_body_and_drops_boilerplate():
    pytest.importorskip("requests")
    from rag.loaders.wikipedia_loader import extract_to_markdown

    md = extract_to_markdown("CAP theorem", WIKI_EXTRACT)
    assert md.startswith("# CAP theorem\n")
    assert "## Definition" in md and "### Partition tolerance" in md and "## Criticism" in md
    assert "$C$" in md and "displaystyle" not in md
    for gone in ("See also", "References", "dropped too", "1. Brewer"):
        assert gone not in md


def test_wikipedia_extract_truncates_long_articles():
    pytest.importorskip("requests")
    from rag.loaders.wikipedia_loader import extract_to_markdown

    md = extract_to_markdown("X", "\n".join("word " * 10 for _ in range(50)), max_words=100)
    assert 90 <= len(md.split()) <= 110


def test_wikipedia_fetch_builds_revision_permalinks(monkeypatch, tmp_path):
    pytest.importorskip("requests")
    from rag.loaders import wikipedia_loader as wl
    from rag.registry import Source

    def fake_batch(titles, session):
        return {
            "query": {
                "pages": [
                    {
                        "title": "CAP theorem",
                        "extract": WIKI_EXTRACT,
                        "revisions": [{"revid": 123456}],
                    },
                    {"title": "No such page", "missing": True},
                ]
            }
        }

    monkeypatch.setattr(wl, "_get_batch", fake_batch)
    src = Source(
        name="wiki-test",
        namespace="technical",
        priority="P1",
        url="https://en.wikipedia.org/",
        loader="wikipedia",
        license="CC-BY-SA-4.0",
        auto_level="full",
        trust_level="community",
        refresh_cycle="yearly",
        domain="system-design",
        options={"titles": ["CAP theorem", "No such page"]},
    )
    res = wl.fetch(src, tmp_path)
    assert [d.path for d in res.docs] == ["CAP_theorem"]
    doc = res.docs[0]
    assert doc.doc_version == "123456" and "oldid=123456" in doc.source_url
    assert any("No such page" in e for e in res.errors)


def test_wikipedia_math_tokens_are_replaced_by_inline_tex():
    pytest.importorskip("requests")
    from rag.loaders.wikipedia_loader import extract_to_markdown

    raw = (
        "The components are a sequence of \n  \n    \n      \n        p\n      \n    \n"
        "    {\\displaystyle p}\n  \n unit vectors, where the \n  \n    \n      \n"
        "        i\n      \n    \n    {\\displaystyle i}\n  \n-th vector is a line.\n"
    )
    md = extract_to_markdown("PCA", raw)
    assert "a sequence of $p$ unit vectors, where the $i$-th vector is a line." in md
