"""End-to-end evidence extraction and atomic multi-dimensional aggregation."""

import json

import pytest

from agents.domain.errors import InvalidAgentState
from agents.orchestrator import InterviewAgentService
from app.adapters.evaluation import LLMEvaluationAdapter
from app.reporting.final_report import build_final_report
from shared.contracts import (
    AnswerAnalysis,
    CandidateAnswer,
    Competency,
    DimensionEvidence,
    EvaluationFeedback,
    EvaluationRequest,
)
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter


def dimension(name, quote, strength=0.7, level=3):
    return DimensionEvidence(
        competency=name,
        observation="supported",
        quote=quote,
        fact="Implemented and measured the cache",
        rationale="Concrete current-answer evidence",
        rubric_level=level,
        strength=strength,
    )


@pytest.mark.asyncio
async def test_multiple_dimensions_deduplicate_followups_and_remain_out_of_question_payload():
    repository = InMemoryRepository()
    llm = MockLLMAdapter()
    service = InterviewAgentService(repository=repository, llm=llm)
    question = (await service.initialize_interview(pipeline_request())).first_action.question
    for index in range(2):
        answer = CandidateAnswer(
            interview_id="pipeline-interview",
            question_id=question.question_id,
            answer_id=f"a{index}",
            text="I implemented the cache and measured latency at 10 ms.",
        )
        feedback = EvaluationFeedback(
            request_id=f"r{index}",
            question_id=question.question_id,
            answer_relevance=0.9,
            evidence_strength=0.8,
            analysis=AnswerAnalysis(
                status="substantive",
                new_information=index == 0,
                missing_information=["Which baseline"] if index == 0 else [],
            ),
            dimensions=[
                dimension(Competency.OWNERSHIP, "I implemented the cache", 0.7 + index * 0.1),
                dimension(Competency.EVALUATION, "measured latency at 10 ms.", 0.7 + index * 0.1),
            ],
        )
        action = await service.apply_evaluation_feedback(
            "pipeline-interview", feedback, answer=answer, elapsed_seconds=120
        )
        question = action.question
    context = await repository.get_interview_context("pipeline-interview")
    for name in (Competency.OWNERSHIP, Competency.EVALUATION):
        assert context.state.competencies[name].score == 3
        assert context.state.competencies[name].evidence_count == 1
    assert len(context.evidence_records) == 2
    assert context.state.competencies[Competency.DEBUGGING].score is None
    report = await build_final_report(context, [])
    assert report.overall_score == pytest.approx(3)
    assert "confidence" not in report.model_dump_json()
    payload = json.dumps(llm.calls[-1][1])
    for forbidden in (
        "target_competency",
        "competencies",
        "rubric_level",
        "confidence",
        "dimensions",
    ):
        assert forbidden not in payload


@pytest.mark.asyncio
async def test_bad_quote_cannot_mutate_persisted_state():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    question = (await service.initialize_interview(pipeline_request())).first_action.question
    before = await repository.get_interview_context("pipeline-interview")
    answer = CandidateAnswer(
        interview_id="pipeline-interview",
        question_id=question.question_id,
        answer_id="a",
        text="I used the library.",
    )
    feedback = EvaluationFeedback(
        request_id="r",
        question_id=question.question_id,
        answer_relevance=0.8,
        evidence_strength=0.8,
        analysis=AnswerAnalysis(status="substantive"),
        dimensions=[dimension(Competency.DEBUGGING, "I debugged CUDA kernels")],
    )
    with pytest.raises(InvalidAgentState):
        await service.apply_evaluation_feedback("pipeline-interview", feedback, answer=answer)
    assert await repository.get_interview_context("pipeline-interview") == before


@pytest.mark.asyncio
async def test_yes_is_not_evidence_even_if_model_awards_a_score():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    question = (await service.initialize_interview(pipeline_request())).first_action.question

    def model(prompt, data, schema):
        return schema(
            answer_relevance=0.9,
            evidence_strength=0.9,
            analysis=AnswerAnalysis(status="substantive", new_information=True),
            dimensions=[dimension(Competency.OWNERSHIP, "yes")],
        )

    answer = CandidateAnswer(
        interview_id="pipeline-interview",
        question_id=question.question_id,
        answer_id="a",
        text="yes",
    )
    feedback = await LLMEvaluationAdapter(model, repository).evaluate(
        EvaluationRequest(
            request_id="r", interview_id="pipeline-interview", question=question, answer=answer
        )
    )
    assert feedback.dimensions == []
    assert feedback.analysis.status == "non_answer"
    following = await service.apply_evaluation_feedback(
        "pipeline-interview", feedback, answer=answer
    )
    assert following.question.dialogue_action == "clarify"
    assert following.question.parent_question_id == question.question_id
    context = await repository.get_interview_context("pipeline-interview")
    assert all(state.score is None for state in context.state.competencies.values())


@pytest.mark.asyncio
async def test_explicit_unknown_switches_project_and_survives_restart():
    from shared.contracts import CandidateProject

    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    request = pipeline_request()
    request.candidate_profile.projects.append(
        CandidateProject(project_id="logs", name="Log parser", technologies=["Python"])
    )
    question = (await service.initialize_interview(request)).first_action.question
    next_action = await InterviewAgentService(repository=repository).apply_evaluation_feedback(
        request.interview_id,
        EvaluationFeedback(
            request_id="r",
            question_id=question.question_id,
            answer_relevance=0,
            evidence_strength=0,
            analysis=AnswerAnalysis(status="explicit_unknown"),
        ),
    )
    assert next_action.question.dialogue_action == "new_project"
    assert next_action.question.project_id == "logs"
    assert next_action.question.parent_question_id is None


@pytest.mark.asyncio
async def test_bare_task_label_does_not_score_or_complete_thread():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    question = (await service.initialize_interview(pipeline_request())).first_action.question

    def model(prompt, data, schema):
        return schema(
            answer_relevance=0.9,
            evidence_strength=0.9,
            analysis=AnswerAnalysis(
                status="substantive", new_information=True, thread_complete=True
            ),
            dimensions=[dimension(Competency.TECHNICAL_DEPTH, "background", level=2)],
        )

    answer = CandidateAnswer(
        interview_id="pipeline-interview",
        question_id=question.question_id,
        answer_id="label-answer",
        text="background",
    )
    feedback = await LLMEvaluationAdapter(model, repository).evaluate(
        EvaluationRequest(
            request_id="label-feedback",
            interview_id="pipeline-interview",
            question=question,
            answer=answer,
        )
    )
    assert feedback.analysis.status == "substantive"
    assert feedback.analysis.answer_scope == "label_only"
    assert not feedback.analysis.thread_complete
    assert feedback.dimensions == []
    next_action = await service.apply_evaluation_feedback(
        "pipeline-interview", feedback, answer=answer
    )
    assert next_action.question.dialogue_action == "probe"
    assert next_action.question.thread_id == question.thread_id


@pytest.mark.asyncio
@pytest.mark.parametrize("grounded", [False, True])
async def test_contradictions_require_two_grounded_answer_quotes(grounded):
    from agents.domain.models import InterviewHistoryEntry

    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    question = (await service.initialize_interview(pipeline_request())).first_action.question
    context = repository.contexts["pipeline-interview"]
    earlier = "For this model, I did not modify the weights."
    current = "For this same model, I modified the weights."
    context.question_history = [
        InterviewHistoryEntry(
            question=question,
            answer=CandidateAnswer(
                interview_id=context.interview_id,
                question_id=question.question_id,
                answer_id="previous",
                text=earlier,
            ),
            feedback=EvaluationFeedback(
                request_id="previous-feedback",
                question_id=question.question_id,
                answer_relevance=1,
                evidence_strength=0,
            ),
        )
    ]

    def model(prompt, data, schema):
        return schema(
            answer_relevance=1,
            evidence_strength=0,
            dimensions=[],
            analysis=AnswerAnalysis(
                status="substantive",
                answer_scope="concrete",
                contradictions=["The model says these accounts differ"],
                contradiction_evidence=[
                    dict(
                        earlier_answer_id="previous",
                        earlier_quote=earlier
                        if grounded
                        else "The question mentioned a Transformer",
                        current_quote=current,
                        explanation="Different claims about weight changes.",
                    )
                ],
            ),
        )

    answer = CandidateAnswer(
        interview_id=context.interview_id,
        question_id=question.question_id,
        answer_id="current",
        text=current,
    )
    feedback = await LLMEvaluationAdapter(model, repository).evaluate(
        EvaluationRequest(
            request_id="r", interview_id=context.interview_id, question=question, answer=answer
        )
    )
    assert bool(feedback.analysis.contradictions) == grounded
    assert bool(feedback.analysis.contradiction_evidence) == grounded
    assert bool(feedback.analysis.uncertainties) != grounded


@pytest.mark.asyncio
async def test_current_thread_evaluation_does_not_revive_old_vit_answer():

    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    first = (await service.initialize_interview(pipeline_request())).first_action.question
    # Close the first thread while retaining its answer in the same project.
    answer = CandidateAnswer(
        interview_id="pipeline-interview",
        question_id=first.question_id,
        answer_id="old-vit-answer",
        text="vit",
    )
    action = await service.apply_evaluation_feedback(
        "pipeline-interview",
        EvaluationFeedback(
            request_id="close-old",
            question_id=first.question_id,
            answer_relevance=1,
            evidence_strength=0,
            analysis=AnswerAnalysis(status="substantive", thread_complete=True),
        ),
        answer=answer,
    )
    current = action.question
    assert current.project_id == first.project_id
    assert current.thread_id != first.thread_id

    def model(prompt, data, schema):
        assert data["history"] == []
        assert "old-vit-answer" not in str(data)
        return schema(
            answer_relevance=0,
            evidence_strength=0,
            dimensions=[],
            analysis=AnswerAnalysis(
                status="partial",
                contradictions=["The earlier ViT conflicts"],
                uncertainties=["Explain the old ViT architecture"],
                missing_information=["Clarify ViT"],
            ),
        )

    current_answer = answer.model_copy(
        update={
            "question_id": current.question_id,
            "answer_id": "current-yes",
            "text": "yes",
        }
    )
    feedback = await LLMEvaluationAdapter(model, repository).evaluate(
        EvaluationRequest(
            request_id="current-feedback",
            interview_id="pipeline-interview",
            question=current,
            answer=current_answer,
        )
    )
    assert feedback.analysis.contradictions == []
    assert feedback.analysis.uncertainties == []
    assert feedback.analysis.missing_information == [
        "Describe one concrete action you personally took"
    ]
    following = await service.apply_evaluation_feedback(
        "pipeline-interview",
        feedback,
        answer=current_answer,
    )
    assert following.question.thread_id == current.thread_id
    assert following.question.parent_question_id == current.question_id
    assert "vit" not in following.question.text.casefold()
    assert "vit" not in following.question.information_goal.casefold()
