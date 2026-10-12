"""Structure-aware chunker.

Three entry points, one per source shape described in the task brief:

- `chunk_markdown_doc(text)`      — technical markdown/rst docs: split by
  heading hierarchy, merge tiny sections into a neighbour, split oversized
  sections on paragraph boundaries (never inside a fenced code block or a
  table), with a small overlap between adjacent split pieces.
- `chunk_sections(sections)`      — same merge/split pipeline, but starting
  from an already-segmented list of (heading, text) pairs. Used for arXiv
  papers, where the HTML->text extraction already gives us one Section per
  <h1-4>; we still want tiny sub-subsections merged into a sibling and huge
  sections split.
- question-namespace docs (one HF dataset row = one chunk) deliberately do
  NOT go through this module — they are wrapped as a single chunk directly
  by the CLI, per the task's "one item per chunk" rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import count_tokens

MIN_TOKENS = 80
MAX_TOKENS = 800

_FENCE_RE = re.compile(r"^(```|~~~)")
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_RST_UNDERLINE_RE = re.compile(r"^\s*([=\-~^\"#*+.:'`])\1{2,}\s*$")
_TOP_LEVEL_LIST_MARKER_RE = re.compile(r"^([-*+]|\d+[.)])\s+")


@dataclass
class Section:
    heading_path: list[str] = field(default_factory=list)
    text: str = ""


# ---------------------------------------------------------------------------
# 1. split markdown/rst into heading-delimited sections
# ---------------------------------------------------------------------------


def split_markdown_into_sections(text: str) -> list[Section]:
    """ATX (`# heading`) markdown headings, skipping anything inside fenced
    code blocks. Also recognizes simple RST underline-style headings as a
    best-effort fallback (one level per distinct underline character seen,
    in order of first appearance — RST doesn't encode level in the
    character itself, unlike markdown's '#' count)."""
    lines = text.split("\n")
    sections: list[Section] = []
    stack: list[tuple] = []  # (level, heading_text)
    current_lines: list[str] = []
    in_fence = False
    rst_level_for_char: dict = {}

    def flush():
        body = "\n".join(current_lines).strip()
        if body:
            sections.append(Section(heading_path=[h for _, h in stack], text=body))

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if _FENCE_RE.match(stripped):
            in_fence = not in_fence
            current_lines.append(line)
            i += 1
            continue

        if not in_fence:
            m = _ATX_HEADING_RE.match(line)
            if m:
                flush()
                current_lines = []
                level = len(m.group(1))
                heading_text = m.group(2).strip().rstrip("#").strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading_text))
                i += 1
                continue

            # RST underline heading: previous non-blank line + this underline line
            if (
                i + 1 is not None
                and _RST_UNDERLINE_RE.match(line)
                and current_lines
                and current_lines[-1].strip()
                and len(line.strip()) >= max(3, len(current_lines[-1].strip()) * 0.6)
            ):
                heading_text = current_lines[-1].strip()
                current_lines = current_lines[:-1]
                char = line.strip()[0]
                if char not in rst_level_for_char:
                    rst_level_for_char[char] = len(rst_level_for_char) + 1
                level = rst_level_for_char[char]
                flush()
                current_lines = []
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading_text))
                i += 1
                continue

        current_lines.append(line)
        i += 1

    flush()
    if not sections:
        whole = text.strip()
        if whole:
            sections = [Section(heading_path=[], text=whole)]
    return sections


# ---------------------------------------------------------------------------
# 2. merge tiny sections into a neighbour
# ---------------------------------------------------------------------------


def _with_heading(sec: Section, target_path: list[str]) -> str:
    """Text of a section that is being merged into another one: keeps its own
    heading as a line (otherwise e.g. a config option's name would vanish)."""
    if sec.heading_path and sec.heading_path != target_path:
        return f"{'#' * min(len(sec.heading_path), 6)} {sec.heading_path[-1]}\n\n{sec.text}"
    return sec.text


def merge_tiny_sections(sections: list[Section], min_tokens: int = MIN_TOKENS) -> list[Section]:
    if not sections:
        return sections
    merged: list[Section] = []
    pending_prefix: list[Section] = []

    for sec in sections:
        if count_tokens(sec.text) < min_tokens:
            if merged:
                merged[-1].text = (
                    merged[-1].text + "\n\n" + _with_heading(sec, merged[-1].heading_path)
                )
            else:
                pending_prefix.append(sec)
            continue
        text = sec.text
        if pending_prefix:
            text = (
                "\n\n".join(_with_heading(p, sec.heading_path) for p in pending_prefix)
                + "\n\n"
                + text
            )
            pending_prefix = []
        merged.append(Section(heading_path=sec.heading_path, text=text))

    if pending_prefix:
        leftover_path = pending_prefix[0].heading_path
        leftover = "\n\n".join(
            _with_heading(p, merged[-1].heading_path if merged else leftover_path)
            for p in pending_prefix
        )
        if merged:
            merged[-1].text = merged[-1].text + "\n\n" + leftover
        else:
            merged.append(
                Section(
                    heading_path=leftover_path, text="\n\n".join(p.text for p in pending_prefix)
                )
            )

    return merged


# ---------------------------------------------------------------------------
# 3. split oversized sections on paragraph boundaries, respecting fences/tables
# ---------------------------------------------------------------------------


def split_table_block(table: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    """An oversized pipe table is split *between rows* into several complete
    tables, each repeating the header row — never inside a row, so every piece
    remains a valid, self-contained table."""
    rows = table.split("\n")
    if count_tokens(table) <= max_tokens or len(rows) < 4:
        return [table]
    # header = first row + alignment row (when present)
    has_sep = bool(re.match(r"^\s*\|?\s*:?-{3,}", rows[1])) if len(rows) > 1 else False
    header = rows[:2] if has_sep else rows[:1]
    body = rows[len(header) :]
    head_tokens = count_tokens("\n".join(header))
    pieces: list[str] = []
    cur: list[str] = []
    cur_tokens = head_tokens
    for r in body:
        rt = count_tokens(r)
        if cur and cur_tokens + rt > max_tokens:
            pieces.append("\n".join(header + cur))
            cur, cur_tokens = [], head_tokens
        cur.append(r)
        cur_tokens += rt
    if cur:
        pieces.append("\n".join(header + cur))
    return pieces


FENCE_SPLIT_FACTOR = 2


def split_fence_block(fence: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    """Fenced blocks stay atomic *unless* they exceed FENCE_SPLIT_FACTOR x
    max_tokens (huge data dumps / generated listings). Those are split on line
    boundaries (preferring blank lines), each piece re-wrapped in its own
    opening/closing fence so every piece is still a valid fenced block."""
    if count_tokens(fence) <= max_tokens * FENCE_SPLIT_FACTOR:
        return [fence]
    lines = fence.split("\n")
    if len(lines) < 4:
        return [fence]
    opener, closer = lines[0], lines[-1] if _FENCE_RE.match(lines[-1].strip()) else ""
    body = lines[1:-1] if closer else lines[1:]
    budget = max_tokens - count_tokens(opener) - 2
    pieces: list[str] = []
    cur: list[str] = []
    cur_t = 0
    for ln in body:
        lt = count_tokens(ln) + 1
        if cur and cur_t + lt > budget:
            pieces.append("\n".join([opener] + cur + [closer or opener.strip()[:3]]))
            cur, cur_t = [], 0
        cur.append(ln)
        cur_t += lt
    if cur:
        pieces.append("\n".join([opener] + cur + [closer or opener.strip()[:3]]))
    return pieces


def _maybe_split_list_block(paragraph: str) -> list[str]:
    """A "paragraph" with no blank lines can still be a long bulleted/numbered
    list (common in nav pages, changelogs). Splitting is still "on paragraph
    boundaries" in spirit: each top-level list item is its own unit; indented
    continuation lines stay attached to the item above them."""
    lines = paragraph.split("\n")
    marker_line_idx = [i for i, ln in enumerate(lines) if _TOP_LEVEL_LIST_MARKER_RE.match(ln)]
    if len(marker_line_idx) < 2 or len(marker_line_idx) < len(lines) * 0.15:
        return [paragraph]

    items: list[str] = []
    current: list[str] = []
    for i, line in enumerate(lines):
        if i in marker_line_idx and current:
            items.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        items.append("\n".join(current))
    return items


def split_into_blocks(text: str) -> list[str]:
    """Breaks text into atomic blocks: a paragraph (or one top-level list
    item within a long list), OR a whole fenced code block, OR a whole
    contiguous table — never splitting inside a fence/table."""
    lines = text.split("\n")
    blocks: list[str] = []
    buf: list[str] = []

    def flush_buf():
        nonlocal buf
        joined = "\n".join(buf).strip()
        if joined:
            blocks.extend(_maybe_split_list_block(joined))
        buf = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if _FENCE_RE.match(stripped):
            flush_buf()
            fence_lines = [line]
            i += 1
            while i < n and not _FENCE_RE.match(lines[i].strip()):
                fence_lines.append(lines[i])
                i += 1
            if i < n:
                fence_lines.append(lines[i])
                i += 1
            blocks.extend(split_fence_block("\n".join(fence_lines)))
            continue

        if _TABLE_ROW_RE.match(line):
            flush_buf()
            table_lines = [line]
            i += 1
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                table_lines.append(lines[i])
                i += 1
            blocks.extend(split_table_block("\n".join(table_lines)))
            continue

        if stripped == "":
            flush_buf()
            i += 1
            continue

        buf.append(line)
        i += 1

    flush_buf()
    return blocks


def pack_blocks(blocks: list[str], max_tokens: int = MAX_TOKENS) -> list[str]:
    """Greedily packs atomic blocks into ~max_tokens-sized chunks, carrying
    the last block of a chunk forward as a small overlap into the next one."""
    if not blocks:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for block in blocks:
        bt = count_tokens(block)
        if current and current_tokens + bt > max_tokens:
            chunks.append("\n\n".join(current))
            overlap_block = current[-1]
            # Overlap is a *small* context carry-over: a huge block (table piece,
            # long code fence) would otherwise double the next chunk's size.
            if count_tokens(overlap_block) <= max_tokens // 3:
                current = [overlap_block, block]
                current_tokens = count_tokens(overlap_block) + bt
            else:
                current = [block]
                current_tokens = bt
        else:
            current.append(block)
            current_tokens += bt

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def split_oversized_section(section: Section, max_tokens: int = MAX_TOKENS) -> list[Section]:
    if count_tokens(section.text) <= max_tokens:
        return [section]
    blocks = split_into_blocks(section.text)
    pieces = pack_blocks(blocks, max_tokens=max_tokens)
    if not pieces:
        return [section]
    return [Section(heading_path=section.heading_path, text=p) for p in pieces]


# ---------------------------------------------------------------------------
# public pipeline
# ---------------------------------------------------------------------------


def chunk_sections(
    sections: list[Section], min_tokens: int = MIN_TOKENS, max_tokens: int = MAX_TOKENS
) -> list[Section]:
    merged = merge_tiny_sections(sections, min_tokens=min_tokens)
    out: list[Section] = []
    for sec in merged:
        out.extend(split_oversized_section(sec, max_tokens=max_tokens))
    return out


def chunk_markdown_doc(
    text: str, min_tokens: int = MIN_TOKENS, max_tokens: int = MAX_TOKENS
) -> list[Section]:
    sections = split_markdown_into_sections(text)
    return chunk_sections(sections, min_tokens=min_tokens, max_tokens=max_tokens)
