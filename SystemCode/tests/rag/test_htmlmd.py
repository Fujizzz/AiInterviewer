from rag.htmlmd import convert_embedded_html, html_to_markdown

PAGE = """
<html><body>
<div class="topnav">SITE NAV that must not leak</div>
<div id="docContent">
  <div class="navheader"><table><tr><th>13.1. Intro</th></tr><tr><td><a
  href="x.html">Prev</a></td></tr></table></div>
  <div class="sect1">
    <h2 class="title">13.1. Introduction <a class="id_link" href="#a">#</a></h2>
    <a class="indexterm" name="idx"></a>
    <p>Postgres uses <acronym>MVCC</acronym> and <code class="literal">SELECT 1</code>; see <a
    href="b.html">this page</a>.</p>
    <pre class="programlisting">SELECT *
FROM t
WHERE a &lt; 3;</pre>
    <ul><li><p>one</p></li><li>two<ul><li>nested</li></ul></li></ul>
    <table><tr><th>Level</th><th>Dirty read</th></tr><tr><td>Read
    committed</td><td>No</td></tr></table>
    <div class="note"><h3 class="title">Note</h3><p>Be careful.</p></div>
  </div>
  <div class="navfooter"><a href="y.html">Next</a> FOOTER NAV</div>
</div>
<script>var x = 1;</script>
</body></html>
"""
STRIP = [".navheader", ".navfooter", ".id_link", ".indexterm"]


def test_main_container_selected_and_navigation_stripped():
    md, found = html_to_markdown(PAGE, ["#docContent"], STRIP)
    assert found
    assert "SITE NAV" not in md
    assert "FOOTER NAV" not in md and "Prev" not in md and "Next" not in md
    assert "var x" not in md


def test_headings_code_lists_tables_admonitions_preserved():
    md, _ = html_to_markdown(PAGE, ["#docContent"], STRIP)
    assert "## 13.1. Introduction" in md and "#" not in md.split("Introduction")[1].split("\n")[0]
    assert "`SELECT 1`" in md
    assert "```\nSELECT *\nFROM t\nWHERE a < 3;\n```" in md
    assert "- one" in md and "  - nested" in md
    assert (
        "| Level | Dirty read |" in md and "| --- | --- |" in md and "| Read committed | No |" in md
    )
    assert "**Note:** Be careful." in md
    assert "this page" in md and "b.html" not in md  # links reduced to text by default


def test_missing_main_selector_is_reported_not_silent():
    md, found = html_to_markdown(PAGE, ["#doesNotExist"], STRIP)
    assert found is False


def test_embedded_table_in_markdown_converted_and_generics_preserved():
    src = (
        "Use KStream<String, Long> here.\n\n"
        "<table><tr><th>Name</th><th>Desc</th></tr>"
        "<tr><td><code>x</code></td><td>Map<String, List<Long>> stays</td></tr></table>\n\nafter "
        "<b>bold</b>"
    )
    out = convert_embedded_html(src)
    assert "KStream<String, Long>" in out
    assert "| Name | Desc |" in out
    assert "Map<String, List<Long>>" in out
    assert "<table>" not in out and "**bold**" in out


def test_embedded_html_inside_code_fence_untouched():
    src = "```html\n<table><tr><td>x</td></tr></table>\n```"
    assert convert_embedded_html(src) == src


def test_very_long_table_rows_become_records():
    long_cell = "word " * 400
    html = (
        "<table><tr><th>Op</th><th>Description</th></tr>"
        f"<tr><td>Branch</td><td>{long_cell}</td></tr></table>"
    )
    md, _ = html_to_markdown(html)
    assert "**Branch**" in md and "Description: word" in md
    assert "| Op |" not in md
