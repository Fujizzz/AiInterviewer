"""CLI handlers: migrate | embed | index."""

from __future__ import annotations

import argparse
import random
import time

from . import config
from .embed import (
    Embedder,
    EmbedStats,
    build_embedding_text,
    dir_size_bytes,
    embed_source,
    list_chunk_sources,
    read_chunks,
    tune_batch_size,
)


def _pick_sources(args: argparse.Namespace) -> list[str]:
    available = list_chunk_sources()
    if args.all:
        return available
    if not args.source:
        raise SystemExit("specify --source a,b or --all")
    names = [n.strip() for n in args.source.split(",") if n.strip()]
    missing = [n for n in names if n not in available]
    if missing:
        raise SystemExit(f"no chunk file for: {missing}")
    return names


def cmd_migrate(argv: list[str]) -> int:
    from .db import describe_dsn, migrate

    argparse.ArgumentParser(prog="rag.cli migrate").parse_args(argv)
    print(f"[migrate] target database: {describe_dsn()} (set RAG_DATABASE_URL or POSTGRES_*)")
    print(migrate())
    return 0


def cmd_embed(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="rag.cli embed")
    p.add_argument("--source", default=None, help="comma-separated source names")
    p.add_argument("--all", action="store_true")
    p.add_argument("--batch-size", type=int, default=8, help="8 was fastest on MPS (see README)")
    p.add_argument("--device", default=None, help="mps|cuda|cpu (default: auto)")
    p.add_argument(
        "--no-fp16",
        dest="fp16",
        action="store_false",
        help="use fp32 weights (fp16 is ~2.2x faster on MPS; cosine vs fp32 >= 0.9997)",
    )
    p.add_argument("--max-seq-length", type=int, default=config.EMBED_MAX_SEQ_LEN)
    p.add_argument(
        "--save-every", type=int, default=1024, help="flush cache every N embedded chunks"
    )
    p.add_argument("--tune", action="store_true", help="benchmark batch sizes on a sample and exit")
    p.add_argument("--tune-sample", type=int, default=192)
    args = p.parse_args(argv)
    names = _pick_sources(args)

    t0 = time.time()
    emb = Embedder(
        device=args.device,
        fp16=args.fp16,
        max_seq_length=args.max_seq_length,
        batch_size=args.batch_size,
    )
    print(
        f"[embed] model={emb.model_name}@{emb.revision[:10]} device={emb.device} fp16={emb.fp16} "
        f"batch={args.batch_size} max_seq={args.max_seq_length} (load {time.time() - t0:.1f}s)"
    )

    if args.tune:
        rng = random.Random(0)
        pool = [
            build_embedding_text(c)
            for n in names
            for c in read_chunks(config.CHUNKS_DIR / f"{n}.jsonl")
        ]
        sample = rng.sample(pool, min(args.tune_sample, len(pool)))
        res = tune_batch_size(emb, sample)
        if res:
            best = max(res, key=res.get)
            print(f"[embed] best batch_size={best} ({res[best]:.1f} texts/s)")
        return 0

    all_stats: list[EmbedStats] = []
    t_all = time.time()
    for name in names:
        chunks = read_chunks(config.CHUNKS_DIR / f"{name}.jsonl")
        st = embed_source(
            name,
            chunks,
            emb.encode,
            emb.meta,
            config.EMBEDDINGS_DIR,
            save_every=args.save_every,
            log=print,
        )
        all_stats.append(st)
        note = " (full rebuild: model/format changed)" if st.full_rebuild else ""
        print(
            f"[embed] {name}: total={st.total} reused={st.reused} embedded={st.embedded} "
            f"dropped={st.dropped} {st.seconds:.1f}s{note}"
        )
    wall = time.time() - t_all
    n_emb = sum(s.embedded for s in all_stats)
    tok = sum(s.tokens for s in all_stats)
    print(
        f"[embed] done: {sum(s.total for s in all_stats)} chunks in {len(names)} sources; "
        f"embedded {n_emb} in {wall:.1f}s ({n_emb / max(wall, 1e-9):.1f} chunks/s, "
        f"~{tok / max(wall, 1e-9):.0f} cl100k-tokens/s); cache "
        f"{dir_size_bytes(config.EMBEDDINGS_DIR) / 1e6:.1f} MB"
    )
    return 0


def cmd_index(argv: list[str]) -> int:
    from .db import db_reachable, describe_dsn
    from .load import IndexError_, load_sources

    p = argparse.ArgumentParser(prog="rag.cli index")
    p.add_argument("--source", default=None)
    p.add_argument("--all", action="store_true")
    args = p.parse_args(argv)
    names = _pick_sources(args)
    print(f"[index] target database: {describe_dsn()} (set RAG_DATABASE_URL or POSTGRES_*)")
    if not db_reachable():
        print("[index] Postgres is not reachable (is `docker compose up -d` running?)")
        return 2
    try:
        st = load_sources(names, prune_unknown=args.all)
    except IndexError_ as exc:
        print(f"[index] REFUSING TO LOAD: {exc}")
        return 3
    print(
        f"[index] {st.sources} sources, {st.upserted_rows_seen} chunks synced, {st.deleted} rows "
        "deleted"
    )
    return 0
