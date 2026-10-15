"""RAGPort adapter over the hybrid retrieval layer.

Implements `agents.ports.RAGPort` structurally (`async retrieve(RetrievalRequest) ->
RetrievalResponse`). This module only maps the Agent contract to `RetrievalIntent` and back; it
makes no interview-policy decisions (which source, when, how many -- that stays with RAGRouter).

Import cost: this file imports only `shared.contracts` and the pure parts of `rag.retrieval`. The
heavy dependencies (psycopg, torch) are touched only by `create_rag_adapter()` / `PooledRetriever`,
so the adapter can be unit-tested with a fake backend in an environment without the rag extra.

Contract notes (full table in rag/README.md):
* `RetrievalResponse.partial` is never set here. RAGRouter owns it: it flips `partial` on the
  successful responses of a batch when *another* source of the batch failed. Low confidence is NOT
  "partial"; it is reported per chunk in `metadata`.
* "No results" is an empty `chunks` list. Infrastructure failures (DB down, pool exhausted, model
  or index mismatch, role not allowed to read the namespace) are raised unchanged so RAGRouter
  records the source as failed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Collection
from typing import Any, Protocol

from shared.contracts import (
    Competency,
    RetrievalRequest,
    RetrievalResponse,
    RetrievalSource,
    RetrievedChunk,
)

from .retrieval.access import ROLE_NAMESPACES
from .retrieval.confidence import RetrievalResult
from .retrieval.intent import Filters, RetrievalIntent
from .retrieval.types import Passage
from .settings import RAGSettings

logger = logging.getLogger(__name__)

SOURCE_NAMESPACE: dict[RetrievalSource, str] = {
    RetrievalSource.CANDIDATE: "candidate",
    RetrievalSource.TECHNICAL: "technical",
    RetrievalSource.QUESTION: "question",
    RetrievalSource.JOB: "job",
}

# Agent difficulty 1-5 -> the only difficulty vocabulary in the corpus: LeetCode Easy/Medium/Hard
# (namespace `question`). Technical/job/candidate chunks carry no difficulty at all.
DIFFICULTY_LABEL: dict[int, str] = {1: "Easy", 2: "Easy", 3: "Medium", 4: "Hard", 5: "Hard"}


class RAGUnavailableError(RuntimeError):
    """The retrieval layer cannot be used (missing rag extra, database down, not migrated)."""


class RetrievalBackend(Protocol):
    """Synchronous retrieval callable the adapter runs in a worker thread."""

    def retrieve(
        self, intent: RetrievalIntent, role: str, *, mode: str = ..., rerank: bool = ...
    ) -> RetrievalResult: ...


def _owner_id(raw: str | None) -> str | None:
    """Normalised owner scope: surrounding whitespace is stripped; blank, or containing a NUL
    byte (never silently "repaired" into some other id), means no scope."""
    if raw is None or "\x00" in raw:
        return None
    return raw.strip() or None


def _normalise_domain(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "-")


class RAGAdapter:
    """`RAGPort` implementation. One instance per process; safe for concurrent `retrieve` calls."""

    def __init__(
        self,
        backend: RetrievalBackend,
        *,
        role: str = "question_planner",
        rerank: bool = False,
        mode: str = "hybrid",
        filter_difficulty: bool = True,
        filter_competency: bool = False,
        drop_low_confidence: bool = False,
        known_domains: Callable[[], Collection[str]] | None = None,
        on_warmup: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        if role not in ROLE_NAMESPACES:
            raise ValueError(f"unknown role {role!r}; known: {sorted(ROLE_NAMESPACES)}")
        self._backend = backend
        self._role = role
        self._rerank = rerank
        self._mode = mode
        self._filter_difficulty = filter_difficulty
        self._filter_competency = filter_competency
        self._drop_low_confidence = drop_low_confidence
        self._known_domains = known_domains
        self._domain_index: dict[str, str] | None = None
        self._on_warmup = on_warmup
        self._on_close = on_close

    # -- RAGPort ----------------------------------------------------------
    async def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        started = time.perf_counter()
        intent = self._build_intent(request)
        if intent is None:  # nothing to ask, or no candidate scope: answer "no results"
            return self._response(request, [], started)
        result = await asyncio.to_thread(
            self._backend.retrieve, intent, self._role, mode=self._mode, rerank=self._rerank
        )
        return self._response(request, self._to_chunks(request, result), started)

    # -- lifecycle --------------------------------------------------------
    async def warmup(self) -> None:
        """Load models and verify the index (call once at start-up, before serving)."""
        if self._on_warmup is not None:
            await asyncio.to_thread(self._on_warmup)

    def close(self) -> None:
        """Release pooled connections (synchronous; safe to call more than once)."""
        if self._on_close is not None:
            self._on_close()

    async def aclose(self) -> None:
        if self._on_close is not None:
            await asyncio.to_thread(self._on_close)

    # -- request -> intent ------------------------------------------------
    def _build_intent(self, request: RetrievalRequest) -> RetrievalIntent | None:
        source = request.source
        candidate_id = _owner_id(request.candidate_id)
        if source == RetrievalSource.CANDIDATE and candidate_id is None:
            # Fail closed: without an owner there is nothing a candidate may see. No DB call.
            return None
        try:
            return RetrievalIntent(
                query=request.query,
                namespaces=[SOURCE_NAMESPACE[source]],
                k=request.top_k,
                purpose=request.intent or None,
                difficulty=self._difficulty(request),
                target_competency=self._competency(request.competency),
                filters=Filters(domain=self._domain_filter(request)),
                candidate_id=candidate_id if source == RetrievalSource.CANDIDATE else None,
            )
        except ValueError:  # blank / NUL-only query: not an infrastructure error
            return None

    def _difficulty(self, request: RetrievalRequest) -> str | None:
        if request.source != RetrievalSource.QUESTION or not self._filter_difficulty:
            return None
        return DIFFICULTY_LABEL.get(request.difficulty)

    def _competency(self, competency: Competency | None) -> str | None:
        # No chunk carries a competency label yet (BARS pending), so by default nothing is filtered.
        return competency.value if competency and self._filter_competency else None

    def _domain_filter(self, request: RetrievalRequest) -> list[str]:
        """Filter on `domain` only when the request's domain is a value the corpus really has.

        An unknown free-text domain ("LLM serving") would otherwise turn into a hard filter that
        matches nothing. Only technical and question chunks have a meaningful domain.
        """
        if (
            not request.domain
            or self._known_domains is None
            or request.source not in (RetrievalSource.TECHNICAL, RetrievalSource.QUESTION)
        ):
            return []
        if self._domain_index is None:
            self._domain_index = {_normalise_domain(d): d for d in self._known_domains()}
        match = self._domain_index.get(_normalise_domain(request.domain))
        return [match] if match else []

    # -- result -> response -----------------------------------------------
    def _to_chunks(
        self, request: RetrievalRequest, result: RetrievalResult
    ) -> list[RetrievedChunk]:
        if self._drop_low_confidence and result.low_confidence:
            return []
        expected_ns = SOURCE_NAMESPACE[request.source]
        owner = _owner_id(request.candidate_id) or ""
        chunks: list[RetrievedChunk] = []
        for passage in result:
            # Defence in depth, independent of the backend's own filters: a misbehaving backend
            # must not be able to leak another namespace or another candidate's resume.
            if passage.namespace != expected_ns or (
                request.source == RetrievalSource.CANDIDATE and passage.candidate_id != owner
            ):
                logger.error(
                    "rag: dropped out-of-scope passage chunk_id=%s namespace=%s for source=%s",
                    passage.chunk_id,
                    passage.namespace,
                    request.source.value,
                )
                continue
            chunks.append(self._chunk(request, passage, result))
        return chunks

    def _chunk(
        self, request: RetrievalRequest, passage: Passage, result: RetrievalResult
    ) -> RetrievedChunk:
        score, kind = self._score(passage)
        return RetrievedChunk(
            chunk_id=passage.chunk_id,
            source=request.source,
            title=passage.title or (passage.heading_path[-1] if passage.heading_path else None),
            content=passage.text,  # raw chunk text; untrusted data, never wrapped as a prompt
            retrieval_score=score,
            metadata={
                "untrusted": True,
                "namespace": passage.namespace,
                "source_name": passage.source_name,
                "source_url": passage.source_url,
                "doc_version": passage.doc_version,
                "heading_path": list(passage.heading_path),
                "domain": passage.domain,
                "difficulty": passage.difficulty,
                "trust_level": passage.trust_level,
                "license": passage.license,
                "rank": passage.rank,
                "score_kind": kind,
                "scores": dict(passage.scores),
                "stage_ranks": dict(passage.stage_ranks),
                "confidence": result.confidence,
                "low_confidence": result.low_confidence,
                "confidence_reasons": list(result.reasons),
                "max_dense": result.max_dense,
                "max_rerank": result.max_rerank,
                "lexical_hit": result.lexical_hit,
                **({"candidate_id": passage.candidate_id} if passage.candidate_id else {}),
            },
        )

    def _score(self, passage: Passage) -> tuple[float | None, str]:
        """retrieval_score = rerank probability if the cross-encoder ran, else the RRF fusion
        score; in single-stage modes the stage score. `metadata['score_kind']` says which."""
        scores = passage.scores
        for kind in ("rerank", "rrf", self._mode):
            if scores.get(kind) is not None:
                return float(scores[kind]), kind  # type: ignore[arg-type]
        return None, "none"

    def _response(
        self, request: RetrievalRequest, chunks: list[RetrievedChunk], started: float
    ) -> RetrievalResponse:
        return RetrievalResponse(
            request_id=request.request_id,
            chunks=chunks,
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
            partial=False,
        )


def create_rag_adapter(
    settings: RAGSettings | None = None, *, warmup: bool = False, **overrides: Any
) -> RAGAdapter:
    """Build the production adapter (pooled Postgres + bge-m3), or raise RAGUnavailableError.

    `settings` defaults to `RAGSettings.from_env()`. With `warmup=True` the embedding model is
    loaded and the index verified before returning (blocking; takes seconds on a cold start);
    otherwise call `await adapter.warmup()` yourself during application start-up.
    """
    import importlib.util

    settings = settings or RAGSettings.from_env()
    missing = [
        m
        for m in ("psycopg", "psycopg_pool", "pgvector", "numpy", "sentence_transformers")
        if importlib.util.find_spec(m) is None
    ]
    if missing:
        raise RAGUnavailableError(
            f"retrieval dependencies missing: {', '.join(missing)}. "
            "Install the optional extra: uv pip install -r rag/requirements.txt"
        )

    from .index import config
    from .retrieval.pool import PooledRetriever

    dsn = settings.database_url or config.get_dsn()
    backend = PooledRetriever(
        dsn,
        pool_min=settings.pool_min,
        pool_max=settings.pool_max,
        pool_timeout=settings.pool_timeout_seconds,
        connect_timeout=settings.connect_timeout_seconds,
        statement_timeout_ms=settings.statement_timeout_ms,
        lock_timeout_ms=settings.lock_timeout_ms,
        device=settings.device,
        rerank_available=settings.rerank,
    )
    try:
        backend.open(wait_timeout=settings.connect_timeout_seconds)
        backend.check_schema()
    except Exception as error:
        backend.close()
        raise RAGUnavailableError(
            f"cannot use the retrieval database ({_describe_dsn(dsn)}): {error}. "
            "Start it with `docker compose -f rag/docker-compose.yml up -d` and run "
            "`python -m rag.cli migrate`."
        ) from error
    adapter = RAGAdapter(
        backend,
        role=settings.role,
        rerank=settings.rerank,
        filter_difficulty=settings.filter_difficulty,
        filter_competency=settings.filter_competency,
        drop_low_confidence=settings.drop_low_confidence,
        known_domains=backend.known_domains,
        on_warmup=backend.warmup,
        on_close=backend.close,
        **overrides,
    )
    if warmup:
        try:
            backend.warmup()
        except Exception as error:
            backend.close()
            raise RAGUnavailableError(f"retrieval warm-up failed: {error}") from error
    return adapter


def _describe_dsn(dsn: str) -> str:
    """host:port/dbname only; never print credentials."""
    try:
        from psycopg.conninfo import conninfo_to_dict

        info = conninfo_to_dict(dsn)
        return f"{info.get('host', '?')}:{info.get('port', '5432')}/{info.get('dbname', '?')}"
    except Exception:
        return "unparseable DSN"
