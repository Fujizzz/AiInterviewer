"""A follow-up to a follow-up spends the root thread's remaining budget."""

import pytest

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from agents.policies.topic_selector import TopicSelector
from agents.question.dialogue import DialogueSelection, dialogue_view, resolve_selection
from app.tracing import FileTrace
from shared.contracts import AnswerAnalysis, CandidateProject, EvaluationFeedback
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository


@pytest.mark.asyncio
async def test_nested_followups_share_budget_and_rotate_project_after_two(tmp_path):
    settings = load_agent_settings().model_copy(update={"max_questions_per_project": 3})
    request = pipeline_request()
    request.candidate_profile.projects.append(
        CandidateProject(project_id="logs", name="Log parser", technologies=["Python"])
    )
    repository = InMemoryRepository()
    questions = []
    with FileTrace(tmp_path) as trace:
        trace.emit(
            "interview.started",
            {
                "job_title": "AI Engineer",
                "max_questions": 5,
                "max_follow_up_per_topic": 2,
            },
        )
        service = InterviewAgentService(repository=repository, settings=settings)
        first = (await service.initialize_interview(request)).first_action.question
        questions.append(first)
        for index in range(3):
            # Rebuild the service to verify that the count is persisted, not local.
            service = InterviewAgentService(repository=repository, settings=settings)
            action = await service.apply_evaluation_feedback(
                request.interview_id,
                EvaluationFeedback(
                    request_id=f"r{index}",
                    question_id=questions[-1].question_id,
                    answer_relevance=0.5,
                    evidence_strength=0,
                    analysis=AnswerAnalysis(
                        status="partial",
                        new_information=True,
                        missing_information=[f"Explain implementation detail {index}"],
                    ),
                ),
            )
            if index == 1:
                exhausted = await repository.get_interview_context(request.interview_id)
            questions.append(action.question)
    q1, q2, q3, q4 = questions
    assert q1.thread_id == q2.thread_id == q3.thread_id == q1.question_id
    assert q2.parent_question_id == q1.question_id
    assert q3.parent_question_id == q2.question_id
    assert [q.probe_depth for q in questions] == [1, 2, 3, 1]
    assert exhausted.active_thread.follow_up_count == 2
    assert q4.dialogue_action == "new_project"
    assert q4.project_id != q1.project_id
    assert q4.thread_id == q4.question_id and q4.parent_question_id is None
    context = await repository.get_interview_context(request.interview_id)
    assert context.closed_threads[-1].follow_up_count == 2
    assert context.active_thread.follow_up_count == 0

    # Even an autonomous model cannot relabel another bullet in the exhausted project.
    view = dialogue_view(exhausted, load_agent_settings())
    assert view["allowed_dialogue_actions"] == ["new_project"]
    available = view["projects"]
    assert not next(p for p in available if p["project_id"] == q1.project_id)["topics"]
    project = next(p for p in request.candidate_profile.projects if p.project_id == q1.project_id)
    unused = next(
        t
        for t in TopicSelector().candidates(project)
        if t.topic_key not in exhausted.used_topic_keys
    )
    with pytest.raises(ValueError, match="UNKNOWN_OR_USED_TOPIC"):
        resolve_selection(
            DialogueSelection(
                dialogue_action="new_topic",
                project_id=q1.project_id,
                topic_key=unused.topic_key,
                information_goal="Ask another question about the same implementation",
                decision_summary="Another resume bullet was found.",
            ),
            exhausted,
            load_agent_settings(),
        )

    record = trace.path.read_text(encoding="utf-8")
    assert "本话题第 2 次追问 / 上限 2" in record
    assert "所属主问题：第 1 题；关联上一题：第 2 题" in record


@pytest.mark.asyncio
async def test_single_project_can_move_to_an_unused_topic_after_cap():
    repository = InMemoryRepository()
    service = InterviewAgentService(
        repository=repository,
        settings=load_agent_settings().model_copy(update={"max_questions_per_topic": 3}),
    )
    request = pipeline_request()
    first = (await service.initialize_interview(request)).first_action.question
    repository.contexts[request.interview_id].active_thread.follow_up_count = 2
    action = await service.apply_evaluation_feedback(
        request.interview_id,
        EvaluationFeedback(
            request_id="cap",
            question_id=first.question_id,
            answer_relevance=0.5,
            evidence_strength=0,
            analysis=AnswerAnalysis(status="partial", new_information=True),
        ),
    )
    assert action.question.project_id == first.project_id
    assert action.question.topic_key != first.topic_key
    assert action.question.dialogue_action == "new_topic"
    assert action.question.parent_question_id is None
    assert action.question.probe_depth == 1
