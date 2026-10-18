"""Fakes for the RAGPort adapter tests: no database, no model, no rag extra needed."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

from rag.retrieval.access import resolve_namespaces, scope_namespaces
from rag.retrieval.confidence import RetrievalResult
from rag.retrieval.intent import RetrievalIntent
from rag.retrieval.types import Passage
from shared.contracts import RetrievalRequest, RetrievalSource


def make_request(
    source: RetrievalSource = RetrievalSource.TECHNICAL,
    *,
    query: str = "PagedAttention kv cache",
    candidate_id: str | None = None,
    request_id: str = "req-1",
    **overrides,
) -> RetrievalRequest:
    fields = dict(
        request_id=request_id,
        interview_id="interview-1",
        source=source,
        intent="question_context",
        difficulty=3,
        query=query,
        candidate_id=candidate_id,
    )
    fields.update(overrides)
    return RetrievalRequest(**fields)


def make_passage(
    chunk_id: str = "vllm-1",
    *,
    text: str = "PagedAttention stores the KV cache in fixed size blocks.",
    namespace: str = "technical",
    candidate_id: str | None = None,
    rank: int = 1,
    scores: dict | None = None,
    **overrides,
) -> Passage:
    passage = Passage(
        chunk_id=chunk_id,
        text=text,
        title=overrides.pop("title", "Paged Attention"),
        heading_path=overrides.pop("heading_path", ["Design", "Paged Attention"]),
        source_url=f"https://example.org/{chunk_id}",
        doc_version="v1",
        namespace=namespace,
        source_name=overrides.pop("source_name", "vllm"),
        candidate_id=candidate_id,
        rank=rank,
        **overrides,
    )
    passage.scores.update(scores or {"dense": 0.71, "lexical": 2.5, "rrf": 0.22, "rerank": None})
    passage.stage_ranks = {"dense": rank, "lexical": rank}
    return passage


def make_result(passages: Sequence[Passage] = (), *, confidence: str = "high", **signals):
    return RetrievalResult(
        list(passages),
        max_dense=signals.pop("max_dense", 0.71),
        max_rerank=signals.pop("max_rerank", None),
        lexical_hit=signals.pop("lexical_hit", True),
        confidence=confidence,
        reasons=signals.pop("reasons", ["lexical match and strong dense"]),
    )


class FakeBackend:
    """Records every call; `handler(intent, role)` decides the result (default: one passage)."""

    def __init__(
        self,
        handler: Callable[[RetrievalIntent, str], RetrievalResult] | None = None,
        *,
        delay: float = 0.0,
    ) -> None:
        self._handler = handler or (lambda intent, role: make_result([make_passage()]))
        self._delay = delay
        self.calls: list[tuple[RetrievalIntent, str, dict]] = []
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.threads: set[int] = set()

    def retrieve(self, intent: RetrievalIntent, role: str, **kwargs) -> RetrievalResult:
        with self._lock:
            self.calls.append((intent, role, kwargs))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.threads.add(threading.get_ident())
        try:
            if self._delay:
                time.sleep(self._delay)  # deliberately blocking: must not block the event loop
            return self._handler(intent, role)
        finally:
            with self._lock:
                self.active -= 1


class ScopedCorpusBackend:
    """A tiny in-memory corpus that applies the same role/candidate scoping rules as SQL."""

    def __init__(self, passages: Sequence[Passage]) -> None:
        self._passages = list(passages)

    def retrieve(self, intent: RetrievalIntent, role: str, **kwargs) -> RetrievalResult:
        namespaces = scope_namespaces(
            resolve_namespaces(role, intent.namespaces), intent.candidate_id
        )
        hits = [
            p
            for p in self._passages
            if p.namespace in namespaces
            and (p.namespace != "candidate" or p.candidate_id == intent.candidate_id)
        ][: intent.k]
        return make_result(hits)
