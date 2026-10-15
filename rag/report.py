"""Per-run stats: files processed, chunk counts, token-count distribution,
metadata completeness, validation failures, sample chunk_ids. Writes both a
human-readable .md and a machine-readable .json per run.
"""

from __future__ import annotations

import json
import random
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

METADATA_FIELDS = [
    "title",
    "heading_path",
    "domain",
    "topic",
    "related_technologies",
    "competency",
    "difficulty",
    "trust_level",
    "license",
    "doc_version",
]


_FENCE_LINE = re.compile(r"^\s*(```|~~~)", re.M)


def count_fence_lines(text: str) -> int:
    return len(_FENCE_LINE.findall(text))


@dataclass
class SourceStats:
    source_name: str
    files_processed: int = 0
    chunk_count: int = 0
    token_counts: list[int] = field(default_factory=list)
    validation_failures: int = 0
    validation_errors: list[str] = field(default_factory=list)
    fetch_errors: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None
    sample_chunk_ids: list[str] = field(default_factory=list)
    field_completeness: dict[str, float] = field(default_factory=dict)
    fence_mismatches: list[str] = field(default_factory=list)
    junk_dropped_count: int = 0
    fetch_notes: list[str] = field(default_factory=list)
    junk_by_category: dict[str, int] = field(default_factory=dict)
    fence_unbalanced_chunks: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = {
            "source_name": self.source_name,
            "files_processed": self.files_processed,
            "chunk_count": self.chunk_count,
            "validation_failures": self.validation_failures,
            "validation_errors": self.validation_errors[:20],
            "fetch_errors": self.fetch_errors,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "sample_chunk_ids": self.sample_chunk_ids,
            "field_completeness": self.field_completeness,
            "fence_parity_mismatches": len(self.fence_mismatches),
            "fence_parity_mismatch_details": self.fence_mismatches[:20],
            "junk_dropped_count": self.junk_dropped_count,
            "junk_by_category": self.junk_by_category,
            "chunks_with_unbalanced_fences": self.fence_unbalanced_chunks,
            "fetch_notes": self.fetch_notes,
        }
        if self.token_counts:
            sorted_tc = sorted(self.token_counts)
            d["token_count_stats"] = {
                "mean": round(statistics.mean(sorted_tc), 1),
                "median": round(statistics.median(sorted_tc), 1),
                "p95": round(_percentile(sorted_tc, 0.95), 1),
                "min": sorted_tc[0],
                "max": sorted_tc[-1],
            }
        else:
            d["token_count_stats"] = None
        return d


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def compute_field_completeness(chunk_dicts: list[dict]) -> dict[str, float]:
    if not chunk_dicts:
        return {f: 0.0 for f in METADATA_FIELDS}
    n = len(chunk_dicts)
    out = {}
    for f in METADATA_FIELDS:
        non_empty = 0
        for c in chunk_dicts:
            v = c.get(f)
            if v is None:
                continue
            if isinstance(v, (list, str)) and len(v) == 0:
                continue
            non_empty += 1
        out[f] = round(non_empty / n, 3)
    return out


def build_source_stats(
    source_name: str,
    files_processed: int,
    chunk_dicts: list[dict],
    validation_failures: int,
    validation_errors: list[str],
    fetch_errors: list[str],
    skipped: bool = False,
    skip_reason: str | None = None,
    fence_mismatches: list[str] | None = None,
    junk_dropped_count: int = 0,
    fetch_notes: list[str] | None = None,
    junk_by_category: dict[str, int] | None = None,
) -> SourceStats:
    stats = SourceStats(
        source_name=source_name,
        files_processed=files_processed,
        chunk_count=len(chunk_dicts),
        token_counts=[c.get("token_count", 0) for c in chunk_dicts],
        validation_failures=validation_failures,
        validation_errors=validation_errors,
        fetch_errors=fetch_errors,
        skipped=skipped,
        skip_reason=skip_reason,
        fence_mismatches=fence_mismatches or [],
        junk_dropped_count=junk_dropped_count,
        fetch_notes=fetch_notes or [],
        junk_by_category=junk_by_category or {},
    )
    ids = [c["chunk_id"] for c in chunk_dicts if c.get("chunk_id")]
    stats.sample_chunk_ids = random.sample(ids, min(3, len(ids))) if ids else []
    stats.field_completeness = compute_field_completeness(chunk_dicts)
    stats.fence_unbalanced_chunks = sum(
        1 for c in chunk_dicts if count_fence_lines(c.get("text", "")) % 2
    )
    return stats


def write_report(
    all_stats: list[SourceStats], reports_dir: Path, timestamp: str
) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / f"{timestamp}.json"
    md_path = reports_dir / f"{timestamp}.md"

    json_payload = {"timestamp": timestamp, "sources": [s.to_dict() for s in all_stats]}
    json_path.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [f"# Pipeline report — {timestamp}", ""]
    total_chunks = sum(s.chunk_count for s in all_stats)
    total_failures = sum(s.validation_failures for s in all_stats)
    lines.append(f"**Total chunks produced:** {total_chunks}  ")
    lines.append(f"**Total validation failures:** {total_failures}")
    lines.append("")

    for s in all_stats:
        lines.append(f"## {s.source_name}")
        if s.skipped:
            lines.append(f"- **skipped**: {s.skip_reason}")
            lines.append("")
            continue
        lines.append(f"- files processed: {s.files_processed}")
        lines.append(f"- chunk count: {s.chunk_count}")
        if s.token_counts:
            sorted_tc = sorted(s.token_counts)
            lines.append(
                f"- token count — mean: {round(statistics.mean(sorted_tc), 1)}, "
                f"median: {round(statistics.median(sorted_tc), 1)}, "
                f"p95: {round(_percentile(sorted_tc, 0.95), 1)}, "
                f"min: {sorted_tc[0]}, max: {sorted_tc[-1]}"
            )
        else:
            lines.append("- token count: n/a (no chunks)")
        lines.append(f"- validation failures: {s.validation_failures}")
        if s.fetch_errors:
            lines.append(f"- fetch errors: {len(s.fetch_errors)} (see json report for details)")
        lines.append(
            "- junk chunks dropped (doc-tool directives / nav boilerplate only): "
            f"{s.junk_dropped_count}"
        )
        if s.junk_by_category:
            lines.append(
                "  - by category: "
                + ", ".join(f"{k}={v}" for k, v in sorted(s.junk_by_category.items()))
            )
        for note in s.fetch_notes:
            lines.append(f"- fetch note: {note}")
        lines.append(
            "- chunks with unbalanced fences (odd fence-line count = orphan opening/closing "
            f"fence): {s.fence_unbalanced_chunks}"
        )
        lines.append(
            "- fence parity mismatches (raw vs. cleaned ``` /~~~ marker count differs): "
            f"{len(s.fence_mismatches)}"
        )
        lines.append("- metadata completeness:")
        for field_name, rate in s.field_completeness.items():
            lines.append(f"  - {field_name}: {rate * 100:.1f}%")
        lines.append(
            f"- sample chunk_ids: {', '.join(s.sample_chunk_ids) if s.sample_chunk_ids else 'n/a'}"
        )
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": json_path, "md": md_path}
