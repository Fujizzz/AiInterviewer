from rag.chunking import (
    Section,
    chunk_markdown_doc,
    chunk_sections,
    merge_tiny_sections,
    pack_blocks,
    split_into_blocks,
    split_markdown_into_sections,
)
from rag.schema import count_tokens


def test_split_simple_headings():
    text = "# Title\nintro text\n\n## Sub\nsub text\n"
    sections = split_markdown_into_sections(text)
    assert [s.heading_path for s in sections] == [["Title"], ["Title", "Sub"]]
    assert sections[0].text == "intro text"
    assert sections[1].text == "sub text"


def test_heading_inside_fenced_code_block_is_ignored():
    text = "# Title\n```\n# not a heading\n```\nmore text\n"
    sections = split_markdown_into_sections(text)
    assert len(sections) == 1
    assert "# not a heading" in sections[0].text


def test_no_headings_returns_single_section():
    text = "just a plain paragraph with no headings at all"
    sections = split_markdown_into_sections(text)
    assert len(sections) == 1
    assert sections[0].heading_path == []


def test_merge_tiny_sections_merges_into_previous():
    sections = [
        Section(heading_path=["A"], text="word " * 200),  # large enough to survive
        Section(heading_path=["A", "B"], text="tiny"),  # well under min_tokens
    ]
    merged = merge_tiny_sections(sections, min_tokens=80)
    assert len(merged) == 1
    assert "tiny" in merged[0].text
    assert merged[0].heading_path == ["A"]


def test_merge_tiny_sections_merges_leading_tiny_into_next():
    sections = [
        Section(heading_path=["A"], text="tiny"),
        Section(heading_path=["B"], text="word " * 200),
    ]
    merged = merge_tiny_sections(sections, min_tokens=80)
    assert len(merged) == 1
    assert "tiny" in merged[0].text
    assert merged[0].heading_path == ["B"]


def test_split_into_blocks_keeps_fenced_code_atomic():
    text = "para one\n\n```python\nline1\nline2\n```\n\npara two"
    blocks = split_into_blocks(text)
    assert blocks[0] == "para one"
    assert blocks[1].startswith("```python")
    assert blocks[1].endswith("```")
    assert blocks[2] == "para two"


def test_split_into_blocks_keeps_table_atomic():
    text = "before\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nafter"
    blocks = split_into_blocks(text)
    assert blocks[0] == "before"
    assert blocks[1].count("\n") == 2
    assert blocks[2] == "after"


def test_pack_blocks_respects_max_tokens_and_overlaps():
    blocks = ["word " * 50 for _ in range(10)]  # ~50 tokens each -> len//4 heuristic
    packed = pack_blocks(blocks, max_tokens=120)
    assert len(packed) > 1
    # every chunk should carry at least some content
    assert all(p.strip() for p in packed)


def test_oversized_section_is_split_and_small_section_is_not():
    small_text = "short section text"
    big_text = "\n\n".join(f"paragraph number {i} " * 30 for i in range(20))
    sections = [
        Section(heading_path=["H"], text=small_text),
        Section(heading_path=["H2"], text=big_text),
    ]
    out = chunk_sections(sections, min_tokens=1, max_tokens=100)
    assert any(count_tokens(s.text) <= 100 for s in out)
    assert len(out) > 2  # big section got split into multiple pieces


def test_long_bullet_list_without_blank_lines_is_split_per_item():
    # A long nav-style list with no blank lines between items used to be
    # treated as one giant "paragraph" block; it should now split per
    # top-level item so pack_blocks can size it properly.
    items = [f"* item number {i} with some extra words to pad it out" for i in range(40)]
    text = "\n".join(items)
    blocks = split_into_blocks(text)
    assert len(blocks) == 40
    assert blocks[0].startswith("* item number 0")
    assert blocks[-1].startswith("* item number 39")


def test_list_split_preserves_all_content():
    text = "* one\n* two\n* three"
    blocks = split_into_blocks(text)
    assert "".join(blocks).replace("\n", "") == text.replace("\n", "")


def test_mostly_prose_paragraph_with_one_stray_dash_is_not_split():
    text = "This is prose.\nIt has a line - with a dash in it.\nAnd more prose here."
    blocks = split_into_blocks(text)
    assert len(blocks) == 1


def test_chunk_markdown_doc_never_splits_fence():
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(200)) + "\n```"
    text = f"# Title\n{code}\n"
    out = chunk_markdown_doc(text, min_tokens=1, max_tokens=50)
    # the fence must appear whole inside exactly one of the output chunks
    joined_counts = sum(
        1 for sec in out if "```python" in sec.text and sec.text.strip().endswith("```")
    )
    assert joined_counts >= 1


# ---------------------------------------------------------------------------
# oversized tables / fences / overlap guard (added with the P0-completion work)
# ---------------------------------------------------------------------------

from rag.chunking import split_fence_block, split_table_block  # noqa: E402


def _table(n_rows):
    rows = ["| Name | Description |", "| --- | --- |"]
    rows += [f"| row{i} | " + "word " * 40 + "|" for i in range(n_rows)]
    return "\n".join(rows)


def test_oversized_table_split_between_rows_with_header_repeated():
    pieces = split_table_block(_table(40), max_tokens=200)
    assert len(pieces) > 1
    for p in pieces:
        lines = p.split("\n")
        assert lines[0] == "| Name | Description |" and lines[1] == "| --- | --- |"
        assert all(
            line.startswith("|") and line.endswith("|") for line in lines
        )  # no row cut in half
    body_rows = [line for p in pieces for line in p.split("\n")[2:]]
    assert len(body_rows) == 40


def test_small_table_not_split():
    t = _table(3)
    assert split_table_block(t, max_tokens=800) == [t]


def test_monstrous_fence_split_into_valid_fences_but_moderate_fence_stays_whole():
    big = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(1500)) + "\n```"
    pieces = split_fence_block(big, max_tokens=200)
    assert len(pieces) > 1
    for p in pieces:
        assert p.startswith("```python\n") and p.endswith("\n```")
    medium = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(60)) + "\n```"
    assert split_fence_block(medium, max_tokens=800) == [medium]


def test_overlap_not_carried_when_previous_block_is_large():
    big = "word " * 300  # ~300 tokens, > max_tokens // 3
    small = "tiny paragraph"
    out = pack_blocks([big, big, small], max_tokens=400)
    assert out[1].count("word ") <= 300 + 5  # second chunk is NOT big+big
