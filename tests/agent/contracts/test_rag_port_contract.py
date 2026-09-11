import pytest

from agents.ports import RAGPort
from shared.contracts import Competency, RetrievalRequest, RetrievalSource
from tests.agent.mocks import MockRAGAdapter


@pytest.mark.asyncio
async def test_rag_adapter_contract() -> None:
    adapter: RAGPort = MockRAGAdapter()
    request = RetrievalRequest(
        request_id="retrieval-1",
        interview_id="interview-1",
        source=RetrievalSource.CANDIDATE,
        intent="question_context",
        competency=Competency.OWNERSHIP,
        difficulty=2,
        query="candidate ownership",
    )

    response = await adapter.retrieve(request)

    assert response.contract_version == "1.0"
    assert response.request_id == request.request_id
    assert isinstance(response.chunks, list)
