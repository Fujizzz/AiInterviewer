"""Retrieval request construction and failure-tolerant parallel execution."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import uuid4

from agents.config import AgentSettings, load_agent_settings
from agents.domain.errors import RAGTimeout
from agents.domain.models import RetrievalBatch
from agents.ports import RAGPort
from agents.timeouts import call_with_timeout
from shared.contracts import (
    Competency,
    PlannedQuestion,
    RetrievalRequest,
    RetrievalResponse,
    RetrievalSource,
)

_DEFAULT_SOURCES: dict[Competency, tuple[RetrievalSource, ...]] = {
    Competency.TECHNICAL_DEPTH: (
        RetrievalSource.CANDIDATE,
        RetrievalSource.TECHNICAL,
        RetrievalSource.QUESTION,
    ),
    Competency.OWNERSHIP: (RetrievalSource.CANDIDATE,),
    Competency.DECISION_MAKING: (
        RetrievalSource.CANDIDATE,
        RetrievalSource.TECHNICAL,
        RetrievalSource.QUESTION,
    ),
    Competency.DEBUGGING: (
        RetrievalSource.CANDIDATE,
        RetrievalSource.TECHNICAL,
        RetrievalSource.QUESTION,
    ),
    Competency.EVALUATION: (RetrievalSource.CANDIDATE, RetrievalSource.QUESTION),
    Competency.ADAPTABILITY: (
        RetrievalSource.CANDIDATE,
        RetrievalSource.TECHNICAL,
        RetrievalSource.QUESTION,
    ),
}

_QUERY_SUFFIX: dict[Competency, str] = {
    Competency.TECHNICAL_DEPTH: "mechanism design tradeoffs",
    Competency.OWNERSHIP: "candidate claims personal implementation responsibility",
    Competency.DECISION_MAKING: "design alternatives decision tradeoffs",
    Competency.DEBUGGING: "failure modes root causes debugging signals",
    Competency.EVALUATION: "validation metrics experiment evidence",
    Competency.ADAPTABILITY: "scaling constraints trade-offs failure modes",
}


class RAGRouter:
    """Decide what to retrieve while leaving retrieval mechanics to RAGPort."""

    def __init__(self, rag: RAGPort, settings: AgentSettings | None = None) -> None:
        self._rag = rag
        self._settings = settings or load_agent_settings()

    def build_requests(
        self,
        question_plan: PlannedQuestion,
        *,
        interview_id: str,
        candidate_id: str | None = None,
        domain: str | None = None,
    ) -> list[RetrievalRequest]:
        configured_sources = question_plan.required_context_sources
        sources = tuple(configured_sources) or _DEFAULT_SOURCES[question_plan.target_competency]
        sources = tuple(dict.fromkeys(sources))
        return [
            RetrievalRequest(
                request_id=str(uuid4()),
                interview_id=interview_id,
                source=source,
                intent=question_plan.intent,
                competency=question_plan.target_competency,
                difficulty=question_plan.difficulty,
                candidate_id=candidate_id,
                project_id=question_plan.project_id,
                topic=question_plan.topic,
                domain=domain,
                query=self._query(question_plan, source),
                top_k=self._top_k(source),
            )
            for source in sources
        ]

    async def retrieve_all(
        self,
        requests: Sequence[RetrievalRequest],
    ) -> list[RetrievalResponse]:
        """Return successful responses; one source failure never fails the batch."""

        return (await self.retrieve_with_diagnostics(requests)).responses

    async def retrieve_with_diagnostics(
        self,
        requests: Sequence[RetrievalRequest],
    ) -> RetrievalBatch:
        results = await asyncio.gather(
            *(self._retrieve_one(request) for request in requests),
            return_exceptions=True,
        )
        failed_sources: list[RetrievalSource] = []
        timeout_sources: list[RetrievalSource] = []
        responses: list[RetrievalResponse] = []
        for request, result in zip(requests, results, strict=True):
            if isinstance(result, BaseException):
                failed_sources.append(request.source)
                if isinstance(result, RAGTimeout):
                    timeout_sources.append(request.source)
            else:
                responses.append(result)
        if failed_sources:
            responses = [response.model_copy(update={"partial": True}) for response in responses]
        return RetrievalBatch(
            responses=responses,
            failed_sources=failed_sources,
            timeout_sources=timeout_sources,
        )

    async def _retrieve_one(self, request: RetrievalRequest) -> RetrievalResponse:
        return await call_with_timeout(
            self._rag.retrieve(request),
            timeout_seconds=self._settings.timeouts.rag_seconds,
            error_factory=lambda: RAGTimeout(
                f"RAG source {request.source.value!r} timed out"
            ),
        )

    def _query(self, question_plan: PlannedQuestion, source: RetrievalSource) -> str:
        topic = question_plan.topic or "candidate project"
        suffix = _QUERY_SUFFIX[question_plan.target_competency]
        if source == RetrievalSource.CANDIDATE:
            return f"{topic} candidate project claims {question_plan.target_competency.value}"
        if source == RetrievalSource.QUESTION:
            return f"{topic} {question_plan.question_type.value} interview question examples"
        if source == RetrievalSource.JOB:
            return f"{topic} role requirements {question_plan.target_competency.value}"
        return f"{topic} {suffix}"

    def _top_k(self, source: RetrievalSource) -> int:
        limits = self._settings.retrieval
        return {
            RetrievalSource.CANDIDATE: limits.candidate_top_k,
            RetrievalSource.TECHNICAL: limits.technical_top_k,
            RetrievalSource.QUESTION: limits.question_top_k,
            RetrievalSource.JOB: limits.job_top_k,
        }[source]
