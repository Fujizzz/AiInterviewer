"""RAG boundary; retrieval internals remain outside the Agent."""

from typing import Protocol

from shared.contracts import RetrievalRequest, RetrievalResponse


class RAGPort(Protocol):
    async def retrieve(self, request: RetrievalRequest) -> RetrievalResponse: ...
