"""Exercise new failure results with the existing atomic feedback/recovery path.

Only the safe failed analysis is mapped here; production port integration is phase five.
"""

import pytest

from app.providers.llm import LLMError
from evaluation.analyzer import ConversationAnalysis
from evaluation.inputs import EvaluationInput
from evaluation.service import EvaluationService
from shared.contracts import CandidateAnswer, EvaluationFeedback, EvaluationRequest
from tests.agent.integration.test_interview_planning import PlannerLLM, setup


@pytest.mark.parametrize("failure", ["analyzer", "extractor", "quote"])
async def test_failed_phase_two_result_preserves_answer_and_keeps_planner_topic_open(
    analysis_payload, failure
):
    agent, repository, clock, initialized = await setup(PlannerLLM())
    question = initialized.first_action.question
    context = await repository.get_interview_context(initialized.interview_id)
    topic = next(t for t in context.plan.topics if t.topic_key == question.topic_key)
    answer = CandidateAnswer(
        interview_id=context.interview_id,
        question_id=question.question_id,
        answer_id="phase-two-failure-answer",
        text="I measured cache misses using a profiler.",
    )
    request = EvaluationRequest(
        request_id="phase-two-failure-request",
        interview_id=context.interview_id,
        question=question,
        answer=answer,
    )

    def model(prompt, data, schema):
        current = "analyzer" if schema is ConversationAnalysis else "extractor"
        if current == failure:
            raise LLMError("Unavailable", code="invalid_json")
        if current == "analyzer":
            return schema(**analysis_payload)
        return schema(
            evidence=[
                dict(
                    quote_spans=[dict(quote="invented", char_start=0, char_end=8)],
                    normalized_claim="Invented diagnosis",
                    evidence_kind="personal_action",
                    ownership_scope="personal",
                    factuality="reported_experience",
                    specificity="concrete",
                )
            ]
        )

    result = await EvaluationService(model).evaluate(
        EvaluationInput.from_request(request, topic=topic)
    )
    assert result.status == "failed"
    feedback = EvaluationFeedback(
        request_id=request.request_id,
        question_id=question.question_id,
        answer_relevance=0,
        evidence_strength=0,
        analysis=result.analysis,
    )
    clock.value += 10
    next_action = await agent.apply_evaluation_feedback(
        context.interview_id, feedback, answer=answer
    )
    persisted = await repository.get_interview_context(context.interview_id)
    assert next_action.question is not None
    assert next_action.question.thread_id == question.thread_id
    assert persisted.topic_progress[question.topic_key].status != "completed"
    assert any(turn.answer == answer for turn in persisted.question_history)
    assert not persisted.evidence_records
    assert all(item.score is None for item in persisted.state.competencies.values())
    saved = persisted.model_dump()
    replay = await agent.apply_evaluation_feedback(context.interview_id, feedback, answer=answer)
    assert replay.action_id == next_action.action_id
    assert (await repository.get_interview_context(context.interview_id)).model_dump() == saved
