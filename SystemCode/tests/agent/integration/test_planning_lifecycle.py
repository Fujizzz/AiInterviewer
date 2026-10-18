"""Fallback replanning must choose executable targets after a completed narrow answer."""

import pytest

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from shared.contracts import CandidateAnswer, InterviewActionType, ObjectiveCoverage
from tests.agent.integration.test_interview_planning import Clock, feedback, setup
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
async def test_completed_question_without_coverage_advances_to_unasked_targets(background):
    original, repo, clock, initialized = await setup(None, max_questions=3)
    service = InterviewAgentService(
        repository=repo,
        settings=original._settings,
        clock=clock,
        background_replanning=background,
    )
    action = initialized.first_action
    topics = []
    try:
        for index in range(1, 4):
            assert action.type == InterviewActionType.ASK_QUESTION
            question = action.question
            topics.append(question.topic_key)
            answer = CandidateAnswer(
                interview_id=initialized.interview_id,
                question_id=question.question_id,
                answer_id=f"answer-{index}",
                text="I implemented the bounded queue.",
            )
            fb = feedback(question, index, complete=True)
            action = await service.apply_evaluation_feedback(
                initialized.interview_id, fb, answer=answer
            )
            context = await repo.get_interview_context(initialized.interview_id)
            assert context.state.remaining_seconds == initialized.plan.duration_seconds
            assert context.topic_progress[question.topic_key].coverage_status == "unassessed"
            assert context.topic_progress[question.topic_key].status != "completed"
            if index < 3:
                assert action.type == InterviewActionType.ASK_QUESTION
                assert action.question.topic_key not in topics
                snapshot = context.model_dump()
                replay = await service.apply_evaluation_feedback(
                    initialized.interview_id, fb, answer=answer
                )
                assert replay == action
                replayed = await repo.get_interview_context(initialized.interview_id)
                assert replayed.model_dump() == snapshot
        assert len(set(topics)) == 3
        assert action.type == InterviewActionType.FINISH
        assert action.decision_trace.reason_code == "QUESTION_SAFETY_LIMIT"
    finally:
        await service.close_background()


@pytest.mark.asyncio
async def test_completed_question_with_grounded_objective_gap_keeps_followup():
    service, repo, _, initialized = await setup(MockLLMAdapter(), max_questions=3)
    question = initialized.first_action.question
    answer = CandidateAnswer(
        interview_id=initialized.interview_id,
        question_id=question.question_id,
        answer_id="gap-answer",
        text="I implemented the bounded queue.",
    )
    fb = feedback(question, complete=True)
    fb.analysis.missing_information = ["Explain how the bounded queue was validated"]
    fb.objective_coverage_status = "valid"
    fb.objective_coverage = [
        ObjectiveCoverage(
            objective_id=question.topic_key,
            coverage_status="partial",
            missing_information=["Explain how the bounded queue was validated"],
            supporting_quotes=[answer.text],
            supporting_segment_ids=["gap-answer:s0"],
            answer_id=answer.answer_id,
        )
    ]
    action = await service.apply_evaluation_feedback(initialized.interview_id, fb, answer=answer)
    assert action.type == InterviewActionType.ASK_QUESTION
    assert action.question.dialogue_action == "probe"
    assert action.question.topic_key == question.topic_key
    assert action.question.information_goal == fb.objective_coverage[0].missing_information[0]
    context = await repo.get_interview_context(initialized.interview_id)
    assert context.topic_progress[question.topic_key].coverage_status == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
async def test_no_fresh_targets_finishes_without_reopening_answered_scope(background):
    request = pipeline_request()
    request.planning_enabled = True
    project = request.candidate_profile.projects[0]
    project.claims = project.claims[:1]
    project.technologies = []
    project.metrics = []
    repo = InMemoryRepository()
    service = InterviewAgentService(
        repository=repo,
        settings=load_agent_settings(),
        clock=Clock(),
        background_replanning=background,
    )
    try:
        initialized = await service.initialize_interview(request)
        question = initialized.first_action.question
        answer = CandidateAnswer(
            interview_id=request.interview_id,
            question_id=question.question_id,
            answer_id="only-answer",
            text="I implemented the bounded queue.",
        )
        action = await service.apply_evaluation_feedback(
            request.interview_id, feedback(question, complete=True), answer=answer
        )
        assert action.type == InterviewActionType.FINISH
        assert action.decision_trace.reason_code == "NO_MORE_TOPICS"
        context = await repo.get_interview_context(request.interview_id)
        assert context.state.question_index == 1
        assert context.topic_progress[question.topic_key].coverage_status == "unassessed"
    finally:
        await service.close_background()
