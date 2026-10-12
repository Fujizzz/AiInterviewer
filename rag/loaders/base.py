"""Shared helpers + the no-op stub for loaders that are not implemented yet."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..registry import Source


@dataclass
class RawDoc:
    """One fetched document, before cleaning/chunking."""

    path: str  # relative path within the source (used for chunk_id + display)
    text: str  # raw markdown/rst/text content
    doc_version: str  # commit hash / dataset revision / arXiv version
    source_url: str  # permalink
    title: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    source_name: str
    docs: list[RawDoc] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None
    errors: list[str] = field(default_factory=list)
    # populated by do_clean(): per-file "raw fence count != clean fence count"
    # warnings, i.e. cleaning may have damaged a fenced code block.
    fence_mismatches: list[str] = field(default_factory=list)
    # populated by do_chunk(): human-readable notes about chunks that were
    # dropped as doc-tool-directive-only junk (count + a few examples).
    junk_dropped_notes: list[str] = field(default_factory=list)
    # informational (non-error) fetch notes: filter counts, snippet-include
    # summaries, crawl stats, etc. Shown in the report as "fetch notes".
    notes: list[str] = field(default_factory=list)
    # populated by do_chunk(): {category: count} of chunks dropped by the junk filter
    junk_by_category: dict = field(default_factory=dict)
    # populated by do_chunk(): the dropped chunks themselves (audit trail), each with a `reason`
    dropped_chunks: list = field(default_factory=list)


def stub_fetch(source: Source, raw_dir: Path) -> FetchResult:
    """Used by crawler/manual loaders that are not implemented yet.

    Always prints a clear message and returns an empty, explicitly-skipped
    result so downstream stages (clean/chunk/report) can treat it honestly
    as "not fetched" rather than silently producing zero chunks.
    """
    reason = (
        f"loader '{source.loader}' not implemented / manual for source '{source.name}' — skipping"
    )
    print(f"[fetch] {reason}")
    return FetchResult(source_name=source.name, docs=[], skipped=True, skip_reason=reason)
