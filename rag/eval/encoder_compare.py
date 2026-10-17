"""Compare query encoders (torch reference vs ONNX int8 variants) on agreement, retrieval, cost.

    python -m rag.cli encoder bench --variants int8-pc,int8-pt --out rag/data/reports/x.json

Everything is measured against the SAME index (the corpus vectors stay torch-made); only the query
vector comes from the encoder under test. Stages:

  vectors    cosine(variant query vec, torch query vec) per query set
  retrieval  main gold / identifier slice / negatives / Chinese: metrics, paired bootstrap deltas,
             top-10 overlap, max_dense shift and confidence-level changes (rerank OFF)
  latency    encode latency p50/p95 for 1 and 2 threads
  rss        peak RSS of fresh subprocesses (see `rss_child`)

The torch reference is the production `Embedder` (fp16 on MPS when available), i.e. exactly what
produced today's numbers.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ..index import config
from ..retrieval.intent import RetrievalIntent
from ..retrieval.retriever import Retriever
from .gold import GoldSet, load_all_chunks, load_gold, resolve_gold
from .metrics import percentile
from .run import by_category, evaluate_ranking, summarize
from .stats import paired_bootstrap

EVAL_DIR = config.DATA_DIR / "eval"
ROLE = "question_planner"
K = 10


# ---------------------------------------------------------------------------
# query sets
# ---------------------------------------------------------------------------


def load_query_sets() -> dict[str, list[tuple[str, str]]]:
    gold = load_gold(EVAL_DIR / "gold_queries.yaml")
    ident = load_gold(EVAL_DIR / "identifier_queries.yaml")
    neg = yaml.safe_load((EVAL_DIR / "negative_queries.yaml").read_text(encoding="utf-8"))
    multi = yaml.safe_load((EVAL_DIR / "multilingual_queries.yaml").read_text(encoding="utf-8"))
    return {
        "gold": [(q.id, q.query) for q in gold.queries],
        "identifier": [(q.id, q.query) for q in ident.queries],
        "negative": [(x["id"], x["query"]) for x in neg["negative"]],
        "zh_in_domain_prior": [(x["id"], x["query"]) for x in neg.get("in_domain_non_ascii", [])],
        "zh_mixed": [(x["id"], x["query"]) for x in multi["queries"]],
    }


# ---------------------------------------------------------------------------
# vector agreement
# ---------------------------------------------------------------------------


def cosine_stats(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    cos = np.sum(a * b, axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
    return {
        "n": int(len(cos)),
        "mean": float(cos.mean()),
        "min": float(cos.min()),
        "p5": float(np.percentile(cos, 5)),
    }


def _encode_each(enc: Any, texts: list[str]) -> np.ndarray:
    """One text per call, as at serve time. (Dynamic int8 quantizes activations per tensor, so a
    padded batch would shift every vector slightly; the server only ever encodes one query.)"""
    return np.stack([np.asarray(enc.encode([t]), dtype=np.float32)[0] for t in texts])


def vector_agreement(
    ref: Any, encoders: dict[str, Any], sets: dict[str, list[tuple[str, str]]]
) -> dict[str, dict[str, dict]]:
    ref_vecs = {s: _encode_each(ref, [q for _, q in items]) for s, items in sets.items()}
    out: dict[str, dict[str, dict]] = {}
    for name, enc in encoders.items():
        vecs = {s: _encode_each(enc, [q for _, q in items]) for s, items in sets.items()}
        out[name] = {s: cosine_stats(ref_vecs[s], vecs[s]) for s in sets}
        out[name]["ALL"] = cosine_stats(
            np.concatenate(list(ref_vecs.values())), np.concatenate(list(vecs.values()))
        )
    return out


# ---------------------------------------------------------------------------
# retrieval pass (one encoder)
# ---------------------------------------------------------------------------


def run_pass(
    embedder: Any,
    sets: dict[str, list[tuple[str, str]]],
    dsn: str | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, dict[str, dict]]:
    """set -> qid -> {mode -> {ranked, max_dense, confidence, lexical_hit, ms}}. Rerank is off."""
    modes_for = {"gold": ("hybrid", "dense"), "identifier": ("hybrid", "dense")}
    r = Retriever(dsn=dsn, embedder=embedder)
    out: dict[str, dict[str, dict]] = {}
    try:
        r.retrieve(RetrievalIntent(query="warm up", k=K), ROLE)
        for set_name, items in sets.items():
            for qid, query in items:
                for mode in modes_for.get(set_name, ("hybrid",)):
                    t = time.perf_counter()
                    res = r.retrieve(RetrievalIntent(query=query, k=K), ROLE, mode=mode)
                    ms = (time.perf_counter() - t) * 1000
                    out.setdefault(set_name, {}).setdefault(qid, {})[mode] = {
                        "ranked": [p.chunk_id for p in res],
                        "max_dense": res.max_dense,
                        "confidence": res.confidence,
                        "lexical_hit": res.lexical_hit,
                        "ms": ms,
                    }
        log(f"  pass done ({sum(len(v) for v in out.values())} queries)")
    finally:
        r.close()
    return out


def sign_flip_p(diffs: list[float]) -> float:
    """Exact two-sided sign-flip permutation p-value for mean(diffs) == 0 (zeros are ignored)."""
    nz = [d for d in diffs if abs(d) > 1e-12]
    if not nz:
        return 1.0
    k = len(nz)
    if k > 22:
        raise ValueError("too many non-zero differences for the exact test")
    obs = abs(sum(nz))
    hits = 0
    for mask in range(1 << k):
        total = sum(d if (mask >> i) & 1 else -d for i, d in enumerate(nz))
        if abs(total) >= obs - 1e-12:
            hits += 1
    return hits / (1 << k)


def _metrics(run: dict[str, dict], gold: GoldSet, rel: dict[str, set[str]], mode: str):
    cat = {q.id: q.category for q in gold.queries}
    per_q = {qid: evaluate_ranking(run[qid][mode]["ranked"], rel[qid]) for qid in run}
    return {"summary": summarize(per_q), "by_category": by_category(per_q, cat), "per_query": per_q}


def _overlap(a: list[str], b: list[str]) -> float:
    return len(set(a[:K]) & set(b[:K])) / max(1, min(K, max(len(a), len(b))))


def compare_pass(
    ref: dict[str, dict[str, dict]], other: dict[str, dict[str, dict]], golds: dict[str, GoldSet]
) -> dict[str, Any]:
    chunks = load_all_chunks()
    rep: dict[str, Any] = {"metrics": {}, "overlap": {}, "max_dense": {}, "confidence": {}}
    for set_name in ("gold", "identifier"):
        rel = resolve_gold(golds[set_name], chunks)
        for mode in ("hybrid", "dense"):
            m_ref = _metrics(ref[set_name], golds[set_name], rel, mode)
            m_new = _metrics(other[set_name], golds[set_name], rel, mode)
            delta = {
                k: paired_bootstrap(
                    {q: v[k] for q, v in m_new["per_query"].items()},
                    {q: v[k] for q, v in m_ref["per_query"].items()},
                )
                for k in ("ndcg@10", "recall@10", "mrr@10")
            }
            cats: dict[str, dict] = {}
            for cat in sorted({q.category for q in golds[set_name].queries}):
                ids = [q.id for q in golds[set_name].queries if q.category == cat]
                a = {q: m_new["per_query"][q]["ndcg@10"] for q in ids}
                b = {q: m_ref["per_query"][q]["ndcg@10"] for q in ids}
                d = paired_bootstrap(a, b)
                d["p_perm"] = sign_flip_p([a[q] - b[q] for q in ids])
                d["n_changed"] = sum(abs(a[q] - b[q]) > 1e-12 for q in ids)
                cats[cat] = d
            rep["metrics"][f"{set_name}/{mode}"] = {
                "category_ndcg_delta": cats,
                "ref": m_ref["summary"],
                "new": m_new["summary"],
                "ref_by_category": m_ref["by_category"],
                "new_by_category": m_new["by_category"],
                "delta": delta,
            }
    for set_name, runs in ref.items():
        ovs: list[float] = []
        dd: list[float] = []
        changes: list[dict] = []
        for qid, modes in runs.items():
            new = other[set_name][qid]
            for mode in modes:
                ovs.append(_overlap(modes[mode]["ranked"], new[mode]["ranked"]))
            h_ref, h_new = modes["hybrid"], new["hybrid"]
            if h_ref["max_dense"] is not None and h_new["max_dense"] is not None:
                dd.append(h_new["max_dense"] - h_ref["max_dense"])
            if h_ref["confidence"] != h_new["confidence"]:
                changes.append(
                    {
                        "id": qid,
                        "ref": h_ref["confidence"],
                        "new": h_new["confidence"],
                        "max_dense_ref": h_ref["max_dense"],
                        "max_dense_new": h_new["max_dense"],
                    }
                )
        rep["overlap"][set_name] = {
            "mean": statistics.fmean(ovs),
            "min": min(ovs),
            "n": len(ovs),
        }
        rep["max_dense"][set_name] = {
            "mean_shift": statistics.fmean(dd) if dd else None,
            "min_shift": min(dd) if dd else None,
            "max_shift": max(dd) if dd else None,
            "n": len(dd),
        }
        levels = ("low", "medium", "high")
        rep["confidence"][set_name] = {
            "ref_counts": {
                lv: sum(r["hybrid"]["confidence"] == lv for r in runs.values()) for lv in levels
            },
            "new_counts": {
                lv: sum(r["hybrid"]["confidence"] == lv for r in other[set_name].values())
                for lv in levels
            },
            "changed": changes,
        }
    rep["latency_hybrid_ms"] = {
        "ref_p50": percentile([m["hybrid"]["ms"] for r in ref.values() for m in r.values()], 0.5),
        "new_p50": percentile([m["hybrid"]["ms"] for r in other.values() for m in r.values()], 0.5),
        "ref_p95": percentile([m["hybrid"]["ms"] for r in ref.values() for m in r.values()], 0.95),
        "new_p95": percentile(
            [m["hybrid"]["ms"] for r in other.values() for m in r.values()], 0.95
        ),
    }
    return rep


# ---------------------------------------------------------------------------
# latency / RSS
# ---------------------------------------------------------------------------


def encode_latency(enc: Any, queries: list[str], n: int = 50) -> dict[str, float]:
    qs = (queries * (n // len(queries) + 1))[:n]
    enc.encode(["warmup"])
    lat = []
    for q in qs:
        t = time.perf_counter()
        enc.encode([q])
        lat.append((time.perf_counter() - t) * 1000)
    return {
        "p50": percentile(lat, 0.5),
        "p95": percentile(lat, 0.95),
        "mean": statistics.fmean(lat),
        "n": n,
    }


def _rss_now_bytes() -> int:
    try:  # Linux
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except OSError:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True
        )
        return int(out.stdout.strip() or 0) * 1024


def _peak_rss_bytes() -> int:
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # macOS: bytes, Linux: KiB


def rss_child(kind: str, threads: int, n_queries: int, mode: str, model_dir: str | None) -> dict:
    """Run IN A FRESH PROCESS: load, warm up, run n_queries, report peak/final RSS and timings.

    kind: onnx-int8 | torch | torch-cpu-fp32 | torch-cpu-fp16; mode: encoder (no DB) | adapter.
    """
    queries = [q for _, q in load_query_sets()["gold"]]
    qs = (queries * (n_queries // len(queries) + 1))[:n_queries]
    t0 = time.perf_counter()
    result: dict[str, Any] = {"kind": kind, "mode": mode, "threads": threads}
    if mode == "adapter":
        import asyncio

        from shared.contracts import RetrievalRequest, RetrievalSource

        from ..adapter import create_rag_adapter
        from ..settings import RAGSettings

        enc = "onnx-int8" if kind == "onnx-int8" else "torch"
        device = {"torch-cpu-fp32": "cpu", "torch-cpu-fp16": "cpu"}.get(kind)
        adapter = create_rag_adapter(
            RAGSettings(
                encoder=enc, encoder_threads=threads, encoder_model_dir=model_dir, device=device
            )
        )

        async def go() -> list[float]:
            await adapter.warmup()
            result["load_warmup_s"] = time.perf_counter() - t0
            lat = []
            for i, q in enumerate(qs):
                t = time.perf_counter()
                await adapter.retrieve(
                    RetrievalRequest(
                        request_id=f"r{i}",
                        interview_id="i",
                        source=RetrievalSource.TECHNICAL,
                        query=q,
                        top_k=5,
                        intent="bench",
                        difficulty=3,
                    )
                )
                lat.append((time.perf_counter() - t) * 1000)
            await adapter.aclose()
            return lat

        lat = asyncio.run(go())
    else:
        if kind == "onnx-int8":
            from ..index.onnx_encoder import OnnxQueryEncoder

            enc_obj = OnnxQueryEncoder(model_dir, threads=threads)
        else:
            from ..index.embed import Embedder

            enc_obj = Embedder(
                device="cpu" if kind.startswith("torch-cpu") else None,
                fp16=kind != "torch-cpu-fp32",
            )
        result["load_s"] = time.perf_counter() - t0
        enc_obj.encode(["warmup"])
        result["load_warmup_s"] = time.perf_counter() - t0
        lat = []
        for q in qs:
            t = time.perf_counter()
            enc_obj.encode([q])
            lat.append((time.perf_counter() - t) * 1000)
    result.update(
        peak_rss_mb=_peak_rss_bytes() / 2**20,
        final_rss_mb=_rss_now_bytes() / 2**20,
        p50_ms=percentile(lat, 0.5),
        p95_ms=percentile(lat, 0.95),
        n=len(lat),
    )
    return result


def run_rss(kind: str, threads: int, n: int, mode: str, model_dir: str | None) -> dict:
    cmd = [
        sys.executable,
        "-m",
        "rag.eval.encoder_compare",
        "--rss-child",
        kind,
        "--threads",
        str(threads),
        "--n",
        str(n),
        "--mode",
        mode,
    ] + (["--model-dir", model_dir] if model_dir else [])
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return {"kind": kind, "mode": mode, "error": p.stderr[-400:]}
    return json.loads(p.stdout.strip().splitlines()[-1])


def _fmt_delta(d: dict) -> str:
    return f"{d['mean_diff']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]"


def render_markdown(report: dict, sizes_mb: dict[str, float] | None = None) -> str:
    """Tables for the committed report (numbers only; the narrative is written by hand)."""
    from ..index.onnx_build import variant_dir

    variants = report["variants"]
    L: list[str] = []
    L += ["## Variant table (reference = torch fp16 query encoder, same index, rerank off)", ""]
    L += [
        "| variant | MB | cos mean / min / p5 | hybrid nDCG@10 delta [95% CI] | R@10 delta | "
        "MRR delta | dense nDCG delta | gold conf. changes | neg. conf. changes | "
        "mean max_dense shift (gold) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in variants:
        if sizes_mb and v in sizes_mb:
            mb = sizes_mb[v]
        else:
            manifest = json.loads((variant_dir(v) / "manifest.json").read_text())
            mb = sum(f["bytes"] for f in manifest["artifact"]["files"].values()) / 1e6
        c = report["vectors"][v]["ALL"]
        r = report["retrieval"][v]
        h, dn = r["metrics"]["gold/hybrid"], r["metrics"]["gold/dense"]
        L.append(
            f"| {v} | {mb:.0f} | {c['mean']:.4f} / {c['min']:.4f} / {c['p5']:.4f} | "
            f"{_fmt_delta(h['delta']['ndcg@10'])} | {_fmt_delta(h['delta']['recall@10'])} | "
            f"{_fmt_delta(h['delta']['mrr@10'])} | {_fmt_delta(dn['delta']['ndcg@10'])} | "
            f"{len(r['confidence']['gold']['changed'])} | "
            f"{len(r['confidence']['negative']['changed'])} | "
            f"{r['max_dense']['gold']['mean_shift']:+.4f} |"
        )
    L += ["", "## Vector agreement per query set (cosine vs torch; mean / min / p5)", ""]
    sets = list(next(iter(report["vectors"].values())))
    L += [
        "| variant | "
        + " | ".join(f"{s} (n={report['vectors'][variants[0]][s]['n']})" for s in sets)
        + " |"
    ]
    L += ["|---|" + "---|" * len(sets)]
    for v in variants:
        row = [
            f"{report['vectors'][v][s]['mean']:.4f} / {report['vectors'][v][s]['min']:.4f} / "
            f"{report['vectors'][v][s]['p5']:.4f}"
            for s in sets
        ]
        L.append(f"| {v} | " + " | ".join(row) + " |")
    L += ["", "## Top-10 overlap with the torch ranking (mean over queries and modes)", ""]
    osets = list(report["retrieval"][variants[0]]["overlap"])
    L += ["| variant | " + " | ".join(osets) + " |", "|---|" + "---|" * len(osets)]
    for v in variants:
        ov = report["retrieval"][v]["overlap"]
        L.append(f"| {v} | " + " | ".join(f"{ov[s]['mean']:.3f}" for s in osets) + " |")
    if "latency" in report:
        L += ["", "## Encode latency per query (ms, Apple M5 arm64, 50 queries after warm-up)", ""]
        L += ["| config | p50 | p95 |", "|---|---|---|"]
        for k, x in report["latency"].items():
            L.append(f"| {k} | {x['p50']:.1f} | {x['p95']:.1f} |")
    return "\n".join(L) + "\n"


def load_variant_encoder(name: str, threads: int = 2) -> Any:
    from ..index.onnx_build import variant_dir
    from ..index.onnx_encoder import EncoderManifest, OnnxQueryEncoder

    d = variant_dir(name)
    manifest = EncoderManifest(json.loads((d / "manifest.json").read_text(encoding="utf-8")))
    return OnnxQueryEncoder(d, threads=threads, manifest=manifest, verify=False)


def run_bench(
    variants: list[str],
    out: Path,
    stages: set[str],
    log: Callable[[str], None] = print,
) -> dict:
    """Measure `variants` against the torch reference; write the JSON and return it."""
    from ..index.embed import Embedder

    sets = load_query_sets()
    golds = {
        "gold": load_gold(EVAL_DIR / "gold_queries.yaml"),
        "identifier": load_gold(EVAL_DIR / "identifier_queries.yaml"),
    }
    report: dict[str, Any] = {"variants": variants, "sets": {k: len(v) for k, v in sets.items()}}
    ref = Embedder()
    report["reference"] = ref.meta
    encs = {v: load_variant_encoder(v, threads=2) for v in variants}
    if "vectors" in stages:
        log("[bench] vector agreement")
        report["vectors"] = vector_agreement(ref, encs, sets)
    if "retrieval" in stages:
        log("[bench] retrieval: torch reference")
        ref_pass = run_pass(ref, sets, log=log)
        report["retrieval"] = {}
        for v, enc in encs.items():
            log(f"[bench] retrieval: {v}")
            report["retrieval"][v] = compare_pass(ref_pass, run_pass(enc, sets, log=log), golds)
    if "latency" in stages:
        queries = [q for _, q in sets["gold"]]
        report["latency"] = {}
        for v in variants:
            for threads in (2, 1):
                log(f"[bench] encode latency {v} threads={threads}")
                report["latency"][f"{v}/threads={threads}"] = encode_latency(
                    load_variant_encoder(v, threads=threads), queries
                )
        report["latency"]["torch(reference)"] = encode_latency(ref, queries)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[bench] wrote {out}")
    return report


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--rss-child", required=True)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--mode", default="encoder")
    ap.add_argument("--model-dir", default=None)
    a = ap.parse_args()
    print(json.dumps(rss_child(a.rss_child, a.threads, a.n, a.mode, a.model_dir)))
