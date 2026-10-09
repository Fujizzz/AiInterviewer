"""Narrative cannot resurrect unassessed or superseded statements as findings."""

import pytest

from agents.domain.models import EvidenceRecord, InterviewHistoryEntry
from agents.orchestrator import InterviewAgentService
from app.reporting.final_report import build_final_report
from shared.contracts import AnswerAnalysis, CandidateAnswer, EvaluationFeedback
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository
from tests.app.test_dialogue_evaluation import dimension


@pytest.mark.asyncio
async def test_report_preserves_correction_and_rejects_narrator_reassessment():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    question = (await service.initialize_interview(pipeline_request())).first_action.question
    context = await repository.get_interview_context("pipeline-interview")
    earlier = CandidateAnswer(
        interview_id=context.interview_id,
        question_id=question.question_id,
        answer_id="old",
        text="We did not deduplicate requests.",
    )
    current = earlier.model_copy(
        update={
            "answer_id": "correction",
            "text": "I correct that: request IDs prevented duplicates.",
        }
    )
    old_evidence = dimension("technical_depth", earlier.text)
    old_evidence.observation = "weak"
    context.question_history = [
        InterviewHistoryEntry(
            question=question,
            answer=earlier,
            feedback=EvaluationFeedback(
                request_id="old-feedback",
                question_id=question.question_id,
                answer_relevance=1,
                evidence_strength=0.7,
                dimensions=[old_evidence],
            ),
        ),
        InterviewHistoryEntry(
            question=question,
            answer=current,
            feedback=EvaluationFeedback(
                request_id="new-feedback",
                question_id=question.question_id,
                answer_relevance=1,
                evidence_strength=0,
                analysis=AnswerAnalysis(
                    answer_relations=[
                        {
                            "kind": "supersedes",
                            "earlier_answer_id": earlier.answer_id,
                            "earlier_quote": earlier.text,
                            "current_quote": current.text,
                            "explanation": "Explicit self-correction.",
                        }
                    ]
                ),
            ),
        ),
    ]
    context.evidence_records = [
        EvidenceRecord(
            project_id=question.project_id,
            thread_id=question.thread_id,
            question_id=question.question_id,
            answer_id=earlier.answer_id,
            difficulty=2,
            evidence=old_evidence,
        )
    ]

    def model(prompt, data, schema):
        assert "question_history" not in data
        assert earlier.text not in str(data["permitted_weaknesses"])
        assert data["corrections"][0]["independently_verified"] is False
        return schema(strengths=["Good engineer"], weaknesses=["Failed to deduplicate requests"])

    report = await build_final_report(
        context, [{"answer": "untrusted extra transcript"}], llm=model
    )
    assert report.corrections[0]["current_answer_id"] == "correction"
    assert "not independently verified" in report.summary
    assert all("deduplicate" not in statement for statement in report.weaknesses)
    assert "Good engineer" not in report.strengths


@pytest.mark.asyncio
async def test_unavailable_assessment_has_no_negative_ability_conclusion():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    question = (await service.initialize_interview(pipeline_request())).first_action.question
    context = await repository.get_interview_context("pipeline-interview")
    answer = CandidateAnswer(
        interview_id=context.interview_id,
        question_id=question.question_id,
        answer_id="unassessed",
        text="I diagnosed the deadlock using thread dumps.",
    )
    evidence = dimension("debugging", answer.text)
    evidence.observation = "weak"
    context.question_history = [
        InterviewHistoryEntry(
            question=question,
            answer=answer,
            feedback=EvaluationFeedback(
                request_id="feedback",
                question_id=question.question_id,
                answer_relevance=0,
                evidence_strength=0,
                assessment_status="unavailable",
                analysis_status="valid",
                dimensions=[],
            ),
        )
    ]
    # Corrupt/imported old evidence must not bypass canonical assessment status.
    context.evidence_records = [
        EvidenceRecord(
            project_id=question.project_id,
            thread_id=question.thread_id,
            question_id=question.question_id,
            answer_id=answer.answer_id,
            difficulty=2,
            evidence=evidence,
        )
    ]
    report = await build_final_report(context, [])
    assert report.unassessed_answer_ids == ["unassessed"]
    assert report.overall_score is None
    assert any("not evidence of a candidate weakness" in line for line in report.weaknesses)
    assert all(answer.text not in line for line in report.weaknesses + report.strengths)


@pytest.mark.asyncio
async def test_grounded_correction_ledger_survives_short_history_window():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    await service.initialize_interview(pipeline_request())
    context = await repository.get_interview_context("pipeline-interview")
    context.question_history = []
    context.unassessed_answer_ids = ["unassessed-before-window"]
    context.answer_relations = [
        {
            "relation_id": "relation-retained",
            "kind": "supersedes",
            "earlier_answer_id": "old",
            "current_answer_id": "new",
            "earlier_quote": "The queue was unbounded.",
            "current_quote": "I correct that: the queue was bounded.",
            "explanation": "Explicit correction",
            "project_id": "project",
            "validation_status": "grounded",
        }
    ]
    report = await build_final_report(context, [])
    assert report.corrections[0]["relation_id"] == "relation-retained"
    assert report.corrections[0]["independently_verified"] is False
    assert report.unassessed_answer_ids == ["unassessed-before-window"]
