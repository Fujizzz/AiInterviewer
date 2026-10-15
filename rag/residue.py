"""Residue scanner: counts leftover markup patterns in chunk files.

    python -m rag.residue [source ...]        # default: every data/chunks/*.jsonl

Patterns (counted as number of *chunks* containing at least one hit; fenced
code is excluded for the markup patterns, since code may legitimately show them):

  rst_role        :ref:`x`, :class:`y` ...
  rst_directive   `.. name::` lines
  tab_selected    d2l `tab.selected(...)`
  cjk             >= 8 CJK / kana / hangul characters in prose (non-English leak; short glosses
  ignored)
  html_tag        known HTML tags outside fences
  triple_colon    `:::` admonition / mkdocstrings lines
  snippet_marker  `--8<--`
  long_line       any line > 900 chars (outside fences)
  fence_unbalanced  odd number of fence lines (orphan opening/closing fence)
  heading_junk    markdown heading with no letter/digit at all (e.g. ")", "-----"); single letters
                  (glossary "## A"), "C++", "1E + 1P" are real headings and not flagged
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CHUNKS_DIR = Path(__file__).resolve().parent / "data" / "chunks"

_FENCE = re.compile(r"^\s*(```|~~~)")
_PATTERNS = {
    "rst_role": re.compile(r":[a-zA-Z][\w:]*:`"),
    "rst_directive": re.compile(r"^\s*\.\. [\w:-]+::", re.M),
    "tab_selected": re.compile(r"tab\.selected"),
    "html_tag": re.compile(
        r"</?(?:div|img|table|tr|td|th|figure|figcaption|a|p|ul|ol|li|span|br|code|pre|details|summary|iframe|center|font|svg)\b[^>\n]*>",
        re.I,
    ),
    "triple_colon": re.compile(r"^\s*:::", re.M),
    "snippet_marker": re.compile(r"--8<--"),
    "rst_hyperlink": re.compile(r"`[^`\n]+<https?://[^>\n]+>`__?"),
    "invisible_block": re.compile(r"INVISIBLE_CODE_BLOCK"),
}
# A 2-4 character gloss (`hard to understand (很难理解)`) is legitimate; a translated
# README/page is not. Chunks with fewer than this many CJK chars in prose are not flagged.
CJK_MIN_CHARS = 8
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.M)


def split_fences(text: str):
    """Returns (prose_text, fence_line_count)."""
    prose, in_fence, n = [], False, 0
    for ln in text.split("\n"):
        if _FENCE.match(ln):
            in_fence = not in_fence
            n += 1
            continue
        if not in_fence:
            prose.append(ln)
    return "\n".join(prose), n


_INLINE_CODE = re.compile(
    r"`[^`\n]*(?:\n[^`\n]+)?`"
)  # markdown inline code may wrap over one line break


def scan_text(text: str, heading_path=None) -> list[str]:
    prose, nfence = split_fences(text)
    prose_nocode = _INLINE_CODE.sub("", prose)  # tags shown inside `inline code` are legitimate
    hits = [name for name, rx in _PATTERNS.items() if rx.search(prose_nocode)]
    if _PATTERNS["tab_selected"].search(text) and "tab_selected" not in hits:
        hits.append("tab_selected")  # also inside code fences
    if len(_CJK.findall(prose)) >= CJK_MIN_CHARS:  # CJK inside code fences is a legitimate example
        hits.append("cjk")
    long_lines = [ln for ln in text.split("\n") if len(ln) > 900]
    if any(len(ln) > 900 for ln in prose.split("\n")):
        hits.append("long_line")
    if any(
        ln.lstrip().startswith("|") or len(re.findall(r"[;{}()=<>]", ln)) / len(ln) > 0.04
        for ln in long_lines
    ):
        hits.append("long_code_line")  # table rows / code flattened onto one line (not plain prose)
    if nfence % 2:
        hits.append("fence_unbalanced")
    headings = [m.group(1).strip() for m in _HEADING.finditer(prose)] + [
        h.strip() for h in (heading_path or [])
    ]
    if any(not re.search(r"[A-Za-z0-9]", h) for h in headings if h):
        hits.append("heading_junk")
    return hits


def scan_file(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {"chunks": 0}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            counts["chunks"] += 1
            for h in scan_text(row["text"], row.get("heading_path")):
                counts[h] = counts.get(h, 0) + 1
    return counts


def main(argv: list[str]) -> int:
    files = (
        [CHUNKS_DIR / f"{a}.jsonl" for a in argv] if argv else sorted(CHUNKS_DIR.glob("*.jsonl"))
    )
    cols = [
        "rst_role",
        "rst_directive",
        "rst_hyperlink",
        "tab_selected",
        "cjk",
        "html_tag",
        "triple_colon",
        "snippet_marker",
        "long_line",
        "long_code_line",
        "fence_unbalanced",
        "heading_junk",
    ]
    print(f"{'source':32}{'chunks':>7} " + " ".join(f"{c[:9]:>9}" for c in cols))
    for f in files:
        c = scan_file(f)
        print(f"{f.stem:32}{c['chunks']:>7} " + " ".join(f"{c.get(k, 0):>9}" for k in cols))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
