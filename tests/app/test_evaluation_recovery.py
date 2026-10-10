"""Independent conversation/assessment recovery and conservative evidence merging."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents.orchestrator import InterviewAgentService
from app.adapters.evaluation import AnswerEvidence, LLMEvaluationAdapter, grounded_dimensions
from app.providers.llm import LLMError, OpenAILLM
from shared.contracts import CandidateAnswer, EvaluationRequest
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository
from tests.app.test_qwen_pdf import response


def output(duplicate=False, quote="I implemented the cache"):
    item = dict(
        competency="technical_depth",
        observation="supported",
        quote=quote,
        fact="Implemented a cache",
        rationale="Concrete implementation",
        strength=0.7,
        rubric_level=3,
    )
    return dict(
        answer_relevance=0.8,
        evidence_strength=0.7,
        analysis=dict(status="substantive", answer_scope="concrete"),
        dimensions=[item, item.copy()] if duplicate else [item],
    )


def test_duplicate_dimension_merges_once_without_provider_repair_or_score_inflation():
    model = OpenAILLM.__new__(OpenAILLM)
    model.provider, model.model, model.options = "dashscope", "fake", {}
    model.request_timeout = 30
    model.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=Mock(
                    side_effect=[response(json.dumps(output(True))), response(json.dumps(output()))]
                )
            )
        )
    )
    result = model("Evaluate", {}, AnswerEvidence)
    dimensions = grounded_dimensions(result.dimensions, "I implemented the cache", [])
    assert len(dimensions) == 1
    assert dimensions[0].rubric_level == 3
    assert dimensions[0].strength == 0.7
    create = model.client.chat.completions.create
    assert create.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["bad_structure", "bad_quote", "provider", "timeout", "system"])
async def test_failed_evaluation_preserves_answer_without_scores_and_continues(failure):
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    question = (await service.initialize_interview(pipeline_request())).first_action.question
    answer = CandidateAnswer(
        interview_id="pipeline-interview",
        question_id=question.question_id,
        answer_id="answer-recovery",
        text="I implemented the cache",
    )

    def model(prompt, data, schema):
        if failure == "system":
            raise RuntimeError("analysis implementation error")
        if failure == "provider":
            raise LLMError("invalid", code="invalid_json")
        if failure == "timeout":
            raise TimeoutError()
        raw = output(quote=("invented quote" if failure == "bad_quote" else answer.text))
        if failure == "bad_structure":
            raw["dimensions"][0]["rubric_level"] = 9
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(model, repository).evaluate(
        EvaluationRequest(
            request_id="recovery",
            interview_id=answer.interview_id,
            question=question,
            answer=answer,
        )
    )
    assert feedback.dimensions == []
    assert feedback.evidence_ids == []
    assert feedback.evidence_strength == 0
    assert not feedback.analysis.thread_complete
    assert feedback.assessment_status == "unavailable"
    if failure in {"bad_structure", "bad_quote"}:
        assert feedback.analysis_status == "valid"
        assert feedback.analysis.status == "substantive"
    else:
        assert feedback.analysis_status == "unavailable"
        assert "unassessed" in feedback.analysis.summary
    action = await service.apply_evaluation_feedback(answer.interview_id, feedback, answer=answer)
    assert action.question is not None
    context = await repository.get_interview_context(answer.interview_id)
    assert any(entry.answer == answer for entry in context.question_history)
    assert all(value.score is None for value in context.state.competencies.values())


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_feedback():
    class Repo:
        async def get_interview_context(self, _):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await LLMEvaluationAdapter(None, Repo()).evaluate(SimpleNamespace(interview_id="cancel"))
