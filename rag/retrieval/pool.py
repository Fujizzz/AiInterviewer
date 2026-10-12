"""Pool-backed, thread-safe retrieval backend (the production backend of the RAGPort adapter).

`Retriever` owns a single psycopg connection and is not thread-safe, but `RAGRouter` fans out one
request per source with asyncio.gather and the adapter runs each in a worker thread. This class
keeps ONE pool and ONE embedding model per process and builds a cheap `Retriever` per call that
borrows a pooled connection for the duration of the call:

* connections: psycopg_pool.ConnectionPool (autocommit, pgvector registered, bounded size). When
  the pool is exhausted callers wait at most `pool_timeout`, then PoolTimeout is raised, which the
  router records as a failed source instead of piling up unbounded work.
* models: LazySingleton (loaded once, on first use or in `warmup()`); `encode` is serialized.
* the embedding-identity check (index_meta vs the loaded model) runs once, not per call.

Requires the rag extra (psycopg_pool, psycopg, pgvector, sentence-transformers, numpy).
"""

from __future__ import annotations

import threading
from typing import Any

from .confidence import RetrievalResult
from .intent import RetrievalIntent
from .lazy import LazySingleton, SerializedEmbedder, SerializedReranker


def _build_embedder(device: str | None) -> Any:
    from ..index.embed import Embedder

    return Embedder(device=device)


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
        device: str | None = None,
        rerank_available: bool = False,
    ) -> None:
        from psycopg_pool import ConnectionPool

        self._dsn = dsn
        self._embedder_loader: LazySingleton[Any] = LazySingleton(lambda: _build_embedder(device))
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
            kwargs={"connect_timeout": int(max(1, round(connect_timeout)))},
            configure=self._configure,
            open=False,
            name="rag-retrieval",
        )

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
        """Load the models, verify the index embedding identity and run one dummy query.

        Call this at application start-up: the first real query would otherwise pay the model
        load (seconds) and exceed the Agent's per-call RAG timeout.
        """
        self._ensure_index_matches_model()
        self._embedder.encode(["warmup"])
        if self._rerank_available:
            self._reranker.score("warmup", ["warmup"])

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
