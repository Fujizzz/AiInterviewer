"""Deterministic, instrumented RAGPort adapter."""

import asyncio
from collections.abc import Mapping, Sequence

from shared.contracts import RetrievalRequest, RetrievalResponse, RetrievalSource, RetrievedChunk


class MockRAGAdapter:
    def __init__(
        self,
        chunks_by_source: Mapping[RetrievalSource, Sequence[RetrievedChunk]] | None = None,
        *,
        delay_seconds: float = 0.0,
        delays_by_source: Mapping[RetrievalSource, float] | None = None,
        failing_sources: Sequence[RetrievalSource] = (),
    ) -> None:
        self._chunks_by_source = chunks_by_source
        self._delay_seconds = delay_seconds
        self._delays_by_source = dict(delays_by_source or {})
        self._failing_sources = frozenset(failing_sources)
        self.requests: list[RetrievalRequest] = []
        self.active_calls = 0
        self.max_concurrent_calls = 0

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        self.requests.append(request.model_copy(deep=True))
        self.active_calls += 1
        self.max_concurrent_calls = max(self.max_concurrent_calls, self.active_calls)
        try:
            delay_seconds = self._delays_by_source.get(request.source, self._delay_seconds)
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            if request.source in self._failing_sources:
                raise TimeoutError(f"Mock {request.source.value} retrieval timed out")
            chunks = self._chunks(request)
            return RetrievalResponse(
                request_id=request.request_id,
                chunks=chunks,
                latency_ms=round(delay_seconds * 1000),
            )
        finally:
            self.active_calls -= 1

    def _chunks(self, request: RetrievalRequest) -> list[RetrievedChunk]:
        if self._chunks_by_source is not None:
            return [
                chunk.model_copy(deep=True)
                for chunk in self._chunks_by_source.get(request.source, ())
            ]
        topic = request.topic or "candidate project"
        content = {
            RetrievalSource.CANDIDATE: (
                f"Candidate context for {topic}: project claims and implementation details."
            ),
            RetrievalSource.TECHNICAL: self._technical_content(topic),
            RetrievalSource.QUESTION: (
                f"Question example for {topic}: ask for concrete evidence without "
                "revealing a rubric."
            ),
            RetrievalSource.JOB: (
                f"Role context relevant to {topic} and {request.competency.value}."
            ),
        }[request.source]
        return [
            RetrievedChunk(
                chunk_id=f"mock-{request.source.value}-{request.request_id}",
                source=request.source,
                title=f"Mock {request.source.value} context",
                content=content,
                metadata={"topic": topic, "competency": request.competency.value},
                retrieval_score=1.0,
            )
        ]

    @staticmethod
    def _technical_content(topic: str) -> str:
        normalized = topic.casefold()
        if "gpu" in normalized or "memory" in normalized:
            return (
                "GPU memory failures can involve OOM, fragmentation, retained allocations, "
                "and low utilization; useful signals include allocation traces and memory profiles."
            )
        return f"Technical mechanisms, trade-offs, constraints, and failure modes for {topic}."
