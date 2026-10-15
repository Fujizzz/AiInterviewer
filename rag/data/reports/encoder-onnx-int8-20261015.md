# Query encoder: torch vs ONNX int8 (bge-m3)

Generated 2026-10-15 from `data/reports/encoder-bench-20261015.json` (gitignored, reproduce with
`python -m rag.cli encoder bench --variants <names>`); narrative and the container/RSS measurements are by hand.
Machine: Apple M5 (arm64), 16 GB, Python 3.13, onnxruntime 1.23.2, Postgres 17 + pgvector 0.8.7 in docker (colima).
**All timings are Apple arm64. The server is x86_64; see "x86" below for what could and could not be measured.**

## Summary

* **Default: `int8-pc`** (dynamic QInt8, per-channel weights, activations uint8 per call), 587 MB on disk, 0.78 GB RSS
  for the whole retrieval process including the DB pool (torch: 2.05 GB for the encoder alone on CPU fp32, 3.7 GB peak
  with the adapter on the dev default), 13 ms per query on 2 arm64 threads.
* Query vectors agree with the torch encoder at cosine **0.9888** mean (min 0.9843, p5 0.9856) over 98 queries.
* Retrieval on the 40-query draft gold set: hybrid nDCG@10 **0.595 to 0.586 (-0.009, 95% CI [-0.030, +0.007])**, dense
  0.552 to 0.550. Nothing is statistically distinguishable from torch, but the weak categories (paraphrase, identifiers)
  move down and the CIs are wide (n=40, n=10).
* Confidence: negatives unchanged (18 low / 4 medium / 0 high); two gold queries drop from high to medium (g18, g27, both
  within 0.005 of the 0.62 cut-off). Thresholds were **not** retuned.
* Decision rule (lose > 0.02 nDCG or change > 2 gold confidence levels): **not triggered** (-0.009, 2 changes), but the
  less aggressive recipes were run anyway and are in the table; none is clearly better.

## What was compared

* **Reference** = the production `Embedder` (BAAI/bge-m3 fp16 on MPS), which is what produced every vector in the
  index and every number quoted in the README (main hybrid 0.595 / 0.664 / 0.672, dense 0.552; negatives 18 low / 0 high).
  The same retriever, SQL, metrics and bootstrap are used for both; only the query vector differs.
  `RAG_ENCODER=onnx-int8 python -m rag.cli eval --no-rerank` / `--confidence` / `--ident-study` (the existing harness)
  give the same int8 numbers as `encoder bench`; see "Existing harness" below.
* **ONNX source**: the official export in the `onnx/` folder of `BAAI/bge-m3`, revision
  `5617a9f61b028005a4858fdac845db406aefb181` (the revision the index was built from), **license MIT**
  (Hugging Face model card). Files: `model.onnx` (0.7 MB graph) + `model.onnx_data` (2.27 GB fp32 weights) +
  `tokenizer.json`; torch 2.1.2 export, opset 11, outputs `token_embeddings` and `sentence_embedding`. No local export
  was needed. The fp32 ONNX reproduces torch fp32 at cosine 0.99999994 or better on the 4 queries checked, so the export
  is faithful and all loss below is quantization.
* **Pooling** was read from the model, not assumed: `modules.json` = Transformer, Pooling, Normalize and
  `1_Pooling/config.json` has only `pooling_mode_cls_token: true`. The encoder takes `token_embeddings[:, 0]` and
  L2-normalises in numpy. Text format = `RetrievalIntent.dense_query_text()` with no prefix; truncation at 1024 tokens
  (`EMBED_MAX_SEQ_LEN`, not the 8192 in `sentence_bert_config.json`, because the corpus was embedded with 1024).
* **Query sets** (98): main gold 40, identifier slice 10, negatives 22, the 4 Chinese in-domain queries of
  `negative_queries.yaml`, and 22 new Chinese / mixed queries (`data/eval/multilingual_queries.yaml`, 16 in-domain,
  6 off-topic).
* **Variants** (`rag/index/onnx_build.py`): `int8-pt` (QInt8 per-tensor, ORT defaults), `int8-pc` (per-channel),
  `uint8-pc` (QUInt8), `int8-pc-rr` (7-bit range, avoids AVX2 u8s8 saturation), `int8-pc-emb16` (MatMul int8,
  the 250k x 1024 word embeddings kept as fp16), `int8-pc-noattn` (Q/K/V/output projections of all 24 layers left
  fp32), `int8-pc-last2fp32` (last two layers fp32). An fp16 ONNX was not built: onnxruntime's CPU provider has no fp16
  kernels (slower than fp32 on x86) and it would be 1.1 GB.
* **One text per forward pass.** Dynamic int8 takes the activation scale over the whole (padded) batch, so batching
  changed a query's vector by about 1e-2 cosine (found while writing the real-artifact test: `encode([a, b])[0]` differed
  from `encode([a])[0]`). All numbers here, and the encoder's default (`batch_size=1`), encode queries one at a time.

## Variant table (reference = torch fp16 query encoder, same index, rerank off)

| variant | MB | cos mean / min / p5 | hybrid nDCG@10 delta [95% CI] | R@10 delta | MRR delta | dense nDCG delta | gold conf. changes | neg. conf. changes | mean max_dense shift (gold) |
|---|---|---|---|---|---|---|---|---|---|
| int8-pc-emb16 | 868 | 0.9889 / 0.9836 / 0.9849 | -0.003 [-0.012, +0.006] | -0.007 [-0.028, +0.007] | +0.004 [-0.002, +0.012] | +0.002 [-0.008, +0.013] | 2 | 0 | -0.0042 |
| int8-pc-last2fp32 | 662 | 0.9911 / 0.9850 / 0.9882 | -0.012 [-0.035, +0.005] | -0.024 [-0.062, +0.005] | +0.001 [-0.020, +0.021] | -0.006 [-0.019, +0.008] | 1 | 0 | -0.0044 |
| int8-pc-noattn | 888 | 0.9934 / 0.9859 / 0.9912 | +0.010 [-0.004, +0.026] | -0.001 [-0.025, +0.014] | +0.025 [+0.003, +0.056] | +0.008 [-0.005, +0.023] | 2 | 1 | -0.0045 |
| int8-pc-rr | 587 | 0.9874 / 0.9798 / 0.9838 | -0.020 [-0.043, -0.004] | -0.024 [-0.049, -0.005] | -0.012 [-0.043, +0.007] | -0.008 [-0.032, +0.009] | 2 | 0 | -0.0100 |
| int8-pt | 586 | 0.9835 / 0.9697 / 0.9788 | -0.000 [-0.010, +0.009] | -0.002 [-0.023, +0.014] | -0.004 [-0.035, +0.016] | +0.001 [-0.023, +0.027] | 2 | 0 | -0.0087 |
| uint8-pc | 587 | 0.9889 / 0.9834 / 0.9860 | -0.001 [-0.026, +0.018] | +0.001 [-0.008, +0.011] | +0.004 [-0.033, +0.040] | -0.001 [-0.027, +0.021] | 1 | 0 | -0.0051 |
| int8-pc | 587 | 0.9888 / 0.9843 / 0.9856 | -0.009 [-0.030, +0.007] | -0.024 [-0.062, +0.005] | +0.005 [-0.012, +0.024] | -0.002 [-0.014, +0.010] | 2 | 0 | -0.0058 |

## Vector agreement per query set (cosine vs torch; mean / min / p5)

| variant | gold (n=40) | identifier (n=10) | negative (n=22) | zh_in_domain_prior (n=4) | zh_mixed (n=22) | ALL (n=98) |
|---|---|---|---|---|---|---|
| int8-pc-emb16 | 0.9883 / 0.9836 / 0.9846 | 0.9879 / 0.9839 / 0.9850 | 0.9892 / 0.9838 / 0.9862 | 0.9907 / 0.9885 / 0.9887 | 0.9896 / 0.9864 / 0.9878 | 0.9889 / 0.9836 / 0.9849 |
| int8-pc-last2fp32 | 0.9908 / 0.9877 / 0.9882 | 0.9902 / 0.9867 / 0.9871 | 0.9913 / 0.9850 / 0.9890 | 0.9922 / 0.9915 / 0.9915 | 0.9915 / 0.9894 / 0.9895 | 0.9911 / 0.9850 / 0.9882 |
| int8-pc-noattn | 0.9931 / 0.9904 / 0.9906 | 0.9930 / 0.9919 / 0.9920 | 0.9935 / 0.9859 / 0.9897 | 0.9942 / 0.9939 / 0.9939 | 0.9939 / 0.9914 / 0.9920 | 0.9934 / 0.9859 / 0.9912 |
| int8-pc-rr | 0.9868 / 0.9820 / 0.9840 | 0.9857 / 0.9798 / 0.9815 | 0.9881 / 0.9818 / 0.9839 | 0.9893 / 0.9886 / 0.9886 | 0.9884 / 0.9848 / 0.9861 | 0.9874 / 0.9798 / 0.9838 |
| int8-pt | 0.9825 / 0.9697 / 0.9764 | 0.9825 / 0.9801 / 0.9803 | 0.9843 / 0.9790 / 0.9805 | 0.9848 / 0.9826 / 0.9827 | 0.9848 / 0.9788 / 0.9823 | 0.9835 / 0.9697 / 0.9788 |
| uint8-pc | 0.9887 / 0.9834 / 0.9852 | 0.9877 / 0.9861 / 0.9862 | 0.9892 / 0.9838 / 0.9866 | 0.9900 / 0.9886 / 0.9887 | 0.9895 / 0.9858 / 0.9869 | 0.9889 / 0.9834 / 0.9860 |
| int8-pc | 0.9885 / 0.9843 / 0.9857 | 0.9878 / 0.9847 / 0.9849 | 0.9891 / 0.9845 / 0.9867 | 0.9903 / 0.9891 / 0.9892 | 0.9894 / 0.9867 / 0.9876 | 0.9888 / 0.9843 / 0.9856 |

## Top-10 overlap with the torch ranking (mean over queries and modes)

| variant | gold | identifier | negative | zh_in_domain_prior | zh_mixed |
|---|---|---|---|---|---|
| int8-pc-emb16 | 0.912 | 0.920 | 0.882 | 0.950 | 0.945 |
| int8-pc-last2fp32 | 0.927 | 0.930 | 0.882 | 0.950 | 0.927 |
| int8-pc-noattn | 0.948 | 0.930 | 0.905 | 0.925 | 0.941 |
| int8-pc-rr | 0.918 | 0.910 | 0.877 | 0.900 | 0.936 |
| int8-pt | 0.912 | 0.915 | 0.841 | 0.900 | 0.905 |
| uint8-pc | 0.922 | 0.910 | 0.855 | 0.875 | 0.945 |
| int8-pc | 0.919 | 0.920 | 0.873 | 0.975 | 0.927 |

## Encode latency per query (ms, Apple M5 arm64, 50 queries after warm-up)

| config | p50 | p95 |
|---|---|---|
| int8-pc-emb16/threads=2 | 13.4 | 19.4 |
| int8-pc-emb16/threads=1 | 19.8 | 31.8 |
| int8-pc-last2fp32/threads=2 | 15.1 | 25.1 |
| int8-pc-last2fp32/threads=1 | 24.8 | 38.2 |
| int8-pc-noattn/threads=2 | 22.5 | 35.3 |
| int8-pc-noattn/threads=1 | 37.0 | 60.1 |
| int8-pc-rr/threads=2 | 14.4 | 22.1 |
| int8-pc-rr/threads=1 | 21.0 | 33.1 |
| int8-pt/threads=2 | 13.9 | 19.6 |
| int8-pt/threads=1 | 20.2 | 32.6 |
| uint8-pc/threads=2 | 13.6 | 20.8 |
| uint8-pc/threads=1 | 20.5 | 33.0 |
| int8-pc/threads=2 | 13.4 | 20.3 |
| int8-pc/threads=1 | 19.9 | 33.3 |
| torch(reference) | 16.0 | 18.6 |


## Default variant `int8-pc` in detail

Per query set, torch vs int8-pc (n and CIs from the 10,000-resample paired bootstrap in `rag/eval/stats.py`):

| set / mode | nDCG@10 torch | nDCG@10 int8 | delta [95% CI] | R@10 torch / int8 | delta [95% CI] | MRR@10 torch / int8 | delta [95% CI] |
|---|---|---|---|---|---|---|---|
| main gold, hybrid | 0.595 | 0.586 | -0.009 [-0.030, +0.007] | 0.664 / 0.640 | -0.024 [-0.062, +0.005] | 0.672 / 0.677 | +0.005 [-0.012, +0.024] |
| main gold, dense | 0.552 | 0.550 | -0.002 [-0.014, +0.010] | 0.620 / 0.621 | +0.001 [-0.011, +0.013] | 0.658 / 0.645 | -0.013 [-0.059, +0.031] |
| identifier slice, hybrid | 0.640 | 0.614 | -0.026 [-0.066, +0.006] | 0.581 / 0.561 | -0.020 [-0.060, +0.020] | 0.900 / 0.833 | -0.067 [-0.200, +0.000] |
| identifier slice, dense | 0.502 | 0.495 | -0.008 [-0.019, +0.002] | 0.451 / 0.451 | +0.000 [+0.000, +0.000] | 0.783 / 0.758 | -0.025 [-0.075, +0.000] |

Per category (main gold):

| mode | category | n | nDCG torch | nDCG int8 | R@10 torch | R@10 int8 | MRR torch | MRR int8 |
|---|---|---|---|---|---|---|---|---|
| hybrid | conceptual | 14 | 0.674 | 0.681 | 0.708 | 0.718 | 0.795 | 0.811 |
| hybrid | identifier | 10 | 0.765 | 0.766 | 0.842 | 0.832 | 0.800 | 0.825 |
| hybrid | paraphrase | 9 | 0.208 | 0.151 | 0.261 | 0.150 | 0.294 | 0.259 |
| hybrid | question | 7 | 0.691 | 0.697 | 0.839 | 0.839 | 0.732 | 0.738 |
| dense | conceptual | 14 | 0.601 | 0.611 | 0.664 | 0.666 | 0.732 | 0.768 |
| dense | identifier | 10 | 0.674 | 0.672 | 0.751 | 0.751 | 0.779 | 0.748 |
| dense | paraphrase | 9 | 0.199 | 0.181 | 0.250 | 0.261 | 0.294 | 0.215 |
| dense | question | 7 | 0.733 | 0.726 | 0.821 | 0.807 | 0.804 | 0.806 |

Paraphrase (n=9) is where hybrid loses most (0.208 to 0.151); dense on the same queries moves 0.199 to 0.181. With 9
queries and a CI that includes 0 this is a warning, not a finding.

### `max_dense` and the confidence thresholds (0.52 / 0.62)

`max_dense` = top dense cosine between the (int8) query vector and the (torch) corpus vectors. int8 noise lowers it on
average (shift = int8 minus torch):

| query set | n | mean shift | min shift | max shift |
|---|---|---|---|---|
| gold | 40 | -0.0058 | -0.0197 | +0.0119 |
| identifier | 10 | -0.0099 | -0.0178 | -0.0057 |
| negative | 22 | -0.0035 | -0.0189 | +0.0130 |
| zh_in_domain_prior | 4 | -0.0132 | -0.0166 | -0.0066 |
| zh_mixed | 22 | -0.0072 | -0.0207 | +0.0046 |

Confidence level (rerank off; low / medium / high counts, torch then int8):

| query set | torch | int8-pc | queries that changed level |
|---|---|---|---|
| gold | 0/13/27 | 0/15/25 | g18 high to medium (max_dense 0.625 to 0.619); g27 high to medium (max_dense 0.625 to 0.617) |
| identifier | 0/4/6 | 0/6/4 | i02 high to medium (max_dense 0.625 to 0.619); i04 high to medium (max_dense 0.628 to 0.617) |
| negative | 18/4/0 | 18/4/0 | none |
| zh_in_domain_prior | 0/1/3 | 0/1/3 | none |
| zh_mixed | 4/6/12 | 5/5/12 | m22 medium to low (max_dense 0.523 to 0.514) |

Reading: the 0.52 "low" cut-off, which decides whether the Agent is told "no evidence", moved one query
(m22 "如何学吉他 acoustic 和弦入门", an off-topic Chinese query, medium to low, which is the right direction); no
negative became medium or high, and no gold query became low. The 0.62 "high" cut-off lost two gold queries and two
identifier queries to medium because their torch max_dense (0.625-0.628) sat just above it. That is the conservative
direction (medium means "corroborate"), a shift of about -0.006 is far below the 0.01-0.02 noise of a 40-query draft
calibration, and retuning the thresholds on the same queries would only add overfitting. Not retuned. If the Agent ever
treats `high` as a hard gate, lowering `dense_high` to 0.61 would restore these four (a one-line change in `ConfidenceRule`; the
effect on other queries was not measured), but that needs a held-out set first (BACKLOG).

### Existing harness cross-check

Run with the repository's own entry points (torch values reproduce the committed 2026-10-08 reports exactly):

| command | torch | `RAG_ENCODER=onnx-int8` |
|---|---|---|
| `eval --no-rerank`: dense nDCG@10 | 0.552 | 0.550 |
| `eval --no-rerank`: hybrid nDCG / R@10 / MRR | 0.595 / 0.664 / 0.672 | 0.586 / 0.640 / 0.677 |
| `eval --no-rerank`: hybrid p50 / p95 ms (incl. encode) | 155 / 480 | 161 / 477 |
| `eval --ident-study` (ident_bonus 0): main nDCG / identifier slice nDCG | 0.594 / 0.640 | 0.587 / 0.614 |
| `eval --confidence`: gold low/medium/high, dense+lexical rule | 0 / 13 / 27 | 0 / 15 / 25 |
| `eval --confidence`: negatives low/medium/high, dense+lexical rule | 18 / 4 / 0 | 18 / 4 / 0 |
| `eval --confidence`: gold low with the torch reranker on top | 5 | 6 (all six have no relevant chunk in the top 10) |
| `eval --confidence`: negatives low/medium with rerank | 20 / 2 | 20 / 2 |

(`eval`, `--tune` and `--ident-study` crashed on `origin/main` with "refusing to build a candidate-namespace filter
without candidate_id"; fixed in `rag/eval/tune.py`, separate commit.)

## Resources

Fresh subprocess per row, model load included, then warm-up and 50 queries; peak RSS from `resource.getrusage`
(macOS `ps` under-reports because of memory compression, so the peak is the number to read). "adapter" = the real
`create_rag_adapter()` + `warmup()` + 50 `retrieve()` calls against the Postgres 17 container, so latencies there
include the database (default `shared_buffers`), encoding and thread hops.

| process | peak RSS MB | cold load + warm-up s | per-query p50 / p95 ms |
|---|---|---|---|
| onnx-int8, 2 threads, adapter (pool + DB) | 782 | 1.0 | 150 / 349 |
| onnx-int8, 2 threads, encoder only | 756 | 0.8 | 18 / 28 |
| onnx-int8, 1 thread, adapter | 784 | 0.7 | 171 / 329 |
| onnx-int8, 1 thread, encoder only | 773 | 0.6 | 19 / 31 |
| torch fp16 on MPS (dev default), adapter | 3729 | 9.0 | 152 / 357 |
| torch fp16 on MPS, encoder only | 2331 | 11.8 | 16 / 23 |
| torch CPU fp16, encoder only | 3276 | 8.4 | 46 / 85 |
| torch CPU fp32, encoder only | 2052 | 7.8 | 38 / 40 |

Notes:

* Encode-only latency for `int8-pc` (idle machine, 50 queries after warm-up): 2 threads 13.4 / 20.3 ms, 1 thread
  19.9 / 33.3 ms; torch fp16 MPS 16.0 / 18.6 ms. Hybrid retrieve end to end in `encoder bench`: p50 111 ms int8 vs
  123 ms torch (p95 397 vs 395 ms).
* The RSS of 0.78 GB is about 0.39 GB for the `tokenizers` XLM-R vocabulary (measured on its own: 17 MB to 433 MB RSS
  after `Tokenizer.from_file`), 0.34 GB for the memory-mapped weights and activations, the rest Python + numpy + psycopg.
  A single-file `model.onnx` (weights inside the protobuf) costs 1.4 GB instead: the build therefore writes the weights
  to `model.onnx.data`, which onnxruntime maps instead of copying.
* Build cost: `build-onnx` takes 30 s and peaks at 3.45 GB RSS (`/usr/bin/time -l`), on a dev machine only; it cannot
  run on a server with 2.3 GB available. Rebuilding gives the same sha256 (checked against the committed manifest).
* torch rows: "fp16 on MPS" is today's dev default; its 3.7 GB peak is the transient while the fp32 `.bin` is loaded
  and cast. CPU fp32 (2.05 GB steady) is the realistic server configuration with torch; with the adapter, Postgres client
  and a Python app on top it matches the 2.5-3 GB estimate, so it does not fit in 2.3 GB.

## x86

No x86 hardware was available. The container is `linux/amd64` Python 3.12.15 under qemu user-mode emulation on this
arm64 Mac (colima), limited with `--cpus 2 --memory 2g`. **Emulated timing is pessimistic by two orders of magnitude and
must not be read as a latency**: encode p50 was 2.1 s (vs 13 ms native arm64) and `/proc/cpuinfo` shows the host.
What the emulation can show:

| check (amd64 container) | result |
|---|---|
| cosine vs torch, first 40 queries (gold), int8-pc / uint8-pc / int8-pc-rr / int8-pt | 0.9886 / 0.9887 / 0.9868 / 0.9827 (arm64: 0.9885 / 0.9887 / 0.9868 / 0.9825) |
| same with `QEMU_CPU=max` (AVX2 exposed to the guest), int8-pc / uint8-pc | identical to 4 digits |
| peak RSS, encoder only | 755-766 MB |
| runs inside a hard cgroup limit without swap | 1 GB, 800 MB, 640 MB and 512 MB all completed (weights are reclaimable page cache; RSS 548 MB at 512 MB) |
| full adapter (`create_rag_adapter` + `warmup` + 3 searches against the Postgres container) | works, no torch-family module imported, peak RSS 801 MB |

Not shown: real AVX2/VNNI kernels. Whether the production CPU saturates u8s8 products cannot be determined here; use
`python -m rag.index.encoder_commands verify --smoke` on the server (cosine must be >= 0.97) and fall back to
`int8-pc-rr` if not. A rough compute estimate (about 10 GOP for a 15-token query, 2 AVX2 cores) puts a native encode
at tens to low hundreds of milliseconds; this is an estimate, not a measurement.

### Serve-only install (clean `python:3.12-slim`, linux/amd64)

`pip install -r rag/requirements-serve.txt` into an empty venv: installs, `pip check` clean, no torch, transformers or
sentence-transformers. Resolved set equals the deploy lock for numpy 2.0.2, psycopg 3.3.3 (+binary), pydantic 2.13.5,
onnxruntime 1.23.2, tokenizers 0.22.2, huggingface-hub 1.33.0 (pulled by tokenizers) and PyYAML 6.0.3; the only packages
that are not in `deploy/requirements-linux.lock.txt` are `psycopg-pool 3.3.3` and `pgvector 0.5.1`. `create_rag_adapter()` +
`warmup()` + searches with `RAG_ENCODER=onnx-int8` work with only those packages (top hits: PagedAttention paper, Django
docs; the off-topic query came back `low` confidence; every chunk carries `metadata["encoder"] == "onnx-int8/int8-pc"`).
Note: the lock file already contains `onnxruntime` and `tokenizers` (for the answer-completion gate), contrary to the
brief; the missing ones are `pgvector` and `psycopg-pool`.

### Postgres on a small box

pg14 (the server's version) in a container limited to `--memory 700m --cpus 1` with `shared_buffers=256MB
effective_cache_size=640MB work_mem=4MB maintenance_work_mem=64MB max_connections=20`: restore from the dev dump works
only through plain SQL (the PG17 custom-format dump is rejected by PG14's `pg_restore`: "unsupported version (1.16)"),
16,379 chunks and all 12 indexes, including HNSW with 64 MB `maintenance_work_mem`, come back; container memory after
restore + 100 retrievals: 291 MiB of 700. Retrieval through the onnx adapter against it: p50 78 / p95 207 ms (the
default-settings pg17 container gave 150 / 349 ms; different major versions, not a controlled comparison).

## Caveats

* n is tiny: 40 gold, 10 identifier, 9 paraphrase queries, all drafts, thresholds calibrated on the same queries.
  "Not distinguishable" is not "equal".
* arm64 and x86 int8 kernels differ; only memory and cosine carry over, and cosine only as far as emulation does.
* The reference encoder is torch fp16 on MPS, not torch fp32; torch fp16 vs fp32 differ by about 1e-4 cosine.
* Only technical and question chunks exist in the index; candidate/job retrieval is untested with int8 (nothing to test).
