# RAG module

Production retrieval layer for the interview Agent: knowledge ingestion, a Postgres + pgvector
index, hybrid retrieval (dense bge-m3 + lexical, fused by RRF, optional cross-encoder rerank; the query
encoder is torch or a torch-free int8 ONNX build for small servers), role
based namespace access control, retrieval confidence signals, an offline evaluation harness, and
`rag/adapter.py`, which exposes all of it as the Agent's `RAGPort`.

**Teammates integrating this module: start with [docs/TEAM_INTEGRATION.md](docs/TEAM_INTEGRATION.md)**
(setup, restoring the shared DB dump, wiring into `InterviewAgentService`, open team decisions).

The module makes no interview-policy decisions. Which source to query, when, and how many chunks
remain `agents/routing/rag_router.py` concerns; the adapter only translates the contract.

```text
rag/
├── adapter.py          RAGPort adapter (RAGAdapter, create_rag_adapter, RAGUnavailableError)
├── settings.py         RAG_* environment settings
├── retrieval/          retrieve(intent, role): sql, fusion, rerank, access (ACL + candidate scope),
│                       confidence, pool.py (pooled backend), lazy.py (model singletons), format.py
├── index/              schema.sql + migrations/, embed.py (bge-m3), load.py, db.py, config.py,
│                       encoders.py + onnx_encoder.py (query-encoder backends), onnx_build.py (dev: build the
│                       int8 artifact), encoder_commands.py (`rag.cli encoder ...`)
├── loaders/ + clean.py, chunking.py, profiles.py, htmlmd.py, cli.py   ingestion pipeline
│                       (git, hf, arxiv, crawler, xlsx, wikipedia; git also parses STIX bundles)
├── eval/               gold-set evaluation, tuning, confidence study
├── data/               sources.yaml (registry), eval/*.yaml (gold sets), reports/*.md (small),
│                       encoder_manifest.json + encoder_smoke.json (int8 artifact identity / reference vectors),
│                       models/ (gitignored: the built artifact)
├── docker-compose.yml  pgvector/pgvector:pg17 on 127.0.0.1:5432
├── .env.example        dev-only Postgres defaults (copy to rag/.env, which is gitignored)
├── requirements.txt    optional dependencies (NOT part of the root pyproject / uv.lock)
├── requirements-serve.txt  serving only (onnx-int8 encoder, no torch); for the small server
├── BACKLOG.md          open work, including integration follow-ups
└── docs/               pipeline-and-index.md (long design notes, Chinese), contract-proposals.md
```

Tests live in `tests/rag/`.

## Dependencies (optional on purpose)

The retrieval stack pulls torch (via sentence-transformers), psycopg, pgvector and `datasets`. The
Agent, `app/`, `backend/` and CI do not need any of it, so it is **not** in the root
`pyproject.toml` / `uv.lock`; it follows the repository rule of keeping module manifests inside the
owning module (like `backend/requirements.txt`):

```bash
uv sync --extra dev                           # the normal team environment: no rag deps
uv pip install -r rag/requirements.txt        # add the retrieval stack on top (re-run after every uv sync)
```

The manifest pins `transformers<5`, `huggingface-hub<1` and `datasets<5`: transformers 5.x needs
`tokenizers>=0.23.1`, which conflicts with the root `completion-gate` extra (`tokenizers==0.22.2`).
With these pins both extras install side by side (verified with `uv pip compile` on Python 3.11 and
3.13, and by running the full suite in such an environment).

A machine that only serves queries does not need any of that: `RAG_ENCODER=onnx-int8` +
`rag/requirements-serve.txt` (numpy, onnxruntime, tokenizers, psycopg, psycopg-pool, pgvector, pydantic;
no torch, transformers or sentence-transformers). See [Query encoder](#query-encoder-torch-or-onnx-int8).

`rag/adapter.py`, `rag/settings.py` and `rag/retrieval/{lazy,access,intent,confidence,types}.py`
import without the extra, so the adapter unit tests run everywhere. `create_rag_adapter()` raises
`RAGUnavailableError` (with the install command) if the extra is missing.

## Setup (once per machine)

Run from the application root (`SystemCode/`).

```bash
cp rag/.env.example rag/.env                          # dev-only defaults; the file is gitignored
docker compose -f rag/docker-compose.yml up -d        # Postgres 17 + pgvector, healthchecked
uv run python -m rag.cli migrate                      # schema.sql + migrations 002, 003 (idempotent; prints the target db)

uv run python -m rag.cli all --priority P0            # fetch -> clean -> chunk -> report
uv run python -m rag.cli embed --all                  # bge-m3 vectors -> rag/data/embeddings (resumable)
uv run python -m rag.cli index --all                  # idempotent upsert into Postgres
uv run python -m rag.cli search "How does PagedAttention reduce KV cache fragmentation?" --role question_planner
```

Measured on an Apple-silicon laptop (numbers from the original runs, 22 P0 sources / 16,379 chunks;
the batch-1 expansion to 64,250 chunks is described under [Corpus](#corpus)):

| Step | Time | Disk |
|---|---|---|
| bge-m3 download (first `embed` or first adapter use) | network bound | ~2.3 GB in `~/.cache/huggingface` (+ ~2.3 GB if you enable rerank) |
| `all --priority P0` (shallow clones, HF/arXiv fetch, crawl) | network bound, not timed in the original run; resumable and cached | `rag/data/raw` ~314 MB, `clean` ~47 MB, `chunks` ~34 MB (all gitignored) |
| `embed --all` (MPS, fp16) | 792.6 s (13.2 min), 20.7 chunks/s; re-run with no changes 0.2 s | `rag/data/embeddings` ~69 MB |
| `index --all` (cold) | 33 s; re-run 4 s, 0 rows rewritten | Postgres ~330 MB (table 151 MB, HNSW 128 MB) |

Skipping ingestion: the index can also be restored from a `pg_dump` of an already loaded database;
nothing in Git contains the corpus. After a `pg_restore` or a bulk load, rebuild the HNSW index with a
large `maintenance_work_mem` (see Retrieval after the expansion) if you ever turn `exact_dense` off.

Existing databases: `migrate` is additive. Migration 003 (candidate isolation) adds
`chunks.candidate_id` and a CHECK constraint; the adapter refuses to start (clear error) until it is
applied.

### Native Windows development runtime and live smoke test

This checkout has a local PostgreSQL 16.15 + pgvector 0.8.6 development instance at
`../../.rag-runtime/` (outside Git), listening on `127.0.0.1:5432`. It uses the development credentials
in the gitignored `rag/.env`. The Docker setup above remains available. The native binaries were
installed into an isolated Conda environment from [conda-forge](https://anaconda.org/conda-forge/pgvector).
`local_db.ps1` operates this already-initialized instance; it does not install or replace databases.

Activate your Python environment first, then run from the application root (`SystemCode/`) in PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File rag/local_db.ps1 start
python -m rag.smoke PagedAttention
# Optional: stop this development database when finished.
powershell -NoProfile -ExecutionPolicy Bypass -File rag/local_db.ps1 stop
```

`rag.smoke` uses a synthetic preceding project answer, then runs the production Agent's actual
project-knowledge branch with real BGE-M3 embeddings, pgvector retrieval and the configured live
question/quality models. It writes the committed question, retrieved chunks, confidence, source
URLs and timing to `output/rag_live_smoke_*/result.json` and a short `report.md`. It fails if only an
ordinary fallback question was returned. This tests the Agent core, not the browser or backend
publication gateway, and uses the configured LLM API credentials.

The initial local corpus contains 34 chunks from the PagedAttention paper, sufficient for this
keyword smoke test; other project technologies need their own sources imported. To rebuild it:

```powershell
python -m rag.cli all --source paper-pagedattention-vllm
python -m rag.cli embed --source paper-pagedattention-vllm --device cpu --batch-size 2 --no-fp16
python -m rag.cli index --source paper-pagedattention-vllm
```

## Corpus

`data/sources.yaml` is the registry: every source records its licence, scope (include / exclude globs),
clean profile and the reason it was ingested or skipped. Nothing from the corpus is committed.

| namespace | before batch 1 | after batch 1 |
|---|---|---|
| `technical` | 13,068 chunks / 20 sources | 60,939 / 113 after batch 1, **91,742 / 243 after batch 2** |
| `question` | 3,311 | 3,311 (interview Q&A banks are deliberately not ingested, see below) |
| total in the index | 16,379 | 64,250 after batch 1, **95,053 after batch 2** |

Batch 1 (2026-10, branch `feat/rag-corpus-expansion`) added 93 technical-documentation sources,
47,871 chunks, for the topics candidates bring up in interviews:

| domain | sources | chunks | tokens |
|---|---|---|---|
| web-frontend | 12 | 13,012 | 3.88M |
| web-backend | 16 | 7,303 | 2.63M |
| security | 12 | 6,920 | 1.97M |
| devops | 16 | 6,740 | 1.90M |
| data-engineering | 13 | 3,209 | 1.05M |
| data-science | 9 | 2,938 | 1.10M |
| ml-classic | 6 | 2,549 | 0.98M |
| system-design | 2 | 2,239 | 0.66M |
| databases | 3 | 1,359 | 0.45M |
| networking | 2 | 690 | 0.40M |
| message-queue | 1 | 545 | 0.14M |
| operating-systems | 1 | 367 | 0.22M |

Source families (names are the `sources.yaml` keys):

* **Frontend**: `mdn-content` (whitelisted JS / CSS / HTTP / Web API / security / accessibility /
  glossary pages), `react-dev`, `vuejs-docs`, `angular-docs`, `nextjs-docs`, `typescript-handbook`,
  `webpack-docs`, `vite-docs`, `redux-docs`, `redux-toolkit-docs`, `web-dev`, `wikipedia-frontend`.
* **Backend / databases**: `spring-framework-docs`, `spring-boot-docs`, `spring-security-docs`,
  `spring-data-jpa-docs`, `hibernate-orm-docs`, `python-docs`, `flask-docs`, `rails-guides`,
  `dotnet-docs`, `nodejs-learn`, `express-docs`, `go-docs`, `rust-book`, `kotlin-coroutines-docs`,
  `grpc-docs`, `protobuf-docs`, `graphql-learn`, `azure-architecture-center` (cloud design patterns,
  microservices, data guide), `rabbitmq-docs`, `opensearch-docs`, `cassandra-docs`, `nginx-docs`,
  `redis-docs-patterns` (distributed locks), `wikipedia-distributed-systems`.
* **DevOps / SRE / cloud**: `kubernetes-website`, `docker-docs`, `helm-docs`, `prometheus-docs`,
  `grafana-docs`, `opentelemetry-docs`, `istio-docs`, `envoy-docs`, `argocd-docs`,
  `argo-rollouts-docs`, `github-actions-docs`, `ansible-docs`, `opentofu-docs`,
  `pagerduty-incident-response`, `networks-systems-approach`, `cnp3-networking`, `linux-insides`,
  `wikipedia-devops-cloud`.
* **Data engineering**: `spark-docs`, `airflow-docs`, `dbt-docs`, `flink-docs`, `iceberg-docs`,
  `delta-lake-protocol`, `parquet-format`, `arrow-format`, `debezium-docs`, `duckdb-docs`,
  `clickhouse-docs`, `data-engineering-cookbook`, `wikipedia-data-engineering`.
* **Classic ML / data science**: `scikit-learn-docs`, `xgboost-docs`, `lightgbm-docs`,
  `imbalanced-learn-docs`, `pandas-docs`, `statsmodels-docs`, `openintro-ims`,
  `interpretable-ml-book`, `think-stats`, `think-bayes`, `causal-inference-brave-true`, `dowhy-docs`,
  `prophet-docs`, `wikipedia-ml-statistics`, `wikipedia-statistics`.
* **Security**: `owasp-cheatsheets`, `owasp-wstg`, `owasp-top10`, `owasp-asvs`, `owasp-community`,
  `nist-sp800-63`, `pyca-cryptography`, `azure-security-docs` (STRIDE threat modeling, key
  management), `illustrated-tls13`, `mitre-attack-enterprise` (721 technique / tactic documents
  parsed from the STIX bundle), `wikipedia-security`.
* The `wikipedia-*` sources are curated lists of ~700 concept articles (CC BY-SA 4.0, one revision
  permalink per page). They exist because interview queries are often a bare term ("CAP theorem",
  "k-means++") or a plain-language paraphrase, which an encyclopedia lead matches far better than
  product documentation.

Batch 2 (2026-10, same branch) added 30,803 chunks (10.2M tokens) from 131 sources for the domains the
blind audit found nearly empty. Per-source licence, files, chunks, junk dropped and residue:
[docs/corpus-batch2.md](docs/corpus-batch2.md).

| domain | sources | chunks |
|---|---|---|
| mobile (Android, Kotlin, KMP, Swift, Flutter, React Native, FCM, OkHttp) | 10 | 6,432 |
| computer vision / video (OpenCV, torchvision, Ultralytics, MMagic, scikit-image, CS231n, FFmpeg, GStreamer, 35 CC papers) | 36 | 4,945 |
| game dev (Godot, Bevy, Flecs, Box2D, Colyseus, Vulkan, LearnOpenGL, Wikipedia) | 9 | 4,722 |
| test / QA (pytest, Playwright, JUnit 5, Cypress, Selenium, JMeter, Locust, Cucumber, Hypothesis, playbook) | 17 | 4,556 |
| embedded (Zephyr, FreeRTOS book, Yocto, Linux kernel docs and labs, devicetree spec, Embedded Rust) | 9 | 4,411 |
| LLM post-training / generative / RL (TRL, PEFT, Diffusers, courses, verl, Spinning Up, papers) | 25 | 3,200 |
| classic CS, data validation, connection pools | ~8 | ~1,500 |

Seven new Wikipedia lists (mobile, embedded, game dev, CV/video, testing, generative/LLM, algorithms) were
de-duplicated against every other Wikipedia source (one chunk set per article).

New clean profiles: `writerside`, `docc`, `eleventy`, `doxygen`, `mkdocs`, `flecs`, `zola`, `xdoc`,
`docusaurus`, `myst`, `mmlab`, `playwright`. New loader options: git `permalink_template` (non-GitHub hosts,
e.g. git.yoctoproject.org), crawler `static_version` + `permalink.append` (sites without versioned URLs:
chunks carry `snapshot-2026-10-16` as doc_version and URL fragment).

arXiv rule for batch 2: a paper is ingested only if its abs page shows a Creative Commons licence (not ND);
127 checked papers with the default non-exclusive licence (or CC BY-NC-ND) are listed in the single
`arxiv-papers-reference-only-batch2` entry. The six P0 papers ingested earlier (Attention, LoRA, PagedAttention,
RAG, ReAct, Reflexion) carry the non-exclusive licence and predate this rule: decide whether to keep them.

Not ingested: Apple docs, Unity/Unreal, MISRA (proprietary); Game Programming Patterns (CC BY-NC-ND); The Book
of Shaders (all rights reserved); Gaffer on Games, Red Blob Games, Martin Fowler (copyright, no licence); Pact,
k6, TestNG docs (no licence file); GStreamer app-dev manual (OPL). See `sources.yaml` notes.

Licence policy: ingest only what a non-commercial, privately shared coursework knowledge base may
reuse. NC and SA licences are accepted (`clickhouse-docs`, `interpretable-ml-book`, `think-*`,
`linux-insides` are NC; `ansible-docs` GPL-3.0, `grafana-docs` AGPL-3.0). No-derivatives (ND),
proprietary / vendor documentation and unknown licences are `reference_only: true` and never
chunked: `elastic-docs-content` (CC BY-NC-ND), `mysql-innodb-docs` (Oracle), `terraform-docs`
(BUSL), `google-sre-book` and `bytebytego-system-design-101` (ND), `mongodb-docs` (no licence
stated), `android-interview-questions` (unspecified), `hudi-docs` (user docs repo not located).
OpenSearch, OpenTofu and PagerDuty's incident-response docs stand in for Elastic, Terraform and the
SRE book. The interview Q&A banks (`front-end-interview-handbook`, `devops-exercises`,
`data-science-interviews`, `ml-interviews-alirezadir`, `ml-interviews-book`) are also
`reference_only` for now: questions are generated by an LLM grounded in the technical chunks.

Ingest a subset (fetch -> clean -> chunk, then embed and index only those sources; the index step
replaces exactly the rows of the named sources):

```bash
uv run python -m rag.cli all --source kubernetes-website,wikipedia-security
uv run python -m rag.cli embed --source kubernetes-website,wikipedia-security
uv run python -m rag.cli index --source kubernetes-website,wikipedia-security
uv run python -m rag.residue kubernetes-website wikipedia-security   # markup-residue counts
```

New clean profiles for these sources live in `profiles.py` (`mdn`, `asciidoc`, `msdocs`, `jekyll`,
`quarto`, `notebook`, plus Hugo / VitePress / MDX additions); `rag/loaders/stix.py` and
`rag/loaders/wikipedia_loader.py` are the two new loaders. Embedding the batch took about 35
minutes for 37k new chunks (17.6 chunks/s on MPS), indexing under a minute; the Postgres database
was ~1.1 GB after batch 1 and is 1.8 GB after batch 2 (table 1.8 GB incl. vectors, HNSW 742 MB, trigram 128 MB, tsvector GIN 66 MB).

Quality gates for the corpus: the end-to-end invariants in `tests/rag/test_chunks_e2e.py` (no raw
HTML tags, balanced fences, permalinks that pin the version, no heading id suffixes), the residue
scanner (`python -m rag.residue`), the unit tests in `tests/rag/test_corpus_expansion.py` and the
licence rule in the same file (no ingestable source with ND / BUSL / unknown licence).

### Retrieval after batch 2

* Gold eval (40 queries, hybrid, no rerank), batch 1 -> batch 2: Recall@10 0.623 -> 0.568, MRR@10 0.612 -> 0.594,
  nDCG@10 0.550 -> 0.513, Hit@10 0.825 -> 0.800 (same artefact as before: gold marks are old chunk ids).
* **Latency regressed past the 300 ms p95 target** (312 calls, 156 dev queries x 2, hybrid, no rerank, idle
  machine): 64k rows p50 289 / p95 818 ms -> 95k rows p50 436 / p95 1,244 ms. Exact dense p50 151 -> 212 ms
  (p95 165 -> 247). The tail is the lexical leg (p95 665 -> 1,034 ms): an OR of up to 12 terms matches
  ~35k of 95k rows, Postgres seq-scans and ranks every match with `ts_rank_cd`. The ~90-100 ms dense scan is
  not the problem; options are in `BACKLOG.md`.
  Since 2026-10-17 the lexical leg keeps at most 6 terms (`MAX_QUERY_TERMS` in `retrieval/sql.py`, was 12): nDCG@10
  0.602 -> 0.599 on gold v2; the latency effect is small and noisy (dev machine unchanged, 2-core server container
  p95 1,248 -> 805 ms in one run). 4 terms costs 0.014 nDCG and is not worth it.
  The studies behind these numbers are reproducible: `python -m rag.eval.studies.<fusion_fix_eval|serving_latency_matrix|
  unlabeled_top10_review|build_gold_v2>`, inputs in `data/eval/` (`gold_queries_v2.yaml` is a draft), write-up in
  `docs/retrieval-tradeoff-study.md`.

### Retrieval after the expansion (batch 1)

* **Dense search is exact by default** (`Retriever(exact_dense=True)`: index scans are disabled in the
  dense transaction). At ~64k rows the planner flipped to the incrementally built HNSW index and its
  recall fell (overlap@10 with the exact result 0.93, down to 0.2 on short queries at
  `ef_search=100`, e.g. "Kafka exactly-once semantics" returned search-autocomplete pages). An exact
  scan costs ~90 ms at this size. Revisit around 300k rows: rebuild the HNSW index in one pass
  (`SET maintenance_work_mem='1GB'; REINDEX INDEX chunks_embedding_hnsw;`) and use
  `hnsw.ef_search >= 400` (overlap@10 0.999 on the gold + dev queries).
* **Gold eval (40 queries) moved down a little**, because the gold relevance marks are the old chunk
  ids and new, equally relevant chunks count as misses. Hybrid, no rerank: Recall@10 0.664 -> 0.623,
  MRR@10 0.672 -> 0.612, nDCG@10 0.595 -> 0.550, Hit@10 0.850 -> 0.825.
* The confidence rule was recalibrated on 2026-10-16 (`dense_low` 0.55, 0.45 when an identifier matched
  verbatim; query identifiers that occur in no chunk report low): 18 of 22 negatives are flagged low
  (15 before). Four "negative" queries had earlier gained legitimate literal hits in Think Bayes /
  Think Stats / Wikipedia and were replaced in `data/eval/negative_queries.yaml`. The thresholds were
  fit on the same sets and still need a held-out check.

## Environment variables

All optional. Unset means the default. Parsed by `RAGSettings.from_env()`.

| Variable | Default | Meaning |
|---|---|---|
| `RAG_DATABASE_URL` | none | libpq DSN. Falls back to `POSTGRES_USER/PASSWORD/HOST/PORT/DB`, then `rag/.env`, then the dev defaults in `rag/.env.example`. A generic `DATABASE_URL` is **deliberately ignored** (it usually belongs to the backend, and `migrate` / `index` write to the target). `migrate` and `index` print the target `host:port/db` before touching it. Set it explicitly outside local development. |
| `RAG_ROLE` | `question_planner` | Retrieval role (ACL): `question_planner`, `evaluator`, `recommender`. |
| `RAG_RERANK` | `false` | Run the bge-reranker-v2-m3 cross-encoder over the top 10 (loads a second ~2.3 GB model; its measured gain is not yet statistically significant). |
| `RAG_POOL_MIN` / `RAG_POOL_MAX` | `1` / `4` | psycopg_pool size = maximum concurrent retrievals. |
| `RAG_POOL_TIMEOUT_SECONDS` | `1.5` | Max wait for a free pooled connection, then the call raises (the router records the source as failed). |
| `RAG_CONNECT_TIMEOUT_SECONDS` | `3.0` | Postgres connect timeout and the start-up wait in `create_rag_adapter()`. |
| `RAG_STATEMENT_TIMEOUT_MS` | `2500` | Server-side `statement_timeout` on every pooled connection (a bit above the router's 2 s): the database cancels a runaway query itself and the connection returns to the pool. |
| `RAG_LOCK_TIMEOUT_MS` | `1000` | Server-side `lock_timeout` on pooled connections. |
| `RAG_DEVICE` | auto | `cpu`, `mps` or `cuda` for the torch models (ignored by the ONNX encoder, which is CPU only). |
| `RAG_ENCODER` | `torch` | Query-encoder backend: `torch` (sentence-transformers; also what embeds the corpus) or `onnx-int8` (onnxruntime + tokenizers, no torch). The corpus vectors are never re-embedded. |
| `RAG_ENCODER_THREADS` | `2` | onnxruntime `intra_op_num_threads` of the ONNX encoder. |
| `RAG_ENCODER_MODEL_DIR` | `rag/data/models/bge-m3-onnx-int8` | Directory with `model.onnx`, `model.onnx.data`, `tokenizer.json` (see below). |
| `RAG_ENCODER_VERIFY` | `true` | Check size and sha256 of those files against `rag/data/encoder_manifest.json` at load (about 1-2 s). `false` only checks sizes: a corrupted file of the right size would pass silently. |
| `RAG_FILTER_DIFFICULTY` | `true` | Map request difficulty to LeetCode tiers for `source=question`. |
| `RAG_FILTER_COMPETENCY` | `false` | Pass `competency` as a (lenient) filter. Off because no chunk has a competency label yet. |
| `RAG_DROP_LOW_CONFIDENCE` | `false` | Return no chunks when the retrieval is `low_confidence` instead of flagging them. |

## Query encoder: torch or onnx-int8

Retrieval needs one bge-m3 vector per query. Two interchangeable backends produce it (`RAG_ENCODER`):

| | `torch` (default) | `onnx-int8` |
|---|---|---|
| runtime | sentence-transformers + torch | onnxruntime + tokenizers + numpy (no torch / transformers / sentence-transformers; `tests/rag/test_onnx_no_torch.py` checks this in a fresh interpreter) |
| use | local development, and the only backend that embeds the corpus | serving on a small CPU box |
| weights | `BAAI/bge-m3` fp16/fp32 | the same weights, dynamic int8 (`uint8-pc`: per-channel QUInt8 weights, uint8 activations), 587 MB on disk |
| query semantics | no prefix, CLS pooling, L2 norm, 1024 tokens max | identical (pooling checked: `modules.json` = Transformer, Pooling(cls), Normalize; the fp32 ONNX export reproduces torch fp32 to cosine 1.0000) |

`index_meta` still describes the torch vectors in the database. An `onnx-int8` encoder built from the same base
model, revision, dim, max_seq_length and format is accepted for *queries*; any other model is refused, and a
derived encoder can never define `index_meta` or embed the corpus. The encoder that produced a result is recorded
in `RetrievalResult.encoder` and in every chunk's `metadata["encoder"]` (`"torch"` or `"onnx-int8/uint8-pc"`).
If the model files are missing or corrupt, `create_rag_adapter()` / `warmup()` / the first `retrieve()` raise
`RAGUnavailableError` (also when the model is loaded lazily because `warmup()` was skipped).

**Artifact.** The ONNX is the *official* export in the `onnx/` folder of `BAAI/bge-m3` at the same revision the
index was built from (`5617a9f6...`, MIT licence). `python -m rag.cli encoder build-onnx` downloads it (fp32, 2.3 GB),
runs `onnxruntime.quantization.quantize_dynamic` (about 30 s, 3.5 GB RAM peak), writes the weights as an external-data
file and records everything in `rag/data/encoder_manifest.json` (committed: sha256 and size of every file, source
revision, quantization settings). The build is deterministic (rebuilding gives the same sha256). The files are
gitignored (`rag/data/models/`); a server gets them by copying, see
[docs/TEAM_INTEGRATION.md](docs/TEAM_INTEGRATION.md) section 6 (it also lists what the deploy owner has to do).
`encoder verify [--smoke]` checks a directory against the manifest (`--dir`, else `$RAG_ENCODER_MODEL_DIR`, else
the default under `rag/data/models/`); `--smoke` also loads the model and compares six query vectors with the
committed torch reference (use it on the machine that will serve: it prints cosine, latency and peak RSS). On a
serve-only install call `python -m rag.index.encoder_commands verify --smoke`, because `rag.cli` imports the
ingestion pipeline. Do not quantize on the 2.3 GB server. `RAG_ENCODER_VERIFY=false` skips the sha256 and only
checks file sizes, so a same-size corrupted file would load silently: keep it `true` unless start-up time matters.

**Which recipe.** Seven recipes were built (per-tensor, per-channel, QInt8 vs QUInt8, `reduce_range`, fp16 embeddings,
attention or last layers kept fp32; table in the report). `uint8-pc` is the default because (a) its u8u8 matmuls avoid
the u8s8 `VPMADDUBSW` saturation path: onnxruntime's quantization docs ("When and why do I need to try U8U8?") say
that on AVX2/AVX512 machines without VNNI the u8s8 kernel can saturate, and that `reduce_range` or U8U8 are the
remedies (no such issue with VNNI or Arm; dynamic quantization has uint8 activations, so s8s8 is not an option), (b) it kept paraphrase and identifier queries closest to torch of the per-channel recipes, and
(c) it has the same size and speed as `int8-pc`. Fallbacks if `verify --smoke` fails on the server CPU: `int8-pt`, then
`int8-pc-rr` (7-bit weights, measurably worse: nDCG -0.020 [-0.043, -0.004]). **u8u8 latency on x86 is unmeasured**
(no x86 hardware here; the amd64 container was emulated). Caveat on the choice: 7 recipes were compared on the same 40
draft queries, so the winner is partly selected by noise; its numbers are optimistic, not a held-out estimate.
`int8-pc` (the first default) was dropped after review showed it lost most on paraphrase queries.

**Measured** (Apple M5, arm64, 16 GB, Python 3.13, onnxruntime 1.23.2; local pg17 index of 16,379 chunks, rerank off;
full tables and all variants in `data/reports/encoder-onnx-int8-20261015.md`). Deltas are int8 minus torch, paired
bootstrap 95% CI, and "p" is the exact sign-flip permutation test:

| | torch (fp16, MPS; today's numbers) | `uint8-pc` | delta (CI) |
|---|---|---|---|
| query vector cosine vs torch, 98 queries | 1 | mean 0.9889, min 0.9834, p5 0.9860 | |
| main gold (40), hybrid nDCG@10 / R@10 / MRR@10 | 0.595 / 0.664 / 0.672 | 0.594 / 0.665 / 0.676 | -0.001 [-0.026, +0.018] / +0.001 [-0.008, +0.011] / +0.004 [-0.033, +0.040] |
| main gold, dense-only nDCG@10 | 0.552 | 0.551 | -0.001 [-0.027, +0.021] |
| paraphrase queries (n=9), hybrid nDCG@10 | 0.208 | 0.224 | +0.017 [-0.012, +0.064], p=1.0 (3 of 9 changed) |
| identifier slice (10), hybrid nDCG@10 | 0.640 | 0.623 | -0.017 [-0.036, -0.003], p=0.062 (5 of 10 changed) |
| top-10 overlap with the torch ranking | | 0.92 (gold), 0.91 (identifier) | |
| confidence: gold high/medium/low | 27 / 13 / 0 | 26 / 14 / 0 | g18 high to medium |
| confidence: 22 negatives low/medium/high | 18 / 4 / 0 | 18 / 4 / 0 | none changed |
| mean `max_dense` shift (gold) | | -0.005 (range -0.022 to +0.012) | thresholds unchanged |

Honest reading: overall and paraphrase are indistinguishable from torch, but the identifier slice loses 0.017 nDCG
hybrid (CI excludes 0, exact p=0.062) and 0.023 dense-only (CI excludes 0, exact p=0.047); n=10 and 7 recipes were
tried, so this is a likely small real loss on identifier queries, not a settled one. For the earlier default `int8-pc` the
paraphrase loss was hybrid -0.057 [-0.131, -0.003] and dense -0.018 [-0.047, -0.001] (both CIs exclude 0), but it comes
from 3 of 9 queries and the exact permutation test gives p=0.25 for both. With n=9 and 10, treat all of these as
hints. The one gold query that loses `high` sits 0.005 below the 0.62 cut-off, in the conservative direction; the
thresholds (0.52 / 0.62) were **not** retuned.

| process (warm-up + 50 queries, fresh process, Apple arm64) | peak RSS | cold load | encode p50 / p95 |
|---|---|---|---|
| `onnx-int8`, adapter with DB pool, 2 threads | 0.78 GB | 0.7 s | 12 / 18 ms (encoder only); hybrid retrieve 154 / 325 ms (default pg17 container), 78 / 208 ms (small tuned pg14 container) |
| `onnx-int8`, 1 thread | 0.78 GB | 0.6 s | 20 / 33 ms |
| torch fp16 on MPS (dev default), adapter | 2.3-3.8 GB peak (see below), 2.1 GB steady | 7-12 s | 15 / 17 ms |
| torch CPU fp32, encoder only | 2.05 GB | 6-8 s | 42 / 44 ms |

The torch MPS peak is a transient while the fp32 `.bin` is loaded and cast to fp16, and it varies: `encoder rss torch`
(`ru_maxrss` of a fresh process) gave 2.33 GB once and 3.73-3.77 GB in six later runs here, and a reviewer measured
2.4 GB; the steady state is 2.1 GB in every run.

Of the 0.78 GB, the XLM-R tokenizer alone is 0.39 GB (the `tokenizers` crate builds a large trie for the 250k-piece
vocabulary) and the memory-mapped weights about 0.34 GB; the weights are clean page cache, so the kernel can drop them
under pressure (the process still ran inside a 512 MB cgroup under emulation). Using `sentencepiece` for tokenization would
save about 0.3 GB with identical ids on the 98 test queries, but it is not in the deploy lock and was not adopted.

**x86 caveat.** All timings above are Apple arm64. The target server is x86_64 and was only exercised in a
`linux/amd64` container under qemu emulation (colima on this Mac is arm64): there the serve install, warm-up, search,
RSS (0.76-0.80 GB) and vector agreement (same cosine as arm64) are valid, but latency (2 s per encode) is 100x
pessimistic and meaningless, and it cannot show how the real AVX2/VNNI kernels behave. Run `encoder verify --smoke` on
the server (cosine must be >= 0.97).

Notes: dynamic quantization takes the activation scale from the whole batch, so a query's vector would depend on its
neighbours; `OnnxQueryEncoder.encode` therefore runs one text per forward pass by default. Reranking needs torch and
is not available with the torch-free install (`RAG_RERANK=true` is refused). Corpus embedding always uses torch.

## Wiring the adapter into the Agent

CLI and backend now inject a shared, prewarmed adapter via `app/adapters/rag.py`.
`RAG_ENABLED` defaults to `true`; set it to `false` to disable initialization. Missing dependencies,
an unavailable database or failed warmup produce a warning and ordinary questions continue.
Backend ASGI startup/shutdown warms/closes the shared adapter; CLI closes it when its run ends.
For direct service construction, the equivalent wiring is:

```python
from rag.adapter import RAGUnavailableError, create_rag_adapter

# once, at process start-up (the model load takes seconds, the Agent's RAG timeout is 2 s)
try:
    rag = create_rag_adapter()
    await rag.warmup()  # runs SELECT 1 on the pool, caches domains, loads bge-m3, checks index_meta, encodes once
except RAGUnavailableError:  # extra not installed / DB down / not migrated
    rag = None  # the Agent runs without retrieval, as today

service = InterviewAgentService(
    repository=repository,
    evaluation=evaluation,
    llm=llm,
    settings=settings,
    rag=rag,  # <- new
    background_replanning=True,
)
# on shutdown: await rag.aclose()
```

Things the integrating team must know:

* With ReAct enabled, after an answered project question the controller attempts one technical
  knowledge question in that project's current thread. It retrieves up to three technical chunks,
  drops low-confidence/empty evidence, locks the target and runs ReAct in fixed-intent mode. Both
  generation and quality review receive bounded reference text. Existing stop/time/question limits
  still apply. Retrieval or generation failure falls back to ordinary questions without another
  attempt for that project. Explicit unknown/refusal never triggers this extra follow-up.
* `InterviewContext.project_knowledge` persists attempt status, committed question ID, chunk IDs and
  source URLs with the turn; CLI results also expose it. Retrieval alone never marks a question asked.
* `RAGRouter` bounds each call with `timeouts.rag_seconds` (default 2.0 s). Warm queries measured
  230-470 ms (Apple MPS, 16k chunks, hybrid, no rerank). A cold first query would time out, hence `warmup()`.
* The legacy non-ReAct path still follows `required_context_sources` (normally `candidate`). The new
  project knowledge question requests `technical`, so it does not require ingested resume chunks.
* `candidate_id` in the request must equal the `candidate_id` the resume chunks were ingested under
  (`app/` uses `candidate-<interview_id>`).
* Run `create_rag_adapter()` once per process and share the instance; it is safe for concurrent calls.

## RAGPort mapping

`RAGAdapter.retrieve(RetrievalRequest) -> RetrievalResponse`, async. The synchronous retriever runs in
`asyncio.to_thread`; the event loop is never blocked.

| `RetrievalRequest` | Retrieval layer | Notes |
|---|---|---|
| `source` | `namespaces=[candidate\|technical\|question\|job]` | exactly one namespace per request; `chunk.source` echoes the request source |
| `query` | `RetrievalIntent.query` | blank / NUL-only query returns no chunks (not an error) |
| `top_k` (1-20) | `k` | |
| `intent` | `purpose` | tracing tag only |
| `difficulty` (1-5) | `difficulty` = Easy (1-2), Medium (3), Hard (4-5) | **`source=question` only**, lenient (unlabeled chunks still qualify). The only difficulty vocabulary in the corpus is LeetCode's three tiers; technical, job and candidate chunks have none, so nothing is filtered there. Disable with `RAG_FILTER_DIFFICULTY=false`. |
| `competency` | `target_competency` | **not applied by default**: no chunk carries a competency label (BARS pending), and the vocabularies are not aligned. Opt in with `RAG_FILTER_COMPETENCY=true`. |
| `domain` | `filters.domain` | only for `technical` / `question`, and only when the value (normalised: case, `_`/space to `-`) is one of the corpus domains (e.g. `ml-serving`, `web-backend`); unknown free text is ignored rather than turned into a filter that matches nothing |
| `topic` | not used | already part of the router's query text; the `topic` column is just `heading_path[0]` |
| `candidate_id` | `candidate_id` (owner scope) | forwarded for `source=candidate` only; see below |
| `project_id` | not used | no chunk has a project key; recorded as a limitation |
| `interview_id`, `request_id` | not used / echoed | `request_id` is echoed in the response |

Response:

| Field | Value |
|---|---|
| `chunks[].chunk_id` | stable chunk id |
| `chunks[].title` | chunk title, else last heading |
| `chunks[].content` | raw chunk text. It is third-party data; callers must keep it out of instruction positions (`ContextBuilder` already escapes and delimits it) |
| `chunks[].retrieval_score` | the rerank probability if `RAG_RERANK=true`, otherwise the **RRF fusion score** (dense weight 2, lexical 1, k=10; roughly 0.0-0.3 and only comparable within one query, not across queries). `metadata["score_kind"]` says which (`rerank`, `rrf`, or the single-stage mode) |
| `chunks[].metadata` | `untrusted=True`, `namespace`, `source_name`, `source_url`, `doc_version`, `heading_path`, `domain`, `difficulty`, `trust_level`, `license`, `rank`, `score_kind`, `scores{dense,lexical,rrf,rerank}`, `stage_ranks`, `confidence` (`low\|medium\|high`), `low_confidence`, `confidence_reasons`, `max_dense`, `max_rerank`, `lexical_hit`, and `candidate_id` for candidate chunks |
| `latency_ms` | wall time of the adapter call including queueing for a pooled connection |
| `partial` | **always `False` from the adapter.** In this codebase `partial` is set by `RAGRouter` on the successful responses of a batch when *another* source of the same batch failed. Low confidence is reported in metadata, not in `partial` |

Failure semantics (matches `RAGRouter.retrieve_with_diagnostics`, which collects any exception per
source and records `RAGTimeout` separately):

* no results, blank query, or candidate source without `candidate_id`: empty `chunks`, no exception;
* DB unreachable, pool exhausted (`PoolTimeout`), embedding-model / `index_meta` mismatch, model load
  failure, or a role that may not read the requested namespace (`NamespaceAccessError`): the
  exception propagates unchanged and the router marks that source failed;
* cancellation by the router's timeout only stops *waiting*: the worker thread keeps its pooled
  connection until its statement finishes or `RAG_STATEMENT_TIMEOUT_MS` cancels it server-side.
  Repeated timeouts can therefore briefly occupy pool slots (new calls then fail fast with
  `PoolTimeout` after `RAG_POOL_TIMEOUT_SECONDS`), but no query runs unbounded. Model loading or
  an in-flight embedding is not interruptible and is not covered by the statement timeout.

## Candidate isolation (security-critical)

Resume chunks (`namespace=candidate`) may only be returned to the candidate they belong to.

1. **Ownership is data.** Migration 003 adds `chunks.candidate_id` with
   `CHECK ((namespace = 'candidate') = (candidate_id IS NOT NULL AND candidate_id <> ''))`: a candidate
   chunk cannot exist without an owner, and no other namespace can carry one. The ingestion `Chunk`
   model enforces the same rule (`candidate_id` required for candidate chunks, forbidden elsewhere).
2. **Fail closed.** `RetrievalIntent.candidate_id` is the scope. Without it the `candidate` namespace
   is removed before any SQL is built (`access.scope_namespaces`); the adapter does not even call the
   backend.
3. **Impossible to build an unscoped predicate.** `sql.build_filter_clause` raises if the namespace
   list contains `candidate` and no owner is set; the owner is a bound parameter (dense and lexical
   queries share the clause). Hydration (`FETCH_SQL`) repeats the owner check.
4. **Defence in depth.** After retrieval the retriever re-checks namespace and owner on every row, and
   the adapter re-checks them again (also dropping any candidate-namespace chunk returned for a
   non-candidate request) and logs an error if it ever has to drop something.
5. `candidate_id` has no effect on other namespaces (technical/question/job are shared knowledge).

Tests: `tests/rag/test_index_db.py` (real Postgres, two candidates, dense / lexical / hybrid, SQL
injection attempt, CHECK constraint), `tests/rag/test_candidate_scope.py`, and
`tests/rag/test_adapter.py` (two candidates through the adapter, plus a deliberately leaky backend).

Not covered: the DSN is a Postgres superuser and role ACLs live in Python, not in the database.
Anything holding the DSN can read every candidate. Row-level security with a read-only role is the
real fix (BACKLOG).

## Confidence semantics

Retrieval always returns up to `k` chunks, even for an unrelated query. The layer therefore also
reports `confidence` per call (the same value is copied onto each returned chunk):

* `low` (`low_confidence=true`): **unverified**, i.e. the corpus gave no good evidence for this
  query (no results, only stop words, `max_dense < 0.52`, or reranker `< 0.30`). It does **not**
  mean a claim is false, and it does not mean the topic is absent from the world, only that this
  corpus could not corroborate it. Treat it as "no evidence found", never as a contradiction.
* `medium`: usable, but corroborate (no lexical match or `max_dense < 0.62`).
* `high`: lexical match and `max_dense >= 0.62` (and rerank `>= 0.80` if it ran).

The thresholds were calibrated on the same 40 draft queries they are reported on (no held-out set)
and the lowest gold query sits 0.008 above the cut-off. Pure non-ASCII queries have no lexical
signal, so they cannot reach `high`. Today the Agent's `ContextBuilder` ignores these flags (see
`contract-proposals.md`).

## Tests

```bash
uv run pytest tests/rag -q          # with the extra: DB tests use a throwaway database
```

Without the rag extra, modules that need `requests`, `numpy`, `psycopg`, `pgvector` or `openpyxl`
skip via `pytest.importorskip`; the adapter, candidate-scope and settings tests still run. With the
extra but without Postgres, DB tests skip; with the local container up, `test_adapter_integration.py`
exercises the real adapter (PagedAttention, `select_related`, Kafka exactly-once). The adapter unit
tests use a fake backend and need neither a database nor a model. The existing contract test
(`tests/agent/contracts/test_rag_port_contract.py`) is not parametrised over implementations, so the
same assertions are repeated for the adapter in `tests/rag/test_adapter.py`.

Encoder tests: `test_encoder_selection.py` (settings, labels, factory errors), `test_onnx_encoder_units.py` (pooling,
normalisation, padding, truncation, manifest checks on a tiny committed fixture model), `test_onnx_no_torch.py` (no
torch-family import, fresh interpreter), `test_index_meta_compat.py` (accept / refuse rules), `test_onnx_real.py` (real
artifact and index; skips without them). Each module `importorskip`s only what it uses, so the deploy workflow's locked
environment (no pgvector, no torch) runs them without errors.

## Known limitations

* The documented index has technical (13,068) and question (3,311) chunks only,

`tests/rag/test_corpus_expansion.py` covers the batch-1 cleaners (`mdn`, `asciidoc`, `msdocs`, `jekyll`,
`quarto`, `notebook`, Hugo / VitePress / MDX additions), the STIX parser, the Wikipedia loader (with a
faked API) and the registry licence rule; it needs only PyYAML, pydantic and (for the Wikipedia loader)
`requests`. `tests/rag/test_chunks_e2e.py` runs its invariants over every `data/chunks/*.jsonl` that
exists and skips otherwise.

## Known limitations

* Not wired into the application; the index has technical (60,939) and question (3,311) chunks only,
  no candidate, job or rubric data. `RetrievalSource` has no `RUBRIC`.
* Query quality is the weakest link: `RAGRouter._query` builds terse strings
  (`"{topic} candidate project claims"`, `"{topic} {information_goal}"`) and paraphrase-style queries
  score nDCG@10 about 0.21 (0.208 hybrid, n=9). See `docs/contract-proposals.md`.
* The 40-query gold set is a draft written from chunk metadata; hybrid's advantage over dense-only and
  rerank's advantage over hybrid are not statistically established. Rerank is therefore off by default.
* Dense search is an exact scan on purpose (`exact_dense=True`); the HNSW index exists but is not used
  by the default retriever. A regression test pins the plan. See "Retrieval after the expansion".
* The embedding model is serialized behind a lock (one encode at a time); throughput under heavy
  concurrency is bounded by one model instance. Per-process model: run N workers, N copies of bge-m3 in RAM
  (about 0.8 GB each with `onnx-int8`, more than 2 GB with torch).
* Lexical search is ASCII-only; Chinese queries are dense-only.
* The long design notes, evaluation tables and per-source limitations are in
  `docs/pipeline-and-index.md`; open work is in `BACKLOG.md`.
