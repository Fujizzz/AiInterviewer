"""python -m rag.cli fetch|clean|chunk|report|all --source NAME | --priority P0

Pipeline stages:
  fetch  -> loaders download raw docs into data/raw/<source>/
  clean  -> markdown cleanup, mirrored into data/clean/<source>/
  chunk  -> structure-aware chunking + schema validation into
            data/chunks/<source>.jsonl
  report -> stats (token-count distribution, metadata completeness,
            validation failures, sample chunk_ids) into
            data/reports/<timestamp>.md + .json
  all    -> fetch + clean + chunk + report, for one run

Index/retrieval commands (rag.index / rag.retrieval / rag.eval):
  migrate | embed [--source ..|--all] | index [--source ..|--all]
  search "query" --role evaluator [--k 5] [--rerank] | eval
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from . import chunking
from .clean import clean_markdown, count_fences, dedupe_chunks, junk_category
from .loaders import arxiv_loader, crawler_loader, git_loader, hf_loader, xlsx_loader
from .loaders.base import FetchResult, RawDoc, stub_fetch
from .profiles import apply_profile
from .registry import Source, filter_sources, load_registry, validate_registry
from .report import SourceStats, build_source_stats, write_report
from .schema import (
    Chunk,
    Namespace,
    SourceType,
    TrustLevel,
    count_tokens,
    make_chunk_id,
    utc_now_iso,
)

MODULE_ROOT = Path(__file__).resolve().parent  # the repo's rag/ directory
PROJECT_ROOT = MODULE_ROOT
DATA_DIR = MODULE_ROOT / "data"
SOURCES_YAML = DATA_DIR / "sources.yaml"
RAW_DIR = DATA_DIR / "raw"
CLEAN_DIR = DATA_DIR / "clean"
CHUNKS_DIR = DATA_DIR / "chunks"
REPORTS_DIR = DATA_DIR / "reports"

LOADER_MODULES = {
    "git": git_loader,
    "hf": hf_loader,
    "arxiv": arxiv_loader,
    "crawler": crawler_loader,
    "xlsx": xlsx_loader,
}

SOURCE_TYPE_BY_LOADER = {
    "git": SourceType.GIT_DOCS,
    "hf": SourceType.DATASET,
    "arxiv": SourceType.PAPER,
    "crawler": SourceType.CRAWL,
    "manual": SourceType.MANUAL,
    "xlsx": SourceType.DATASET,
}


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


def do_fetch(source: Source) -> FetchResult:
    module = LOADER_MODULES.get(source.loader)
    if module is None:
        return stub_fetch(source, RAW_DIR)
    return module.fetch(source, RAW_DIR)


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


def do_clean(source: Source, fetched: FetchResult) -> FetchResult:
    if fetched.skipped or not fetched.docs:
        return fetched

    out_dir = CLEAN_DIR / source.name
    out_dir.mkdir(parents=True, exist_ok=True)

    cleaned_docs: list[RawDoc] = []
    fence_mismatches: list[str] = []
    for doc in fetched.docs:
        if source.loader in ("git", "crawler"):
            pre = apply_profile(
                doc.text, source.clean_profile, path=doc.path, options=source.options
            )
            # fence parity is measured across the *generic* cleaner only: profile
            # conversions (rst code-blocks -> fences, d2l tab filtering) change
            # fence counts by design and are covered by unit tests instead.
            raw_fence_count = count_fences(pre)
            text = clean_markdown(pre, source_name=f"{source.name}:{doc.path}")
        else:
            raw_fence_count = count_fences(doc.text)
            text = doc.text
        if not text.strip():
            continue

        clean_fence_count = count_fences(text)
        if raw_fence_count != clean_fence_count:
            msg = (
                f"{doc.path}: fence count raw={raw_fence_count} clean={clean_fence_count} "
                "(cleaning may have damaged a code block)"
            )
            fence_mismatches.append(msg)
            print(f"[clean] WARNING fence-parity mismatch in {msg}")

        cleaned_docs.append(
            RawDoc(
                path=doc.path,
                text=text,
                doc_version=doc.doc_version,
                source_url=doc.source_url,
                title=doc.title,
                extra=doc.extra,
            )
        )
        safe_name = doc.path.replace("/", "__").replace(":", "_") + ".txt"
        try:
            (out_dir / safe_name).write_text(text, encoding="utf-8")
        except OSError as e:
            print(f"[clean] WARNING could not write {safe_name}: {e}")

    return FetchResult(
        source_name=source.name,
        docs=cleaned_docs,
        errors=fetched.errors,
        fence_mismatches=fence_mismatches,
    )


# ---------------------------------------------------------------------------
# chunk
# ---------------------------------------------------------------------------


def _build_chunk(
    source: Source,
    namespace: Namespace,
    source_type: SourceType,
    trust_level: TrustLevel,
    path: str,
    heading_path: list[str],
    idx: int,
    text: str,
    title: str | None,
    source_url: str,
    doc_version: str | None,
    topic: str | None = None,
    difficulty: str | None = None,
    related_technologies: list[str] | None = None,
    metadata: dict | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=make_chunk_id(source.name, path, heading_path, idx),
        namespace=namespace,
        text=text,
        title=title,
        heading_path=heading_path,
        source_name=source.name,
        source_type=source_type,
        source_url=source_url,
        doc_version=doc_version,
        domain=source.domain,
        topic=topic,
        related_technologies=(
            related_technologies
            if related_technologies is not None
            else list(source.related_technologies or [])
        ),
        competency=None,
        difficulty=difficulty,
        trust_level=trust_level,
        license=source.license,
        token_count=count_tokens(text),
        ingested_at=utc_now_iso(),
        metadata=metadata or {},
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _dedupe_against_primary(
    source: Source, primary: str, chunk_objs: list[Chunk], cleaned: FetchResult
) -> tuple[list[Chunk], list[str]]:
    """Cross-source dedupe by problem slug: drop chunks whose slug already
    exists in the primary source's chunk file. Returns (kept, overlapping_slugs)."""
    primary_slugs = {
        (c.get("metadata") or {}).get("slug") for c in _read_jsonl(CHUNKS_DIR / f"{primary}.jsonl")
    } - {None}
    if not primary_slugs:
        cleaned.notes.append(
            f"dedupe_against={primary}: primary chunk file missing/empty (run {primary} first) — "
            "NO cross-source dedupe applied"
        )
        return chunk_objs, []
    kept, overlap = [], []
    for c in chunk_objs:
        slug = (c.metadata or {}).get("slug")
        if slug and slug in primary_slugs:
            overlap.append(slug)
        else:
            kept.append(c)
    cleaned.notes.append(
        f"dedupe_against={primary}: {len(overlap)} of {len(chunk_objs)} problem(s) already in "
        f"{primary} "
        f"(by slug) dropped; {len(kept)} unique problem(s) kept"
    )
    print(
        f"[chunk] {source.name}: {len(overlap)} problems overlap with {primary}, {len(kept)} "
        "unique kept"
    )
    return kept, overlap


def _merge_provenance(primary: str, slugs: list[str], from_source: str) -> None:
    """Records on the primary chunk that the same problem also appears in
    `from_source` (metadata.also_in_sources). Idempotent."""
    path = CHUNKS_DIR / f"{primary}.jsonl"
    rows = _read_jsonl(path)
    wanted = set(slugs)
    changed = 0
    for r in rows:
        md = r.setdefault("metadata", {})
        if md.get("slug") in wanted:
            also = set(md.get("also_in_sources") or [])
            if from_source not in also:
                also.add(from_source)
                md["also_in_sources"] = sorted(also)
                changed += 1
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


def do_chunk(source: Source, cleaned: FetchResult) -> tuple[list[dict], list[str], int, int]:
    if source.reference_only:
        msg = f"source '{source.name}' is reference_only=true — refusing to chunk full text"
        print(f"[chunk] {msg}")
        return [], [msg], 0, 0

    if cleaned.skipped or not cleaned.docs:
        return [], [], 0, 0

    namespace = Namespace(source.namespace)
    source_type = SOURCE_TYPE_BY_LOADER.get(source.loader, SourceType.MANUAL)
    trust_level = TrustLevel(source.trust_level)

    chunk_objs: list[Chunk] = []
    validation_errors: list[str] = []
    validation_failures = 0

    if namespace == Namespace.QUESTION and source.loader == "hf":
        for doc in cleaned.docs:
            heading_path = [doc.title] if doc.title else []
            try:
                chunk_objs.append(
                    _build_chunk(
                        source,
                        namespace,
                        source_type,
                        trust_level,
                        path=doc.path,
                        heading_path=heading_path,
                        idx=0,
                        text=doc.text,
                        title=doc.title,
                        source_url=doc.source_url,
                        doc_version=doc.doc_version,
                        difficulty=doc.extra.get("difficulty"),
                        related_technologies=list(doc.extra.get("tags") or [])
                        or list(source.related_technologies or []),
                        metadata=doc.extra.get("metadata"),
                    )
                )
            except ValidationError as e:
                validation_failures += 1
                validation_errors.append(f"{doc.path}: {e}")

        primary = (source.options or {}).get("dedupe_against")
        if primary:
            chunk_objs, overlap = _dedupe_against_primary(source, primary, chunk_objs, cleaned)
            if overlap:
                _merge_provenance(primary, overlap, source.name)

    elif source.loader == "xlsx":
        for doc in cleaned.docs:
            try:
                chunk_objs.append(
                    _build_chunk(
                        source,
                        namespace,
                        source_type,
                        trust_level,
                        path=doc.path,
                        heading_path=doc.extra.get("heading_path") or [doc.title or ""],
                        idx=0,
                        text=doc.text,
                        title=doc.title,
                        source_url=doc.source_url,
                        doc_version=doc.doc_version,
                        topic=doc.extra.get("role_family"),
                        related_technologies=list(doc.extra.get("tsc_titles") or [])
                        or list(source.related_technologies or []),
                        metadata=doc.extra.get("metadata"),
                    )
                )
            except ValidationError as e:
                validation_failures += 1
                validation_errors.append(f"{doc.path}: {e}")

    elif source.loader == "arxiv":
        sections = [
            chunking.Section(heading_path=[doc.extra.get("heading", "")], text=doc.text)
            for doc in cleaned.docs
            if doc.text.strip()
        ]
        final_sections = chunking.chunk_sections(sections)
        first_doc = cleaned.docs[0]
        for idx, sec in enumerate(final_sections):
            title = sec.heading_path[-1] if sec.heading_path else first_doc.title
            try:
                chunk_objs.append(
                    _build_chunk(
                        source,
                        namespace,
                        source_type,
                        trust_level,
                        path=source.name,
                        heading_path=sec.heading_path,
                        idx=idx,
                        text=sec.text,
                        title=title,
                        source_url=first_doc.source_url,
                        doc_version=first_doc.doc_version,
                        topic=sec.heading_path[0] if sec.heading_path else None,
                    )
                )
            except ValidationError as e:
                validation_failures += 1
                validation_errors.append(f"{source.name}#{idx}: {e}")

    else:
        for doc in cleaned.docs:
            try:
                sections = chunking.chunk_markdown_doc(doc.text)
            except Exception as e:
                validation_errors.append(f"{doc.path}: chunking error ({e})")
                continue
            for idx, sec in enumerate(sections):
                title = (
                    sec.heading_path[-1] if sec.heading_path else (doc.title or Path(doc.path).stem)
                )
                try:
                    chunk_objs.append(
                        _build_chunk(
                            source,
                            namespace,
                            source_type,
                            trust_level,
                            path=doc.path,
                            heading_path=sec.heading_path,
                            idx=idx,
                            text=sec.text,
                            title=title,
                            source_url=doc.source_url,
                            doc_version=doc.doc_version,
                            topic=sec.heading_path[0] if sec.heading_path else None,
                        )
                    )
                except ValidationError as e:
                    validation_failures += 1
                    validation_errors.append(f"{doc.path}#{idx}: {e}")

    texts = [c.text for c in chunk_objs]
    keep_mask = dedupe_chunks(texts)
    deduped = [c for c, keep in zip(chunk_objs, keep_mask, strict=True) if keep]

    junk_dropped = 0
    kept: list[Chunk] = []
    by_cat: dict = {}
    for c in deduped:
        cat = junk_category(c.text, token_count=c.token_count, namespace=c.namespace.value)
        if cat:
            junk_dropped += 1
            by_cat[cat] = by_cat.get(cat, 0) + 1
            row = c.to_jsonl_dict()
            row["reason"] = cat
            cleaned.dropped_chunks.append(row)
            continue
        kept.append(c)
    cleaned.junk_by_category = by_cat
    if junk_dropped:
        print(f"[chunk] {source.name}: dropped {junk_dropped} junk chunk(s) {by_cat}")

    return [c.to_jsonl_dict() for c in kept], validation_errors, validation_failures, junk_dropped


DROPPED_DIR = REPORTS_DIR / "dropped"


def write_dropped_jsonl(source: Source, dropped: list[dict]) -> Path | None:
    """Audit trail: every chunk removed by the junk filter, with a `reason`.
    Categories listed in options.dropped_not_persisted (e.g. cp-algorithms'
    practice-problem link lists) are counted in the report but not written."""
    skip = set((source.options or {}).get("dropped_not_persisted") or [])
    rows = [d for d in dropped if d.get("reason") not in skip]
    path = DROPPED_DIR / f"{source.name}.jsonl"
    if not rows:
        if path.exists():
            path.unlink()
        return None
    DROPPED_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return path


def write_chunks_jsonl(source_name: str, chunk_dicts: list[dict]) -> Path:
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CHUNKS_DIR / f"{source_name}.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for d in chunk_dicts:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return out_path


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def run_source_all(source: Source) -> SourceStats:
    print(f"\n=== {source.name} ({source.loader}, {source.priority}) ===")
    fetched = do_fetch(source)
    cleaned = do_clean(source, fetched)
    chunk_dicts, validation_errors, validation_failures, junk_dropped = do_chunk(source, cleaned)
    if not fetched.skipped:
        write_dropped_jsonl(source, cleaned.dropped_chunks)
    if chunk_dicts:
        write_chunks_jsonl(source.name, chunk_dicts)
        print(f"[chunk] {source.name}: {len(chunk_dicts)} chunks written")
    elif not fetched.skipped and (CHUNKS_DIR / f"{source.name}.jsonl").exists():
        (
            CHUNKS_DIR / f"{source.name}.jsonl"
        ).unlink()  # don't leave a stale file from an earlier run
        print(f"[chunk] {source.name}: 0 chunks — removed stale chunk file")
    return build_source_stats(
        source_name=source.name,
        files_processed=len(fetched.docs),
        chunk_dicts=chunk_dicts,
        validation_failures=validation_failures,
        validation_errors=validation_errors,
        fetch_errors=fetched.errors,
        skipped=fetched.skipped,
        skip_reason=fetched.skip_reason,
        fence_mismatches=cleaned.fence_mismatches,
        junk_dropped_count=junk_dropped,
        fetch_notes=list(fetched.notes) + [n for n in cleaned.notes if n not in fetched.notes],
        junk_by_category=cleaned.junk_by_category,
    )


def report_from_existing(source: Source) -> SourceStats:
    path = CHUNKS_DIR / f"{source.name}.jsonl"
    if not path.exists():
        return build_source_stats(
            source.name, 0, [], 0, [], [], skipped=True, skip_reason="no chunks file found"
        )
    chunk_dicts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunk_dicts.append(json.loads(line))
    return build_source_stats(
        source.name,
        files_processed=-1,
        chunk_dicts=chunk_dicts,
        validation_failures=0,
        validation_errors=[],
        fetch_errors=[],
    )


def select_sources(args: argparse.Namespace) -> list[Source]:
    sources = load_registry(SOURCES_YAML)
    errors = validate_registry(sources)
    if errors:
        print("[registry] validation warnings:")
        for e in errors:
            print(f"  - {e}")
    selected = filter_sources(sources, name=args.source, priority=args.priority)
    if not selected:
        print(f"No sources matched --source={args.source} --priority={args.priority}")
    return selected


CLI_DESCRIPTION = """\
Ingestion pipeline (options below): fetch | clean | chunk | report | all

Index / retrieval commands (each has its own --help):
  migrate   apply schema.sql and migrations to the Postgres at RAG_DATABASE_URL / POSTGRES_*
  embed     embed chunks with bge-m3 into rag/data/embeddings (no database needed)
  index     idempotently load chunks + vectors into Postgres
  search    run one retrieval, e.g.  search "kv cache" --role question_planner
  eval      retrieval evaluation / tuning against the gold sets
"""


def _dispatch_index_commands(argv: list[str]) -> int | None:
    """migrate | embed | index | search | eval live in rag.index / rag.retrieval / rag.eval
    (lazy imports: the fetch/clean/chunk pipeline must not need torch or psycopg)."""
    if not argv or argv[0] not in ("migrate", "embed", "index", "search", "eval"):
        return None
    cmd, rest = argv[0], argv[1:]
    if cmd in ("migrate", "embed", "index"):
        from .index import commands as m
    elif cmd == "search":
        from .retrieval import commands as m
    else:
        from .eval import commands as m
    return getattr(m, f"cmd_{cmd}")(rest)


def main(argv: list[str] | None = None) -> int:
    handled = _dispatch_index_commands(list(sys.argv[1:] if argv is None else argv))
    if handled is not None:
        return handled
    parser = argparse.ArgumentParser(
        prog="python -m rag.cli",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=CLI_DESCRIPTION,
    )
    parser.add_argument("command", choices=["fetch", "clean", "chunk", "report", "all"])
    parser.add_argument(
        "--source",
        default=None,
        help="run one source by name, or multiple as a comma-separated list",
    )
    parser.add_argument(
        "--priority",
        default=None,
        choices=["P0", "P1", "P2"],
        help="run all sources at this priority",
    )
    args = parser.parse_args(argv)

    if args.source and "," in args.source:
        names = [n.strip() for n in args.source.split(",") if n.strip()]
        all_sources = load_registry(SOURCES_YAML)
        sources = [s for s in all_sources if s.name in names]
        missing = set(names) - {s.name for s in sources}
        if missing:
            print(f"WARNING: unknown source names ignored: {sorted(missing)}")
    else:
        sources = select_sources(args)
    if not sources:
        return 1

    # sources that dedupe against another source must run after it
    sources = sorted(sources, key=lambda x: 1 if (x.options or {}).get("dedupe_against") else 0)

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    all_stats: list[SourceStats] = []

    for source in sources:
        if args.command == "all":
            all_stats.append(run_source_all(source))
        elif args.command == "fetch":
            fetched = do_fetch(source)
            print(f"[fetch] {source.name}: {len(fetched.docs)} docs, skipped={fetched.skipped}")
        elif args.command == "clean":
            fetched = do_fetch(source)
            cleaned = do_clean(source, fetched)
            print(f"[clean] {source.name}: {len(cleaned.docs)} docs cleaned")
        elif args.command == "chunk":
            fetched = do_fetch(source)
            cleaned = do_clean(source, fetched)
            chunk_dicts, validation_errors, validation_failures, junk_dropped = do_chunk(
                source, cleaned
            )
            if chunk_dicts:
                write_chunks_jsonl(source.name, chunk_dicts)
            print(
                f"[chunk] {source.name}: {len(chunk_dicts)} chunks, "
                f"{validation_failures} validation failures, {junk_dropped} junk dropped"
            )
        elif args.command == "report":
            all_stats.append(report_from_existing(source))

    if args.command in ("all", "report") and all_stats:
        paths = write_report(all_stats, REPORTS_DIR, timestamp)
        print(f"\n[report] wrote {paths['md']} and {paths['json']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
