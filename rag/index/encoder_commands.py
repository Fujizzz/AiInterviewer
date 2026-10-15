"""CLI handler: encoder build-onnx | verify | smoke-ref | bench | rss.

`python -m rag.cli encoder ...` on a dev machine. On a serve-only install use
`python -m rag.index.encoder_commands verify --smoke`: rag.cli imports the ingestion pipeline
(requests, tiktoken, datasets...) at start-up, which requirements-serve.txt does not install.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from . import config


def cmd_encoder(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="rag.cli encoder",  # also: python -m rag.index.encoder_commands
        description="ONNX int8 query-encoder tooling (build on a dev machine, copy the files)",
    )
    sub = p.add_subparsers(dest="action", required=True)

    b = sub.add_parser("build-onnx", help="download the official bge-m3 ONNX and quantize it")
    from .onnx_build import DEFAULT_VARIANT, VARIANTS

    b.add_argument("--variant", default=DEFAULT_VARIANT, choices=sorted(VARIANTS))
    b.add_argument("--out", type=Path, default=None, help="output directory")
    b.add_argument("--src", type=Path, default=None, help="where the fp32 ONNX is cached")
    b.add_argument(
        "--update-manifest",
        action="store_true",
        help="rewrite the committed rag/data/encoder_manifest.json from this build",
    )

    v = sub.add_parser("verify", help="check an artifact directory against the manifest")
    v.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="default: $RAG_ENCODER_MODEL_DIR, else rag/data/models/...",
    )
    v.add_argument("--no-sha", action="store_true", help="size check only")
    v.add_argument(
        "--smoke",
        action="store_true",
        help="also load the model and compare with the committed torch reference vectors "
        "(run this on the server CPU)",
    )
    v.add_argument("--threads", type=int, default=2)

    sub.add_parser(
        "smoke-ref", help="dev: (re)write rag/data/encoder_smoke.json with torch vectors"
    )

    m = sub.add_parser("bench", help="compare ONNX variants with the torch encoder (dev machine)")
    m.add_argument("--variants", default="", help="comma-separated; default: the default variant")
    m.add_argument("--stages", default="vectors,retrieval,latency")
    m.add_argument("--out", type=Path, default=None)

    r = sub.add_parser("rss", help="peak RSS of a fresh process (one kind per call)")
    r.add_argument("kind", choices=["onnx-int8", "torch", "torch-cpu-fp32", "torch-cpu-fp16"])
    r.add_argument("--threads", type=int, default=2)
    r.add_argument("--n", type=int, default=50)
    r.add_argument("--mode", choices=["encoder", "adapter"], default="adapter")
    r.add_argument("--model-dir", default=None)

    a = p.parse_args(argv)
    if a.action == "bench":
        from datetime import UTC, datetime

        from ..eval.encoder_compare import run_bench
        from .onnx_build import DEFAULT_VARIANT

        variants = [x for x in a.variants.split(",") if x] or [DEFAULT_VARIANT]
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out = a.out or (config.DATA_DIR / "reports" / f"encoder-bench-{stamp}.json")
        run_bench(variants, out, set(a.stages.split(",")))
        return 0
    if a.action == "rss":
        import json

        from ..eval.encoder_compare import run_rss

        print(json.dumps(run_rss(a.kind, a.threads, a.n, a.mode, a.model_dir), indent=2))
        return 0
    if a.action == "build-onnx":
        from .onnx_build import build

        build(a.variant, a.out, a.src, a.update_manifest)
        return 0
    if a.action == "smoke-ref":
        from .embed import Embedder
        from .onnx_build import SANITY_QUERIES
        from .onnx_encoder import write_smoke_reference

        emb = Embedder()
        out = write_smoke_reference({q: emb.encode([q])[0] for q in SANITY_QUERIES})
        print(f"[encoder] wrote {out}")
        return 0
    return _verify(a)


def _verify(a: argparse.Namespace) -> int:
    from .onnx_encoder import (
        SMOKE_MIN_EACH,
        SMOKE_MIN_MEAN,
        EncoderUnavailableError,
        OnnxQueryEncoder,
        load_manifest,
        resolve_model_dir,
        smoke_check,
        verify_artifact,
    )

    try:
        manifest = load_manifest()
        d = resolve_model_dir(a.dir, manifest)
        verify_artifact(d, manifest, check_sha=not a.no_sha)
    except EncoderUnavailableError as error:
        print(f"[encoder] FAIL: {error}")
        return 3
    print(f"[encoder] OK: {d} matches manifest variant {manifest.variant}")
    if not a.smoke:
        return 0
    import resource
    import sys

    t = time.perf_counter()
    encoder = OnnxQueryEncoder(d, threads=a.threads, verify=False)
    load_s = time.perf_counter() - t
    res = smoke_check(encoder)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / 2**20 if sys.platform == "darwin" else peak / 1024
    ok = res["mean"] >= SMOKE_MIN_MEAN and res["min"] >= SMOKE_MIN_EACH
    print(
        f"[encoder] smoke: cosine vs torch reference mean={res['mean']:.4f} min={res['min']:.4f} "
        f"({res['n']} queries) -> "
        + ("OK" if ok else f"FAIL (need mean>={SMOKE_MIN_MEAN}, min>={SMOKE_MIN_EACH})")
    )
    print(
        f"[encoder] threads={a.threads} load {load_s:.1f}s, encode p50 {res['p50_ms']:.0f} ms / "
        f"p95 {res['p95_ms']:.0f} ms, peak RSS {peak_mb:.0f} MB (this process)"
    )
    return 0 if ok else 4


if __name__ == "__main__":
    import sys

    sys.exit(cmd_encoder(sys.argv[1:]))
