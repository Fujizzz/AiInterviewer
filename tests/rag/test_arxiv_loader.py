import pytest

# Optional rag-extra dependencies: skip the whole module when they are not installed.
pytest.importorskip("requests")
from rag.loaders.arxiv_loader import _extract_sections

_FAKE_PAPER_HTML = """
<html>
<body>
<div class="ltx_page_main">
  <header class="ltx_page_header">site chrome, not paper content</header>
  <article class="ltx_document ltx_authors_1line">
    <h1 class="ltx_title ltx_title_document">My Great Paper</h1>
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">1 </span>Introduction</h2>
    <p>This is the introduction with real content.</p>
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">2 </span>Method</h2>
    <p>This describes the method in detail.</p>
    <h2 class="ltx_title ltx_title_section">Acknowledgments</h2>
    <p>We thank everyone for their support and funding.</p>
    <h2 class="ltx_title ltx_title_bibliography">References</h2>
    <ul>
      <li>[1] Some Author. Some Paper. 2020.</li>
      <li>[2] Another Author. Another Paper. 2021.</li>
    </ul>
    <h2 class="ltx_title ltx_title_appendix">Appendix A Extra Results</h2>
    <p>This appendix has genuinely useful extra experiments.</p>
  </article>
  <div class="keyboard-glossary">
    <h2>Instructions for reporting errors</h2>
    <p>We are continuing to improve HTML versions of papers...</p>
  </div>
</body>
</html>
"""


def test_extracts_only_sections_inside_article():
    sections = _extract_sections(_FAKE_PAPER_HTML, "My Great Paper")
    all_text = " ".join(text for _, text in sections)
    assert "continuing to improve HTML versions" not in all_text
    assert "site chrome, not paper content" not in all_text


def test_drops_references_and_acknowledgments_but_keeps_appendix():
    sections = _extract_sections(_FAKE_PAPER_HTML, "My Great Paper")
    headings = [h for h, _ in sections]

    assert not any("reference" in h.lower() for h in headings)
    assert not any("bibliography" in h.lower() for h in headings)
    assert not any("acknowledg" in h.lower() for h in headings)

    assert any("introduction" in h.lower() for h in headings)
    assert any("method" in h.lower() for h in headings)
    assert any("appendix" in h.lower() for h in headings)

    all_text = " ".join(text for _, text in sections)
    assert "Some Author" not in all_text
    assert "thank everyone" not in all_text
    assert "genuinely useful extra experiments" in all_text


def test_real_content_sections_keep_their_text():
    sections = _extract_sections(_FAKE_PAPER_HTML, "My Great Paper")
    intro = next(t for h, t in sections if "introduction" in h.lower())
    assert "real content" in intro


def test_no_article_tag_falls_back_to_whole_page_without_crashing():
    html_without_article = "<html><body><h1>Title</h1><p>body text</p></body></html>"
    sections = _extract_sections(html_without_article, "Fallback Title")
    assert len(sections) >= 1
    assert any("body text" in t for _, t in sections)
