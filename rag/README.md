# RAG module

Production retrieval layer for the interview Agent: knowledge ingestion, a Postgres + pgvector
index, hybrid retrieval (dense bge-m3 + lexical, fused by RRF, optional cross-encoder rerank), role
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
├── index/              schema.sql + migrations/, embed.py (bge-m3), load.py, db.py, config.py
├── loaders/ + clean.py, chunking.py, profiles.py, htmlmd.py, cli.py   ingestion pipeline
├── eval/               gold-set evaluation, tuning, confidence study
├── data/               sources.yaml (registry), eval/*.yaml (gold sets), reports/*.md (small)
├── docker-compose.yml  pgvector/pgvector:pg17 on 127.0.0.1:5432
├── .env.example        dev-only Postgres defaults (copy to rag/.env, which is gitignored)
├── requirements.txt    optional dependencies (NOT part of the root pyproject / uv.lock)
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

`rag/adapter.py`, `rag/settings.py` and `rag/retrieval/{lazy,access,intent,confidence,types}.py`
import without the extra, so the adapter unit tests run everywhere. `create_rag_adapter()` raises
`RAGUnavailableError` (with the install command) if the extra is missing.

## Setup (once per machine)

Run from the repository root.

```bash
cp rag/.env.example rag/.env                          # dev-only defaults; the file is gitignored
docker compose -f rag/docker-compose.yml up -d        # Postgres 17 + pgvector, healthchecked
uv run python -m rag.cli migrate                      # schema.sql + migrations 002, 003 (idempotent; prints the target db)

uv run python -m rag.cli all --priority P0            # fetch -> clean -> chunk -> report
uv run python -m rag.cli embed --all                  # bge-m3 vectors -> rag/data/embeddings (resumable)
uv run python -m rag.cli index --all                  # idempotent upsert into Postgres
uv run python -m rag.cli search "How does PagedAttention reduce KV cache fragmentation?" --role question_planner
```

Measured on an Apple-silicon laptop (numbers from the original runs, 22 P0 sources / 16,379 chunks):

| Step | Time | Disk |
|---|---|---|
| bge-m3 download (first `embed` or first adapter use) | network bound | ~2.3 GB in `~/.cache/huggingface` (+ ~2.3 GB if you enable rerank) |
| `all --priority P0` (shallow clones, HF/arXiv fetch, crawl) | network bound, not timed in the original run; resumable and cached | `rag/data/raw` ~314 MB, `clean` ~47 MB, `chunks` ~34 MB (all gitignored) |
| `embed --all` (MPS, fp16) | 792.6 s (13.2 min), 20.7 chunks/s; re-run with no changes 0.2 s | `rag/data/embeddings` ~69 MB |
| `index --all` (cold) | 33 s; re-run 4 s, 0 rows rewritten | Postgres ~330 MB (table 151 MB, HNSW 128 MB) |

Skipping ingestion: the index can also be restored from a `pg_dump` of an already loaded database;
nothing in Git contains the corpus.

Existing databases: `migrate` is additive. Migration 003 (candidate isolation) adds
`chunks.candidate_id` and a CHECK constraint; the adapter refuses to start (clear error) until it is
applied.

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
| `RAG_DEVICE` | auto | `cpu`, `mps` or `cuda` for the models. |
| `RAG_FILTER_DIFFICULTY` | `true` | Map request difficulty to LeetCode tiers for `source=question`. |
| `RAG_FILTER_COMPETENCY` | `false` | Pass `competency` as a (lenient) filter. Off because no chunk has a competency label yet. |
| `RAG_DROP_LOW_CONFIDENCE` | `false` | Return no chunks when the retrieval is `low_confidence` instead of flagging them. |

## Wiring the adapter into the Agent

Not wired in this change: `app/` and `backend/` belong to other teams, and `app/adapters/` has no RAG
slot today (it has `llm`, `evaluation`, `repository`, ...). `InterviewAgentService` already accepts
`rag: RAGPort | None = None`; both construction sites (`app/application.py`,
`backend/interviews/agent_session.py`) simply do not pass it. The integrating team adds:

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

* The Agent only calls RAG on the non-autonomous question path (`self._question_agent is None` in
  `agents/orchestrator/service.py`). With the ReAct question agent enabled the adapter is never called.
* `RAGRouter` bounds each call with `timeouts.rag_seconds` (default 2.0 s). Warm queries measured
  230-470 ms (Apple MPS, 16k chunks, hybrid, no rerank). A cold first query would time out, hence `warmup()`.
* The default plan requests only `source=candidate` (`required_context_sources`), and there are no
  candidate or job chunks in the index yet, so until resumes are ingested the Agent would receive
  empty results from this adapter.
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

## Known limitations

* Not wired into the application; the index has technical (13,068) and question (3,311) chunks only,
  no candidate, job or rubric data. `RetrievalSource` has no `RUBRIC`.
* Query quality is the weakest link: `RAGRouter._query` builds terse strings
  (`"{topic} candidate project claims"`, `"{topic} {information_goal}"`) and paraphrase-style queries
  score nDCG@10 about 0.21 (0.208 hybrid, n=9). See `docs/contract-proposals.md`.
* The 40-query gold set is a draft written from chunk metadata; hybrid's advantage over dense-only and
  rerank's advantage over hybrid are not statistically established. Rerank is therefore off by default.
* Dense search is currently an exact scan (a side effect of `hnsw.ef_search=100` at 16k rows); the
  plan may flip to approximate HNSW as the corpus grows. A regression test pins the current plan.
* The embedding model is serialized behind a lock (one encode at a time); throughput under heavy
  concurrency is bounded by one model instance. Per-process model: run N workers, N copies of bge-m3 in RAM.
* Lexical search is ASCII-only; Chinese queries are dense-only.
* The long design notes, evaluation tables and per-source limitations are in
  `docs/pipeline-and-index.md`; open work is in `BACKLOG.md`.
