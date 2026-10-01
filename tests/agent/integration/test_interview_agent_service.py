import pytest

from agents.orchestrator import InterviewAgentService
from shared.contracts import AnswerAnalysis, EvaluationFeedback, InterviewActionType
from tests.agent.factories import initialize_request
from tests.agent.mocks import (
    InMemoryRepository,
    MockEvaluationAdapter,
    MockLLMAdapter,
    MockRAGAdapter,
)


@pytest.mark.asyncio
async def test_service_initializes_with_mock_adapters() -> None:
    repository = InMemoryRepository()
    service = InterviewAgentService(
        repository=repository,
        rag=MockRAGAdapter(),
        evaluation=MockEvaluationAdapter(),
        llm=MockLLMAdapter(),
    )

    response = await service.initialize_interview(initialize_request())

    assert response.first_action.type == InterviewActionType.ASK_QUESTION
    assert response.first_action.question is not None
    assert response.state.question_index == 1
    assert response.state.state_version == 2
    assert len(repository.questions) == 1
    assert len(repository.decision_logs) == 1


@pytest.mark.asyncio
async def test_duplicate_feedback_does_not_advance_state_or_create_action_twice() -> None:
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    initialized = await service.initialize_interview(initialize_request())
    question = initialized.first_action.question
    assert question is not None
    feedback = EvaluationFeedback(
        request_id="feedback-1",
        question_id=question.question_id,
        answer_relevance=0.9,
        evidence_strength=0.9,
        analysis=AnswerAnalysis(status="partial", missing_information=["Give one example"]),
        evidence_ids=["evidence-1"],
    )

    first_action = await service.apply_evaluation_feedback(
        "interview-1",
        feedback,
        elapsed_seconds=120,
    )
    state_after_first = await repository.get_state("interview-1")
    question_count_after_first = len(repository.questions)
    log_count_after_first = len(repository.decision_logs)
    second_action = await service.apply_evaluation_feedback(
        "interview-1",
        feedback,
        elapsed_seconds=120,
    )
    state_after_second = await repository.get_state("interview-1")

    assert second_action == first_action
    assert state_after_second == state_after_first
    assert state_after_first.elapsed_seconds == 120
    assert state_after_first.remaining_seconds == 780
    assert len(repository.questions) == question_count_after_first
    assert len(repository.decision_logs) == log_count_after_first
