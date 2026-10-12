from rag.clean import clean_markdown, count_fences
from rag.profiles import (
    apply_profile,
    profile_d2l,
    profile_hugo,
    profile_markdown,
    profile_mintlify,
    rst_to_markdown,
)

RST = """\
=====================
Making queries
=====================

Once you've created :doc:`data models </topics/db/models>`, see
:ref:`the API <queryset-api>` and :meth:`~django.db.models.query.QuerySet.delete`::

    >>> Blog.objects.all()

.. code-block:: python
    :caption: models.py

    class Foo(models.Model):
        pass

.. versionadded:: 4.1

    Added the thing.

.. note::

    Careful with ``QuerySet.delete()``.

.. toctree::
    :maxdepth: 2

    other

.. _label:

Sub heading
-----------

.. class:: Foo(bar)

    A descriptive body.

Done_ here.
"""


def test_rst_headings_codeblocks_roles_directives():
    md = rst_to_markdown(RST)
    assert md.startswith("# Making queries")
    assert "## Sub heading" in md
    assert "```python\nclass Foo(models.Model):\n    pass\n```" in md
    assert "data models" in md and ":doc:" not in md and ":ref:" not in md and "`delete`" in md
    assert "```python\n>>> Blog.objects.all()\n```" in md  # '::' literal block
    assert "*New in version 4.1.* Added the thing." in md
    assert "**Note:** Careful with `QuerySet.delete()`." in md
    assert (
        "toctree" not in md
        and "maxdepth" not in md
        and "other" not in md.split("Sub heading")[0].split("**Note")[1]
    )
    assert "**Foo(bar)**" in md and "A descriptive body." in md
    assert ".. _label" not in md and "Done here." in md


def test_rst_comments_dropped_and_page_still_balanced_fences():
    md = rst_to_markdown(".. comment that is ignored\n   continued\n\nReal text.\n")
    assert "ignored" not in md and "Real text." in md
    assert count_fences(RST) == 0 and count_fences(rst_to_markdown(RST)) % 2 == 0


def test_sphinx_gallery_py_to_rst_roundtrip():
    py = '''"""
Tensors
=======

Intro paragraph.
"""

import torch

######################################################################
# Creating tensors
# ----------------
#
# We create one below.

x = torch.zeros(2)
print(x)
'''
    md = apply_profile(py, "rst", path="beginner_source/t.py")
    assert md.startswith("# Tensors") and "Intro paragraph." in md
    assert "## Creating tensors" in md and "We create one below." in md
    assert "```python\nimport torch\n```" in md and "x = torch.zeros(2)" in md
    assert "######" not in md


D2L = """\
```{.python .input  n=2}
%load_ext d2lbook.tab
tab.interact_select('mxnet', 'pytorch')
```

# Attention
:label:`sec_att`

:begin_tab:`mxnet`
mxnet only prose
:end_tab:

:begin_tab:`pytorch`
pytorch prose, see :numref:`sec_foo-bar` :cite:`Vaswani.2017` and :citet:`X.2020`
:end_tab:

```{.python .input}
#@tab mxnet
import mxnet
```

```{.python .input}
%%tab pytorch, jax
import torch  #@save
```

```{.python .input}
%%tab tensorflow
import tensorflow
```

```{.python .input  n=3}
#@tab all
y = [**1**]
```

```toc
:maxdepth: 2
a
```
"""


def test_d2l_keeps_pytorch_only():
    out = profile_d2l(D2L)
    assert "import torch\n" in out and "#@save" not in out
    assert "import mxnet" not in out and "tensorflow" not in out and "mxnet only" not in out
    assert (
        "pytorch prose" in out
        and ":numref:" not in out
        and ":cite" not in out
        and "citet" not in out
    )
    assert "y = [**1**]" in out  # tab all kept, code untouched
    assert "d2lbook" not in out and "interact_select" not in out
    assert (
        ":maxdepth" not in out
        and ":label:" not in out
        and "%%tab" not in out
        and "#@tab" not in out
    )


MDX = """\
import X from './x'

Intro.

:::python
Python text.

```python
print("hi")  # [!code highlight]
```
:::

:::js
JS-only text.
```js
console.log("hi")
```
:::

<Accordion title="Why?">
Because. See @[`interrupt`].
</Accordion>

<img
  src="/a.png"
  alt="x"
/>
"""


def test_mintlify_python_kept_js_dropped_components_unwrapped():
    out = profile_mintlify(MDX)
    assert "Python text." in out and 'print("hi")' in out and "[!code" not in out
    assert "JS-only" not in out and "console.log" not in out and ":::" not in out
    assert "**Why?**" in out and "Because." in out and "<Accordion" not in out
    assert "`interrupt`" in out and "@[" not in out
    assert "<img" not in out and "import X" not in out


def test_hugo_shortcodes_and_embedded_html():
    src = (
        '{{< clients-example set="hash" step="a" >}}\n> HSET k f v\n(integer) 1\n{{< '
        "/clients-example >}}\n\n"
        '{{< command-group group="hash" title="x" >}}\n\n{{< include-html '
        'file="/static/{version}/gen.html" >}}\n\n'
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>\n\n"
        "[`HSET`](/content/commands/hset.md) text"
    )
    out = profile_hugo(src)
    assert "```\n> HSET k f v\n(integer) 1\n```" in out
    assert "{{" not in out and "include-html" not in out
    assert "| A | B |" in out and "`HSET` text" in out and "/content/commands" not in out


def test_markdown_profile_strips_autodoc_and_notebook_output():
    src = (
        "# Title\n\n[[autodoc]] Foo\n    - bar\n    - baz\n\nReal text.\n\n"
        '<table border="1" class="dataframe"><tr><td>1</td></tr></table>\n\n'
        "<div>\n  <progress value='1' max='2'></progress>\n</div>\n\n<hfoptions id=\"a\"><hfoption "
        'id="b">kept</hfoption></hfoptions>'
    )
    out = profile_markdown(src)
    assert "autodoc" not in out and "- bar" not in out and "Real text." in out
    assert (
        "dataframe" not in out and "progress" not in out and "kept" in out and "hfoption" not in out
    )


def test_generic_cleaner_does_not_swallow_fences_when_prose_mentions_script_tag():
    # regression found on django docs: prose mentioning <script> + a later </script>
    src = (
        "Use the `<script>` element carefully.\n\n```html\n<p>x</p>\n```\n\nLater `</script>` ends "
        "it.\n\n```python\nprint(1)\n```\n"
    )
    out = clean_markdown(src)
    assert count_fences(out) == count_fences(src) == 4 and "print(1)" in out
