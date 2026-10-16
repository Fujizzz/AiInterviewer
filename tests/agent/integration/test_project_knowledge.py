"""A RAG question is published once per project, through the real turn transaction."""

from copy import deepcopy

import pytest

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from shared.contracts import CandidateAnswer, QuestionType, RetrievalSource, RetrievedChunk
from tests.agent.integration.test_phase3_hardening import strong_feedback
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter, MockRAGAdapter


class KnowledgeLLM(MockLLMAdapter):
    def __init__(self, *, fail_knowledge=False):
        super().__init__()
        self.fail_knowledge = fail_knowledge
        self.knowledge_inputs = []
        self.reviews = []

    async def generate_structured(self, *, prompt_name, payload, response_model):
        if prompt_name == "question_quality_v1":
            self.reviews.append(deepcopy(payload))
        if prompt_name == "question_react_v1" and payload.get("retrieved_evidence"):
            self.knowledge_inputs.append(deepcopy(payload))
            if self.fail_knowledge:
                raise RuntimeError("knowledge generation unavailable")
            return response_model(
                action="final",
                text="How does PagedAttention reduce KV cache memory fragmentation?",
                intent_id=payload["locked_intent"]["intent_id"],
            )
        return await super().generate_structured(
            prompt_name=prompt_name, payload=payload, response_model=response_model
        )


def evidence(**metadata):
    return RetrievedChunk(
        chunk_id="vllm-paged-attention",
        source=RetrievalSource.TECHNICAL,
        title="PagedAttention",
        content="PagedAttention stores KV cache in blocks and maps logical to physical blocks.",
        metadata={"source_url": "https://example.test/vllm", **metadata},
    )


async def answer(service, question, request_id="answer-1", status="substantive"):
    feedback = strong_feedback(question.question_id, question.difficulty, request_id=request_id)
    feedback.analysis.status = status
    return await service.apply_evaluation_feedback(
        "pipeline-interview",
        feedback,
        answer=CandidateAnswer(
            interview_id="pipeline-interview",
            question_id=question.question_id,
            answer_id=request_id,
            text="I measured memory use and implemented the cache allocation component.",
        ),
    )


@pytest.mark.asyncio
async def test_cold_rag_does_not_block_or_consume_project_attempt_then_joins_when_ready():
    class LoadingRAG(MockRAGAdapter):
        is_ready = False

    repository = InMemoryRepository()
    rag = LoadingRAG(chunks_by_source={RetrievalSource.TECHNICAL: [evidence()]})
    llm = KnowledgeLLM()
    service = InterviewAgentService(repository=repository, rag=rag, llm=llm)
    first = (await service.initialize_interview(pipeline_request())).first_action.question
    second = await answer(service, first)
    saved = await repository.get_interview_context("pipeline-interview")
    assert saved.project_knowledge == {}
    assert rag.requests == []
    assert second.question is not None
    rag.is_ready = True
    third = await answer(service, second.question, "answer-2")
    saved = await repository.get_interview_context("pipeline-interview")
    assert third.question.text == "How does PagedAttention reduce KV cache memory fragmentation?"
    assert saved.project_knowledge[first.project_id].status == "asked"
    assert len(rag.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("planning", [False, True])
async def test_rag_question_uses_evidence_and_survives_restart_and_feedback_replay(planning):
    repository, llm = InMemoryRepository(), KnowledgeLLM()
    rag = MockRAGAdapter({RetrievalSource.TECHNICAL: [evidence()]})
    service = InterviewAgentService(repository=repository, rag=rag, llm=llm)
    request = pipeline_request()
    request.planning_enabled = planning
    first = (await service.initialize_interview(request)).first_action.question
    assert rag.requests == []

    action = await answer(service, first)
    question = action.question
    assert question.question_type == QuestionType.MECHANISM
    assert question.project_id == first.project_id
    assert question.topic_key == first.topic_key
    assert question.thread_id == first.thread_id
    assert question.required_context_sources == [RetrievalSource.TECHNICAL]
    assert len(rag.requests) == 1
    assert rag.requests[0].top_k == 3
    assert "PyTorch" in rag.requests[0].query
    payload = llm.knowledge_inputs[0]
    assert payload["question_purpose"] == "technical_knowledge"
    assert payload["retrieved_evidence"][0]["text"] == evidence().content
    assert payload["followup_brief"] is None
    assert "dialogue_state" not in payload
    review = next(r for r in llm.reviews if r["question_purpose"] == "technical_knowledge")
    assert any(s.get("source_kind") == "technical_reference" for s in review["grounding_sources"])
    saved = await repository.get_interview_context("pipeline-interview")
    attempt = saved.project_knowledge[first.project_id]
    assert attempt.status == "asked"
    assert attempt.question_id == question.question_id
    assert attempt.chunk_ids == [evidence().chunk_id]
    assert attempt.source_urls == ["https://example.test/vllm"]
    assert saved.state.question_index == 2

    restarted = InterviewAgentService(repository=repository, rag=rag, llm=KnowledgeLLM())
    replay = await answer(restarted, first)
    assert replay.action_id == action.action_id
    following = await answer(restarted, question, request_id="answer-2")
    assert following.question is not None
    assert not following.question.need_id.startswith("knowledge:")
    assert len(rag.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "low", "timeout", "generation"])
async def test_rag_failure_returns_to_normal_questions_without_retrying_project(failure):
    repository = InMemoryRepository()
    llm = KnowledgeLLM(fail_knowledge=failure == "generation")
    chunks = [] if failure == "empty" else [evidence(low_confidence=failure == "low")]
    rag = MockRAGAdapter(
        {RetrievalSource.TECHNICAL: chunks},
        delay_seconds=0.05 if failure == "timeout" else 0,
    )
    settings = load_agent_settings()
    settings.timeouts.rag_seconds = 0.01
    service = InterviewAgentService(repository=repository, rag=rag, llm=llm, settings=settings)
    first = (await service.initialize_interview(pipeline_request())).first_action.question
    second = (await answer(service, first)).question
    assert second is not None
    assert not second.need_id.startswith("knowledge:")
    saved = await repository.get_interview_context("pipeline-interview")
    attempt = saved.project_knowledge[first.project_id]
    assert attempt.status in {"retrieval_failed", "no_usable_evidence", "generation_failed"}
    assert attempt.question_id is None
    await answer(service, second, request_id="answer-2")
    assert len(rag.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("block", ["explicit_unknown", "refusal", "topic_cap", "project_cap"])
async def test_rag_does_not_bypass_candidate_stop_or_question_limits(block):
    repository, rag = InMemoryRepository(), MockRAGAdapter()
    settings = load_agent_settings()
    if block == "topic_cap":
        settings.max_questions_per_topic = 1
    if block == "project_cap":
        settings.max_questions_per_project = 1
    service = InterviewAgentService(
        repository=repository, rag=rag, llm=KnowledgeLLM(), settings=settings
    )
    first = (await service.initialize_interview(pipeline_request())).first_action.question
    await answer(
        service, first, status=block if block in {"explicit_unknown", "refusal"} else "substantive"
    )
    assert rag.requests == []


@pytest.mark.asyncio
async def test_explicit_finish_never_starts_rag_question():
    repository, rag = InMemoryRepository(), MockRAGAdapter()
    service = InterviewAgentService(repository=repository, rag=rag, llm=KnowledgeLLM())
    await service.initialize_interview(pipeline_request())
    await service.finish_interview("pipeline-interview")
    assert rag.requests == []


@pytest.mark.asyncio
async def test_failed_turn_does_not_publish_knowledge_progress_or_question():
    from agents.domain.errors import RepositoryUnavailable

    class RejectCommit(InMemoryRepository):
        fail = False

        async def commit_turn(self, request):
            if self.fail:
                raise RepositoryUnavailable("database write failed")
            return await super().commit_turn(request)

    repository, rag = RejectCommit(), MockRAGAdapter()
    service = InterviewAgentService(repository=repository, rag=rag, llm=KnowledgeLLM())
    first = (await service.initialize_interview(pipeline_request())).first_action.question
    repository.fail = True
    with pytest.raises(RepositoryUnavailable):
        await answer(service, first)
    saved = await repository.get_interview_context("pipeline-interview")
    assert saved.project_knowledge == {}
    assert saved.state.question_index == 1
    assert len(repository.questions) == 1


@pytest.mark.asyncio
async def test_expired_topic_allocation_discards_draft_and_keeps_normal_routing():
    clock = [100.0]

    class SlowKnowledgeLLM(KnowledgeLLM):
        delay = 0

        async def generate_structured(self, **kwargs):
            result = await super().generate_structured(**kwargs)
            if kwargs["payload"].get("retrieved_evidence"):
                clock[0] += self.delay
            return result

    repository, rag, llm = InMemoryRepository(), MockRAGAdapter(), SlowKnowledgeLLM()
    service = InterviewAgentService(repository=repository, rag=rag, llm=llm, clock=lambda: clock[0])
    request = pipeline_request()
    request.planning_enabled = True
    first = (await service.initialize_interview(request)).first_action.question
    saved = await repository.get_interview_context(request.interview_id)
    allocation = next(t for t in saved.plan.topics if t.topic_key == first.topic_key)
    llm.delay = allocation.budget_seconds + 1
    action = await answer(service, first)
    saved = await repository.get_interview_context(request.interview_id)
    assert saved.project_knowledge[first.project_id].status == "budget_exhausted"
    assert saved.project_knowledge[first.project_id].question_id is None
    assert action.question.topic_key != first.topic_key
