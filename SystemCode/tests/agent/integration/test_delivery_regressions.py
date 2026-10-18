"""Regress real-run malformed evidence and protect all-project first visits."""

import pytest

from agents.domain.models import InterviewHistoryEntry
from agents.planning.pace import round_cost
from agents.planning.planner import execution_topics
from agents.planning.projects import unvisited_projects
from agents.question.prepared import admit_prepared, opening_plan
from agents.question.quality import QuestionQualityGate
from app.settings import interview_settings
from shared.contracts import CandidateAnswer
from tests.agent.integration.test_prepared_questions import prepared
from tests.agent.integration.test_project_continuity import enter, multi_setup, proposal
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.integration.test_question_react import seed


@pytest.mark.asyncio
async def test_overlong_repeat_quote_isolated_without_erasing_another_issue():
    repo, _, previous, feedback, answer = await seed()
    answer.text = "I checked the version and rejected stale results. " * 20
    context = await repo.get_interview_context("pipeline-interview")
    context.question_history = [
        InterviewHistoryEntry(question=previous, feedback=feedback, answer=answer)
    ]
    candidate = previous.model_copy(
        update={"text": "Which version did you check? Here is the answer."}
    )

    class Reviewer:
        async def generate_structured(self, *, response_model, **kwargs):
            return response_model(
                issues=[
                    dict(
                        code="SEMANTIC_REPEAT",
                        question_quote="Which version did you check?",
                        instruction="Do not repeat",
                        comparison_question_id=previous.question_id,
                        comparison_answer_quote=answer.text,
                        repeat_relation="already_answered",
                        answer_coverage="direct",
                        answer_evidence_kind="concrete_answer",
                    ),
                    dict(
                        code="ANSWER_HINT",
                        question_quote="Here is the answer.",
                        instruction="Remove hint",
                    ),
                ]
            )

    review = await QuestionQualityGate(Reviewer(), interview_settings()).review(candidate, context)
    assert [issue.code for issue in review.issues] == ["ANSWER_HINT"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,quote,source,blocked",
    [
        ("summary_only", "I built an agent project.", "introduction", False),
        ("concrete_answer", "invented", "introduction", False),
        ("concrete_answer", "I stored remaining seconds in the plan state.", "unknown", False),
        ("concrete_answer", "I stored remaining seconds in the plan state.", "introduction", True),
    ],
)
async def test_cache_repetition_requires_real_concrete_answer(kind, quote, source, blocked):
    from agents.orchestrator import InterviewAgentService
    from tests.agent.mocks import InMemoryRepository

    request = pipeline_request()
    item = prepared(request)
    repo = InMemoryRepository()
    initial = await InterviewAgentService(repository=repo).initialize_interview(request)
    context = await repo.get_interview_context(initial.interview_id)
    context.introduction_answer = CandidateAnswer(
        interview_id=initial.interview_id,
        question_id="intro",
        answer_id="intro-answer",
        text="I built an agent project. I stored remaining seconds in the plan state.",
    )
    context.question_history = []
    context.used_prepared_question_ids = []
    project = context.candidate_profile.projects[0]
    from agents.policies.topic_selector import TopicSelector

    plan = opening_plan(project, TopicSelector().candidates(project)[0])

    class Reviewer:
        calls = 0

        async def generate_structured(self, *, response_model, **kwargs):
            self.calls += 1
            return response_model(
                usable=False,
                reason="REPEAT",
                comparison_source_id=source,
                comparison_answer_quote=quote,
                answer_coverage="direct",
                answer_evidence_kind=kind,
            )

    model = Reviewer()
    question, reason = await admit_prepared(item, plan, context, model, interview_settings())
    assert (question is None) == blocked
    assert model.calls == 1


@pytest.mark.asyncio
async def test_replan_cannot_drop_unvisited_projects_and_reserves_their_slots():
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    current = context.active_thread.project_id
    keys = [t.topic_key for t in context.plan.topics if t.project_id == current]
    service._interview_planner.accept(context, proposal(context, keys), "PERIODIC_REVIEW")
    assert {t.project_id for t in execution_topics(context)} == {"A", "B", "C"}
    assert any(
        a["action"] == "project_entry_preserved" for a in context.plan_history[-1].adjustments
    )
    # Exactly two slots remain: the two unvisited projects outrank any extra A question.
    context.plan.max_questions = context.state.question_index + 2
    service._interview_planner.accept(context, proposal(context, keys), "PERIODIC_REVIEW")
    assert {t.project_id for t in execution_topics(context)} == {"B", "C"}
    assert {t.project_id for t in context.plan_history[-1].proposal.topics} == {"A", "B", "C"}
    assert all(t.expected_questions == 1 for t in execution_topics(context))


@pytest.mark.asyncio
async def test_time_reserve_forces_transition_and_allows_each_unvisited_project():
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    settings = service._settings
    pace = round_cost(context, settings)
    context.state.remaining_seconds = context.plan.closing_seconds + 3 * pace
    assert unvisited_projects(context) == ["B", "C"]
    await service._interview_planner.review(context, allow_model_request=False)
    assert {t.project_id for t in execution_topics(context)} == {"B", "C"}
    enter(context, "B")
    context.state.remaining_seconds = context.plan.closing_seconds + 2 * pace
    await service._interview_planner.review(context, allow_model_request=False)
    assert {t.project_id for t in execution_topics(context)} == {"C"}
    enter(context, "C")
    assert unvisited_projects(context) == []


@pytest.mark.asyncio
async def test_service_covers_all_projects_even_if_replanner_keeps_only_current_project():
    from shared.contracts import InterviewActionType
    from tests.agent.integration.test_interview_planning import feedback

    service, repo, llm, initial = await multi_setup(cache=True)
    original = llm.generate_structured

    async def biased_plan(*, prompt_name, payload, response_model):
        if prompt_name != "interview_planner_v2":
            return await original(
                prompt_name=prompt_name, payload=payload, response_model=response_model
            )
        active = payload["project_continuity"]["active_project_id"]
        choices = [t for t in payload["eligible_topics"] if t["project_id"] == active]
        choices = choices[:1] or payload["eligible_topics"][:1]
        return response_model.model_validate(
            {
                "base_plan_version": payload["base_plan_version"],
                "topics": [
                    dict(
                        topic_key=t["topic_key"],
                        objective="Inspect a concrete mechanism",
                        completion_criteria="Explain one implementation boundary",
                    )
                    for t in choices
                ],
                "reason": "Deliberately keep deepening only the current project",
            }
        )

    llm.generate_structured = biased_plan
    action, owners = initial.first_action, []
    for index in range(15):
        if action.type != InterviewActionType.ASK_QUESTION:
            break
        question = action.question
        owners.append(question.project_id)
        service._clock.value += 150
        answer = CandidateAnswer(
            interview_id=initial.interview_id,
            question_id=question.question_id,
            answer_id=f"coverage-{index}",
            text="I implemented the state update; validation needs more detail.",
        )
        action = await service.apply_evaluation_feedback(
            initial.interview_id,
            feedback(question, index + 1),
            answer=answer,
        )
    assert set(owners) == {"A", "B", "C"}
    visits = [owner for i, owner in enumerate(owners) if i == 0 or owner != owners[i - 1]]
    assert visits == ["A", "B", "C"]
