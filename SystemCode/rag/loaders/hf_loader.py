"""HuggingFace dataset loader.

Scoped to the `question` namespace use case described in the task brief:
one chunk per problem (title + description go into `text`; difficulty and
tags are carried as metadata, never full test cases). Downloads the
dataset's .jsonl siblings directly via the HF resolve API (no `datasets`
library materialization needed for this shape of data) and records the
repo's current git sha as `doc_version`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import requests

from ..registry import Source
from .base import FetchResult, RawDoc

HF_API = "https://huggingface.co/api/datasets/{repo_id}"
HF_RESOLVE = "https://huggingface.co/datasets/{repo_id}/resolve/{revision}/{filename}"


def _repo_id_from_url(url: str) -> str:
    marker = "/datasets/"
    idx = url.find(marker)
    if idx == -1:
        raise ValueError(f"not a huggingface.co/datasets url: {url!r}")
    return url[idx + len(marker) :].rstrip("/")


def fetch(source: Source, raw_dir: Path) -> FetchResult:
    if not source.url or "huggingface.co/datasets/" not in source.url:
        reason = f"hf loader requires a huggingface.co/datasets url, got {source.url!r}"
        print(f"[fetch] {reason}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=reason)

    repo_id = _repo_id_from_url(source.url)
    errors: list[str] = []

    try:
        info_resp = requests.get(HF_API.format(repo_id=repo_id), timeout=30)
        info_resp.raise_for_status()
        info = info_resp.json()
    except Exception as e:
        msg = f"HF metadata fetch failed for {repo_id}: {e}"
        print(f"[fetch] ERROR {source.name}: {msg}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=msg, errors=[msg])

    revision = info.get("sha")
    if not revision:
        msg = f"HF dataset {repo_id} has no 'sha' in API response: {info}"
        print(f"[fetch] ERROR {source.name}: {msg}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=msg, errors=[msg])

    siblings = [s["rfilename"] for s in info.get("siblings", [])]
    jsonl_files = [f for f in siblings if f.endswith(".jsonl")]
    wanted = source.options.get("files")
    if wanted:
        jsonl_files = [f for f in jsonl_files if f in wanted]
    if not jsonl_files:
        msg = (
            f"no .jsonl files in dataset {repo_id} (found: {siblings}); "
            "parquet/arrow formats are not yet supported by this loader"
        )
        print(f"[fetch] {msg}")
        return FetchResult(source_name=source.name, skipped=True, skip_reason=msg, errors=errors)

    out_dir = raw_dir / source.name
    out_dir.mkdir(parents=True, exist_ok=True)

    docs: list[RawDoc] = []
    seen_slugs = set()
    n_dupe_rows = 0
    for fname in jsonl_files:
        url = HF_RESOLVE.format(repo_id=repo_id, revision=revision, filename=fname)
        local_path = out_dir / fname
        try:
            if not local_path.exists():
                print(f"[fetch] downloading {url}")
                resp = requests.get(url, timeout=180, stream=True)
                resp.raise_for_status()
                with open(local_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
        except Exception as e:
            errors.append(f"{fname}: download failed ({e})")
            print(f"[fetch] ERROR downloading {fname}: {e}")
            continue

        with open(local_path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception as e:
                    errors.append(f"{fname}:{i}: bad json ({e})")
                    continue
                doc = _row_to_rawdoc(row, repo_id, revision, fname, i, url)
                if doc is not None:
                    slug = doc.extra.get("slug")
                    if slug:
                        if slug in seen_slugs:  # same problem repeated inside one source
                            n_dupe_rows += 1
                            continue
                        seen_slugs.add(slug)
                    docs.append(doc)

    print(
        f"[fetch] {source.name}: {len(docs)} rows from {len(jsonl_files)} file(s), "
        f"revision={revision[:12]}"
    )
    notes = (
        [f"{n_dupe_rows} repeated-slug row(s) dropped inside this source"] if n_dupe_rows else []
    )
    return FetchResult(source_name=source.name, docs=docs, errors=errors, notes=notes)


def _row_to_rawdoc(
    row: dict, repo_id: str, revision: str, fname: str, idx: int, file_url: str
) -> RawDoc | None:
    raw_title = row.get("title") or row.get("task_id") or row.get("slug")
    description = (
        row.get("problem_description")
        or row.get("problem")
        or row.get("description")
        or row.get("content")
        or ""
    )
    if not description.strip():
        return None

    difficulty = row.get("difficulty")
    tags = row.get("tags") or row.get("topics") or []
    if isinstance(tags, str):
        try:
            parsed = ast.literal_eval(tags)
            tags = [str(t) for t in parsed] if isinstance(parsed, (list, tuple)) else [str(parsed)]
        except (ValueError, SyntaxError):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
    slug = row.get("task_id") or row.get("slug")

    readable_title = (
        str(raw_title).replace("-", " ").replace("_", " ").strip().title()
        if raw_title
        else f"Problem {idx}"
    )
    text = f"{readable_title}\n\n{description.strip()}"

    return RawDoc(
        path=f"{fname}:{idx}",
        text=text,
        doc_version=revision,
        source_url=file_url,
        title=readable_title,
        extra={
            "difficulty": difficulty,
            "tags": tags,
            "slug": slug,
            "metadata": {
                k: v
                for k, v in {
                    "slug": slug,
                    "question_id": row.get("question_id"),
                    "origin": row.get("source"),
                }.items()
                if v not in (None, "")
            },
        },
    )
