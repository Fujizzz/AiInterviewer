import pytest

from agents.ports import EvaluationPort
from shared.contracts import (
    CandidateAnswer,
    EvaluationRequest,
    PlannedQuestion,
    QuestionType,
)
from tests.agent.mocks import MockEvaluationAdapter


@pytest.mark.asyncio
async def test_evaluation_adapter_contract() -> None:
    adapter: EvaluationPort = MockEvaluationAdapter()
    question = PlannedQuestion(
        question_id="question-1",
        difficulty=2,
        probe_depth=1,
        question_type=QuestionType.FAILURE_ANALYSIS,
        intent="collect debugging evidence",
    )
    request = EvaluationRequest(
        request_id="evaluation-1",
        interview_id="interview-1",
        question=question,
        answer=CandidateAnswer(
            interview_id="interview-1",
            question_id=question.question_id,
            answer_id="answer-1",
            text="I inspected logs and isolated the failing component.",
        ),
    )

    response = await adapter.evaluate(request)

    assert response.contract_version == "2.0"
    assert response.request_id == request.request_id
    assert response.question_id == question.question_id
    assert response.analysis.status == "substantive"
    assert response.dimensions == []
