"""Project totals survive topic changes, history trimming, retries and restarts."""

from collections import Counter

import pytest

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from agents.policies.dialogue_controller import DialogueController
from agents.question.dialogue import DialogueSelection, resolve_selection
from shared.contracts import AnswerAnalysis, CandidateProject, EvaluationFeedback
from tests.agent.integration.test_phase3_hardening import ConflictOnceRepository
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository


def feedback(question, index):
    return EvaluationFeedback(
        request_id=f"r{index}",
        question_id=question.question_id,
        answer_relevance=0.4,
        evidence_strength=0,
        analysis=AnswerAnalysis(
            status="partial",
            new_information=True,
            missing_information=[f"Explain concrete step {index}"],
        ),
    )


def request_with_two_projects():
    request = pipeline_request()
    request.candidate_profile.projects.append(
        CandidateProject(
            project_id="parser", name="Log parser", technologies=["Python", "SQL", "Kafka"]
        )
    )
    return request


@pytest.mark.asyncio
async def test_topic_cap_switches_topic_project_cap_switches_project():
    settings = load_agent_settings().model_copy(
        update={
            "max_questions_per_project": 4,
            "max_questions_per_topic": 2,
        }
    )
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository, settings=settings)
    request = request_with_two_projects()
    action = (await service.initialize_interview(request)).first_action
    questions = [action.question]
    for i in range(4):
        action = await service.apply_evaluation_feedback(
            request.interview_id, feedback(questions[-1], i)
        )
        questions.append(action.question)
    assert [q.dialogue_action for q in questions] == [
        "new_topic",
        "clarify",
        "new_topic",
        "clarify",
        "new_project",
    ]
    assert len({q.project_id for q in questions[:4]}) == 1
    assert questions[4].project_id != questions[0].project_id
    assert questions[0].thread_id == questions[1].thread_id
    assert questions[2].thread_id == questions[3].thread_id != questions[0].thread_id
    context = await repository.get_interview_context(request.interview_id)
    assert DialogueController(context).project_questions(questions[0].project_id) == 4
    assert max(Counter(q.topic_key for q in questions).values()) == 2


class TopicHoppingModel:
    """Reproduce new_topic on every turn, which used to reset the only budget."""

    async def generate_structured(self, *, payload, response_model, **kwargs):
        # Scripted semantic pass; quality rejection is tested separately.
        if response_model.__name__ == "QuestionQualityReview":
            return response_model(issues=[])
        view = payload["dialogue_state"]
        project = next(p for p in view["projects"] if p["topics"])
        topic = project["topics"][0]
        active = view["active_thread"]
        return response_model(
            action="final",
            text=f"In {project['name']}, how did you implement {topic['label']}?",
            selection=dict(
                dialogue_action="new_project"
                if active and active["project_id"] != project["project_id"]
                else "new_topic",
                project_id=project["project_id"],
                topic_key=topic["topic_key"],
                information_goal=f"Explain implementation of {topic['label']}",
                decision_summary="Explore a different implementation detail.",
            ),
        )


@pytest.mark.asyncio
async def test_early_topic_hopping_cannot_evade_project_cap_or_return_to_exhausted_project():
    settings = load_agent_settings().model_copy(
        update={
            "max_questions_per_project": 2,
            "max_questions_per_topic": 3,
        }
    )
    settings.question_agent.history_retention = 1
    request = request_with_two_projects()
    repository = InMemoryRepository()
    service = InterviewAgentService(
        repository=repository, settings=settings, llm=TopicHoppingModel()
    )
    action = (await service.initialize_interview(request)).first_action
    questions = []
    for i in range(5):
        if action.question is None:
            break
        questions.append(action.question)
        item = feedback(action.question, i)
        # Canonical JSON roundtrip and a new service with different defaults must not reset budgets.
        stored = repository.contexts[request.interview_id]
        repository.contexts[request.interview_id] = type(stored).model_validate_json(
            stored.model_dump_json()
        )
        service = InterviewAgentService(repository=repository, llm=TopicHoppingModel())
        action = await service.apply_evaluation_feedback(request.interview_id, item)
        after = await repository.get_interview_context(request.interview_id)
        replay = await service.apply_evaluation_feedback(request.interview_id, item)
        assert replay == action
        assert await repository.get_interview_context(request.interview_id) == after
    assert action.type.value == "finish"
    assert len(questions) == 4
    assert sorted(Counter(q.project_id for q in questions).values()) == [2, 2]
    assert all(q.probe_depth == 1 for q in questions)
    controller = DialogueController(after)
    assert controller.available_topics() == {}
    assert all(
        controller.project_questions(p.project_id) == 2 for p in request.candidate_profile.projects
    )


@pytest.mark.asyncio
async def test_closed_topic_goals_remain_forbidden_after_switch():
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    request = pipeline_request()
    first = (await service.initialize_interview(request)).first_action.question
    item = feedback(first, 0)
    item.analysis = AnswerAnalysis(status="substantive", thread_complete=True)
    await service.apply_evaluation_feedback(request.interview_id, item)
    context = await repository.get_interview_context(request.interview_id)
    topic = next(iter(DialogueController(context).available_topics()))
    with pytest.raises(ValueError, match="REPEATED_INFORMATION_GOAL"):
        resolve_selection(
            DialogueSelection(
                dialogue_action="new_topic",
                project_id=first.project_id,
                topic_key=topic,
                information_goal=first.information_goal,
                decision_summary="Try to relabel the same goal as a new topic.",
            ),
            context,
            load_agent_settings(),
        )


@pytest.mark.asyncio
async def test_single_project_stops_at_project_budget_even_with_unused_topics():
    settings = load_agent_settings().model_copy(
        update={
            "max_questions_per_project": 1,
            "max_questions_per_topic": 5,
        }
    )
    request = pipeline_request()
    service = InterviewAgentService(repository=InMemoryRepository(), settings=settings)
    first = (await service.initialize_interview(request)).first_action.question
    action = await service.apply_evaluation_feedback(request.interview_id, feedback(first, 0))
    assert action.type.value == "finish"


@pytest.mark.asyncio
async def test_conflict_recomputation_counts_only_the_committed_question():
    repository = ConflictOnceRepository()
    service = InterviewAgentService(repository=repository)
    request = pipeline_request()
    first = (await service.initialize_interview(request)).first_action.question
    await service.apply_evaluation_feedback(request.interview_id, feedback(first, 0))
    context = await repository.get_interview_context(request.interview_id)
    assert repository.injected_conflict
    assert DialogueController(context).project_questions(first.project_id) == 2
    assert DialogueController(context).topic_questions(first.topic_key) == 2
