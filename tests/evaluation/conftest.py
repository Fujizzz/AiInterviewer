import json
from pathlib import Path

import pytest

from evaluation.inputs import EvaluationInput
from shared.contracts import CandidateAnswer, EvaluationRequest, PlannedQuestion
from shared.contracts.planning import TopicAllocation


@pytest.fixture
def example():
    return json.loads(
        Path(__file__)
        .with_name("fixtures")
        .joinpath("evidence_to_assessment.json")
        .read_text(encoding="utf-8")
    )


@pytest.fixture
def phase_two_request():
    question = PlannedQuestion(
        question_id="question-current",
        thread_id="thread-cache",
        project_id="project-cache",
        topic_key="cache-diagnosis",
        text="How did you diagnose contention and validate the fix?",
        information_goal="Personal diagnosis and measured result",
        difficulty=3,
        probe_depth=2,
        question_type="failure_analysis",
        intent="Clarify diagnosis",
    )
    return EvaluationRequest(
        request_id="request-current",
        interview_id="interview-cache",
        question=question,
        answer=CandidateAnswer(
            interview_id="interview-cache",
            question_id=question.question_id,
            answer_id="answer-current",
            text="我定位了锁竞争。团队开会。通过火焰图确认。修复后延迟降到10ms。",
        ),
    )


@pytest.fixture
def phase_two_topic(phase_two_request):
    return TopicAllocation(
        project_id=phase_two_request.question.project_id,
        topic_key=phase_two_request.question.topic_key,
        objective="Understand personal diagnosis and validation",
        completion_criteria="A concrete diagnosis method and a measured fix result",
        budget_seconds=120,
        expected_questions=2,
    )


@pytest.fixture
def phase_two_input(phase_two_request, phase_two_topic):
    return EvaluationInput.from_request(phase_two_request, topic=phase_two_topic)


@pytest.fixture
def analysis_payload():
    return dict(
        status="substantive",
        answer_scope="concrete",
        summary="The candidate diagnosed lock contention and measured the fix.",
        new_information=True,
        missing_information=[],
        uncertainties=[],
        contradiction_evidence=[],
        thread_complete=True,
    )


@pytest.fixture
def extraction_payload(phase_two_request):
    def spans(*quotes):
        text = phase_two_request.answer.text
        return [
            dict(quote=quote, char_start=text.index(quote), char_end=text.index(quote) + len(quote))
            for quote in quotes
        ]

    return dict(
        evidence=[
            dict(
                quote_spans=spans("我定位了锁竞争", "通过火焰图确认"),
                normalized_claim="我用火焰图定位了锁竞争。",
                evidence_kind="personal_action",
                ownership_scope="personal",
                factuality="reported_experience",
                specificity="concrete",
            ),
            dict(
                quote_spans=spans("修复后延迟降到10ms"),
                normalized_claim="修复后延迟降到10ms。",
                evidence_kind="outcome",
                ownership_scope="unclear",
                factuality="reported_experience",
                specificity="concrete",
            ),
        ]
    )
