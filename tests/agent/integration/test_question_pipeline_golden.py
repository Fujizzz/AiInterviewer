"""Actual service scenario: question -> clarification -> targeted probe -> new topic."""

import pytest

from agents.orchestrator import InterviewAgentService
from shared.contracts import AnswerAnalysis, CandidateAnswer, EvaluationFeedback
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter


@pytest.mark.asyncio
async def test_dialogue_sequence_preserves_parent_and_topic_until_followup_limit():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository, llm=MockLLMAdapter())
    first = (await service.initialize_interview(pipeline_request())).first_action.question
    questions = [first]
    statuses = [
        ("partial", "Which component", "I used it"),
        ("substantive", "Which baseline", "I implemented the cache"),
        ("partial", "Which metric", "Some numbers"),
    ]
    for index, (status, missing, text) in enumerate(statuses):
        current = questions[-1]
        answer = CandidateAnswer(
            interview_id="pipeline-interview",
            question_id=current.question_id,
            answer_id=str(index),
            text=text,
        )
        action = await service.apply_evaluation_feedback(
            "pipeline-interview",
            EvaluationFeedback(
                request_id=str(index),
                question_id=current.question_id,
                answer_relevance=0.8,
                evidence_strength=0.7,
                analysis=AnswerAnalysis(
                    status=status, missing_information=[missing], new_information=True
                ),
            ),
            answer=answer,
            elapsed_seconds=120,
        )
        questions.append(action.question)
    assert [q.dialogue_action for q in questions] == ["new_topic", "clarify", "probe", "new_topic"]
    assert [q.probe_depth for q in questions] == [1, 2, 3, 1]
    for index in (1, 2):
        assert questions[index].parent_question_id == questions[index - 1].question_id
        assert questions[index].thread_id == first.thread_id
        assert questions[index].topic_key == first.topic_key
    assert questions[3].parent_question_id is None
    assert questions[3].topic_key != first.topic_key
    stored = await repository.get_interview_context("pipeline-interview")
    assert stored.closed_threads[0].follow_up_count == 2
    assert all("target_competency" not in q.model_dump() for q in questions)
