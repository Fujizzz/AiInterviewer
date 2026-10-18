"""Pool-backed, thread-safe retrieval backend (the production backend of the RAGPort adapter).

`Retriever` owns a single psycopg connection and is not thread-safe, but `RAGRouter` fans out one
request per source with asyncio.gather and the adapter runs each in a worker thread. This class
keeps ONE pool and ONE embedding model per process and builds a cheap `Retriever` per call that
borrows a pooled connection for the duration of the call:

* connections: psycopg_pool.ConnectionPool (autocommit, pgvector registered, bounded size). When
  the pool is exhausted callers wait at most `pool_timeout`, then PoolTimeout is raised, which the
  router records as a failed source instead of piling up unbounded work.
* models: LazySingleton (loaded once, on first use or in `warmup()`); `encode` is serialized. The
  query encoder is `torch` (sentence-transformers) or `onnx-int8` (onnxruntime, no torch).
* the embedding-identity check (index_meta vs the loaded model) runs once, not per call.

Requires psycopg_pool, psycopg, pgvector, numpy and the chosen encoder's runtime
(sentence-transformers, or onnxruntime + tokenizers).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .confidence import RetrievalResult
from .intent import RetrievalIntent
from .lazy import LazySingleton, SerializedEmbedder, SerializedReranker

logger = logging.getLogger(__name__)

# Real retrievals run once at start-up so the first user request does not pay for cold caches. On
# the 95k-chunk corpus a cold dense scan reads ~850 MB (4-25 s, against the pool's 2.5 s
# statement_timeout and the Agent's 2 s RAG timeout); a warm one takes ~0.8 s. The identifier-shaped
# query warms the trigram index used by `text ILIKE '%Identifier%'`, which plain words never touch.
WARMUP_QUERIES = (
    "how does a database index work",
    "useState HashMap",
    "explain the difference between a process and a thread",
)
WARMUP_ROLE = "question_planner"
WARMUP_STATEMENT_TIMEOUT = "120s"


def _build_embedder(
    device: str | None,
    encoder: str = "torch",
    threads: int = 2,
    model_dir: str | None = None,
    verify: bool = True,
) -> Any:
    from ..index.encoders import build_query_encoder

    return build_query_encoder(
        encoder, device=device, threads=threads, model_dir=model_dir, verify=verify
    )


def _build_reranker(device: str | None) -> Any:
    from .rerank import Reranker

    return Reranker(device=device)


class PooledRetriever:
    def __init__(
        self,
        dsn: str,
        *,
        pool_min: int = 1,
        pool_max: int = 4,
        pool_timeout: float = 1.5,
        connect_timeout: float = 3.0,
        statement_timeout_ms: int = 2500,
        lock_timeout_ms: int = 1000,
        device: str | None = None,
        rerank_available: bool = False,
        encoder: str = "torch",
        encoder_threads: int = 2,
        encoder_model_dir: str | None = None,
        encoder_verify: bool = True,
    ) -> None:
        from psycopg_pool import ConnectionPool

        self._dsn = dsn
        self._embedder_loader: LazySingleton[Any] = LazySingleton(
            lambda: _build_embedder(
                device, encoder, encoder_threads, encoder_model_dir, encoder_verify
            )
        )
        self._reranker_loader: LazySingleton[Any] = LazySingleton(lambda: _build_reranker(device))
        self._embedder = SerializedEmbedder(self._embedder_loader)
        self._reranker = SerializedReranker(self._reranker_loader)
        self._rerank_available = rerank_available
        self._meta_lock = threading.Lock()
        self._meta_checked = False
        self._domains: frozenset[str] | None = None
        self._pool = ConnectionPool(
            dsn,
            min_size=pool_min,
            max_size=pool_max,
            timeout=pool_timeout,
            kwargs=self.connection_kwargs(connect_timeout, statement_timeout_ms, lock_timeout_ms),
            configure=self._configure,
            open=False,
            name="rag-retrieval",
        )

    @staticmethod
    def connection_kwargs(
        connect_timeout: float, statement_timeout_ms: int, lock_timeout_ms: int
    ) -> dict[str, object]:
        """libpq settings for every pooled connection.

        The server-side timeouts matter because asyncio cancellation (RAGRouter's timeout) only
        stops *waiting*: the worker thread keeps its connection until the statement ends.
        statement_timeout makes the database cancel a runaway query itself, which frees the
        connection (and the pool slot) instead of leaving it busy indefinitely.
        """
        return {
            "connect_timeout": int(max(1, round(connect_timeout))),
            "options": f"-c statement_timeout={int(statement_timeout_ms)} "
            f"-c lock_timeout={int(lock_timeout_ms)}",
        }

    # -- lifecycle --------------------------------------------------------
    @staticmethod
    def _configure(conn: Any) -> None:
        from pgvector.psycopg import register_vector

        conn.autocommit = True
        register_vector(conn)

    def open(self, wait_timeout: float = 5.0) -> None:
        """Open the pool and block until `pool_min` connections work (raises PoolTimeout)."""
        self._pool.open(wait=True, timeout=wait_timeout)

    def close(self) -> None:
        self._pool.close(timeout=1.0)

    def warmup(self) -> None:
        """Load the models, verify the index embedding identity, touch the database and encode.

        Runs a trivial query on a pooled connection (so the pool is warm and the database
        reachable), caches the domain vocabulary, loads bge-m3, checks `index_meta` against the
        model and encodes one dummy string.

        Then runs a few real retrievals (`_prime_caches`) so the database caches are warm too.

        Call this at application start-up: the first real query would otherwise pay the model
        load (seconds) and exceed the Agent's per-call RAG timeout.
        """
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")
        self.known_domains()
        self._ensure_index_matches_model()
        self._embedder.encode(["warmup"])
        if self._rerank_available:
            self._reranker.score("warmup", ["warmup"])
        self._prime_caches()

    def _prime_caches(self) -> None:
        """Run `WARMUP_QUERIES` through the normal retrieval path, in parallel, timeout lifted.

        The pool's `statement_timeout` (2.5 s) would cancel a cold scan before it finished, leaving
        the cache only partly warm and failing every request until it filled. `SET LOCAL` lifts the
        limit for each warm-up transaction only; the pooled connections keep their normal timeout.
        The queries run concurrently (one pooled connection each, never more than the pool allows)
        so the cold dense scan and the index reads overlap instead of adding up.
        A failure here is logged and ignored: warm-up must never stop RAG from starting.
        """
        from concurrent.futures import ThreadPoolExecutor

        started = time.perf_counter()
        workers = max(1, min(len(WARMUP_QUERIES), self._pool.max_size))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rag-warmup") as pool:
            ok = list(pool.map(self._prime_one, WARMUP_QUERIES))
        failed = ok.count(False)
        logger.info(
            "RAG cache warm-up: %d queries (%d in parallel) in %.1fs, %d failed",
            len(WARMUP_QUERIES),
            workers,
            time.perf_counter() - started,
            failed,
        )

    def _prime_one(self, query: str) -> bool:
        from .retriever import Retriever

        try:
            with self._pool.connection() as conn, conn.transaction():
                conn.execute(f"SET LOCAL statement_timeout = '{WARMUP_STATEMENT_TIMEOUT}'")
                retriever = Retriever(embedder=self._embedder, connection=conn, check_model=False)
                retriever.retrieve(RetrievalIntent(query=query, k=5), WARMUP_ROLE)
        except Exception:  # noqa: BLE001 - start-up must survive a failed warm-up query
            logger.warning(
                "RAG cache warm-up failed for %r; first queries may be slow", query, exc_info=True
            )
            return False
        return True

    def known_domains(self) -> frozenset[str]:
        """Distinct non-null chunk.domain values (cached for the life of the process)."""
        if self._domains is None:
            with self._pool.connection() as conn:
                rows = conn.execute("SELECT DISTINCT domain FROM chunks WHERE domain IS NOT NULL")
                self._domains = frozenset(str(r[0]) for r in rows.fetchall())
        return self._domains

    def check_schema(self) -> None:
        """Raise RuntimeError if the database was not migrated for this version of the module."""
        with self._pool.connection() as conn:
            has = conn.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'chunks' AND column_name = 'candidate_id'"
            ).fetchone()
        if not has or not has[0]:
            raise RuntimeError(
                "chunks.candidate_id is missing: run `python -m rag.cli migrate` "
                "(migration 003 enforces candidate isolation)"
            )

    # -- retrieval --------------------------------------------------------
    def _ensure_index_matches_model(self) -> None:
        if self._meta_checked:
            return
        with self._meta_lock:
            if self._meta_checked:
                return
            from ..index.load import check_index_meta

            meta = self._embedder.meta  # loads the model
            with self._pool.connection() as conn:
                check_index_meta(conn, meta, set_if_empty=False)
            self._meta_checked = True

    def retrieve(
        self,
        intent: RetrievalIntent,
        role: str,
        *,
        mode: str = "hybrid",
        rerank: bool = False,
    ) -> RetrievalResult:
        from .retriever import Retriever

        self._ensure_index_matches_model()
        with self._pool.connection() as conn:
            retriever = Retriever(
                embedder=self._embedder,
                reranker=self._reranker,
                connection=conn,
                check_model=False,  # done once in _ensure_index_matches_model
            )
            return retriever.retrieve(intent, role, mode=mode, rerank=rerank)
