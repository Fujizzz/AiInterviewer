"""Project continuity covers compilation, durable visits, recovery and explicit returns."""

from unittest.mock import AsyncMock

import pytest

from agents.domain.models import InterviewContext
from agents.orchestrator import InterviewAgentService
from agents.orchestrator.recovery import QuestionUnavailable, quarantine, recovery_target
from agents.planning.background import BackgroundReplanner
from agents.planning.payload import planner_payload
from agents.planning.planner import InterviewPlannerAgent, execution_topics
from agents.planning.projects import project_route
from agents.policies.dialogue_controller import DialogueController
from agents.question.prepared import PreparedUseDecision, opening_plan, review_version
from shared.contracts import CandidateAnswer, InterviewActionType, PlannedQuestion, QuestionType
from shared.contracts.planning import PlanProposal
from shared.contracts.prepared import PreparedQuestion
from tests.agent.integration.test_interview_planning import Clock, PlannerLLM, covered_feedback
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.integration.test_question_recovery import RejectedAgent
from tests.agent.mocks import InMemoryRepository


class MultiProjectLLM(PlannerLLM):
    """Deliberately interleave two topics from each project in every model plan."""

    async def generate_structured(self, *, prompt_name, payload, response_model):
        if prompt_name == "interview_planner_v2":
            self.plans.append(payload)
            groups = {}
            for item in payload["eligible_topics"]:
                groups.setdefault(item["project_id"], []).append(item)
            interleaved = [
                items[index]
                for index in range(2)
                for items in groups.values()
                if len(items) > index
            ]
            return response_model.model_validate(
                {
                    "base_plan_version": payload["base_plan_version"],
                    "topics": [
                        {
                            "topic_key": item["topic_key"],
                            "objective": "Assess implementation of " + item["label"],
                            "completion_criteria": "Establish the mechanism and boundary tests",
                        }
                        for item in interleaved
                    ],
                    "reason": "Inspect selected implementation mechanisms",
                }
            )
        if response_model is PreparedUseDecision:
            return PreparedUseDecision(usable=True, reason="Fits the unanswered implementation")
        return await super().generate_structured(
            prompt_name=prompt_name,
            payload=payload,
            response_model=response_model,
        )


async def multi_setup(*, cache=False):
    request = pipeline_request("project-continuity")
    request.planning_enabled = True
    request.duration_seconds = 1800
    original = request.candidate_profile.projects[0]
    request.candidate_profile.projects = [
        original.model_copy(
            deep=True,
            update={
                "project_id": owner,
                "name": f"Project {owner}",
                "claims": original.claims[:2],
                "technologies": [],
                "metrics": [],
            },
        )
        for owner in ("A", "B", "C")
    ]
    saved = []
    if cache:
        from agents.policies.topic_selector import TopicSelector

        for project in request.candidate_profile.projects:
            for topic in TopicSelector().candidates(project):
                plan = opening_plan(project, topic)
                saved.append(
                    PreparedQuestion(
                        cache_id=topic.topic_key,
                        kind="opening",
                        project_id=project.project_id,
                        topic_key=topic.topic_key,
                        topic=topic.topic,
                        text=f"How did you implement {topic.topic} in {project.project_id}?",
                        information_goal=plan.information_goal,
                        difficulty=2,
                        question_type=plan.question_type,
                        review_version=review_version(),
                    )
                )
    repo, llm = InMemoryRepository(), MultiProjectLLM()
    service = InterviewAgentService(
        repository=repo,
        llm=llm,
        clock=Clock(),
        prepared_questions=saved,
    )
    initial = await service.initialize_interview(request)
    return service, repo, llm, initial


def proposal(context, keys, **extra):
    topics = {item.topic_key: item for item in context.plan.topics}
    return PlanProposal.model_validate(
        {
            "base_plan_version": context.plan.version,
            "topics": [
                {
                    "project_id": topics[key].project_id,
                    "topic_key": key,
                    "objective": topics[key].objective,
                    "completion_criteria": topics[key].completion_criteria,
                }
                for key in keys
            ],
            "reason": "Prioritize remaining implementation evidence",
            **extra,
        }
    )


def enter(context, owner):
    """Commit a thread transition without claiming any answer coverage."""
    item = next(
        t
        for t in context.plan.topics
        if t.project_id == owner and t.topic_key not in context.used_topic_keys
    )
    question = PlannedQuestion(
        question_id=f"visit-{owner}-{context.state.question_index}",
        thread_id=f"visit-{owner}-{context.state.question_index}",
        project_id=owner,
        topic=item.objective,
        topic_key=item.topic_key,
        text="How did you implement this mechanism?",
        intent=item.objective,
        information_goal=item.objective,
        dialogue_action="new_project",
        difficulty=2,
        probe_depth=1,
        question_type=QuestionType.DESCRIPTION,
    )
    DialogueController(context).record_question(question, closed_reason="TEST_PROJECT_SWITCH")
    context.state.question_index += 1
    return question


@pytest.mark.asyncio
async def test_compilation_groups_model_agenda_and_preserves_goal_contracts():
    service, repo, llm, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    assert [t.project_id for t in context.plan.topics] == ["A", "A", "B", "B", "C", "C"]
    assert any(
        a["action"] == "project_topics_grouped" for a in context.plan_history[-1].adjustments
    )
    assert len(llm.plans) == 1
    before = {t.topic_key: (t.objective, t.completion_criteria) for t in context.plan.topics}
    enter(context, "B")
    eligible = service._interview_planner._eligible(context)
    keys = [t.topic_key for t in context.plan.topics]
    service._interview_planner.accept(context, proposal(context, keys), "PERIODIC_REVIEW")
    selected = context.plan_history[-1].proposal.topics
    assert [t.project_id for t in selected] == ["B", "B", "C", "C"]
    assert all((t.objective, t.completion_criteria) == before[t.topic_key] for t in selected)
    assert context.topic_progress[keys[0]].coverage_status != "sufficient"
    assert all(t.project_id != "A" for t in execution_topics(context))
    assert all(
        t.project_id != "A" for t in service._interview_planner._fallback(context, eligible).topics
    )
    assert eligible  # Scope eligibility is separate from the project return policy.


@pytest.mark.asyncio
async def test_real_service_questions_stay_grouped_and_still_use_cache():
    service, repo, _, initial = await multi_setup(cache=True)
    action, owners = initial.first_action, []
    for index in range(6):
        assert action.type == InterviewActionType.ASK_QUESTION
        question = action.question
        owners.append(question.project_id)
        assert action.decision_trace.details["generation_reason"] == "PREPARED_QUESTION"
        context = await repo.get_interview_context(initial.interview_id)
        answer = CandidateAnswer(
            interview_id=initial.interview_id,
            question_id=question.question_id,
            answer_id=f"answer-{index}",
            text="I implemented the mechanism and tested its boundary.",
        )
        fb = covered_feedback(
            question,
            answer.answer_id,
            answer.text,
            requirements=context.topic_progress[question.topic_key].completion_requirements,
        )
        fb.request_id = f"advance-{index}"
        action = await service.apply_evaluation_feedback(initial.interview_id, fb, answer=answer)
    assert owners == ["A", "A", "B", "B", "C", "C"]
    assert action.type == InterviewActionType.FINISH


@pytest.mark.asyncio
@pytest.mark.parametrize("after_quarantine", [False, True])
async def test_restart_and_recovery_keep_current_project_and_never_return_by_default(
    after_quarantine,
):
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    enter(context, "B")
    if after_quarantine:
        current = next(t for t in context.plan.topics if t.project_id == "B")
        quarantine(context, QuestionUnavailable(current, reason="TIMEOUT"))
        assert context.active_thread is None
    restored = InterviewContext.model_validate(context.model_dump(mode="json"))
    assert project_route(restored) == {
        "active_project_id": "B",
        "left_project_ids": ["A"],
        "visit_index": 2,
    }
    target = recovery_target(restored, service._settings)
    assert target.project.project_id == "B"
    payload = planner_payload(
        restored, "PERIODIC_REVIEW", service._interview_planner._eligible(restored)
    )
    assert payload["project_continuity"]["active_project_id"] == "B"
    assert all(
        t["requires_project_revisit"] for t in payload["eligible_topics"] if t["project_id"] == "A"
    )
    with pytest.raises(ValueError, match="PROJECT_ALREADY_LEFT"):
        enter(restored, "A")


@pytest.mark.asyncio
async def test_failed_followup_recovery_uses_next_cached_topic_in_same_project():
    service, repo, _, initial = await multi_setup(cache=True)
    service._interview_planner.review = AsyncMock()
    current = initial.first_action.question
    from tests.agent.integration.test_interview_planning import feedback

    service._question_agent = RejectedAgent(None, stop_reason="TIMEOUT")
    action = await service.apply_evaluation_feedback(initial.interview_id, feedback(current))
    assert action.question.project_id == current.project_id == "A"
    assert action.question.topic_key != current.topic_key
    assert action.decision_trace.details["generation_reason"] == "PREPARED_QUESTION"
    saved = await repo.get_interview_context(initial.interview_id)
    assert project_route(saved)["left_project_ids"] == []
    assert saved.topic_progress[current.topic_key].coverage_status != "sufficient"


@pytest.mark.asyncio
async def test_explicit_revisit_survives_local_compilation_but_not_another_visit():
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    enter(context, "B")
    a = next(
        t
        for t in context.plan.topics
        if t.project_id == "A" and t.topic_key not in context.used_topic_keys
    )
    c = next(t for t in context.plan.topics if t.project_id == "C")
    planner = service._interview_planner
    planner.accept(
        context,
        proposal(
            context,
            [a.topic_key],
            revisit_projects={"A": "Resolve the missing recovery validation"},
        ),
        "REVISIT_GAP",
    )
    assert any(t.project_id == "A" for t in execution_topics(context))
    planner.accept(
        context,
        planner._fallback(context, planner._eligible(context)),
        "TIME_DRIFT",
        local_only=True,
    )
    assert any(t.project_id == "A" for t in execution_topics(context))
    # Allow a new topic in A, leaving B; the grant must not authorize a later C -> A return.
    context.plan.topics.sort(key=lambda t: t.topic_key == a.topic_key, reverse=True)
    enter(context, "A")
    enter(context, "C")
    planner.accept(context, proposal(context, [a.topic_key, c.topic_key]), "PERIODIC_REVIEW")
    assert [t.project_id for t in context.plan_history[-1].proposal.topics] == ["C"]


@pytest.mark.asyncio
async def test_project_limit_moves_to_next_project_without_returning():
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    context.plan.max_questions_per_project = 1
    target = recovery_target(context, service._settings)
    assert target.project.project_id == "B"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["completed", "skipped", "no_open_needs", "question_limit"])
async def test_explicit_return_does_not_override_topic_boundaries(boundary):
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    enter(context, "B")
    a = next(t for t in context.plan.topics if t.project_id == "A")
    b = next(t for t in context.plan.topics if t.project_id == "B")
    progress = context.topic_progress[a.topic_key]
    requested = proposal(
        context,
        [a.topic_key, b.topic_key],
        revisit_projects={"A": "Resolve a critical validation gap"},
    )
    if boundary in {"completed", "skipped"}:
        progress.status = boundary
    elif boundary == "no_open_needs":
        progress.questions_asked = 1
        progress.needs_assessed = True
        progress.information_needs = []
    else:
        progress.questions_asked = context.plan.max_questions_per_topic
    before = progress.model_dump()
    service._interview_planner.accept(context, requested, "REVISIT_GAP")
    assert all(t.project_id != "A" for t in execution_topics(context))
    assert progress.model_dump() == before


@pytest.mark.asyncio
async def test_short_remaining_time_prioritizes_an_unvisited_project():
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    a = next(
        t
        for t in context.plan.topics
        if t.project_id == "A" and t.topic_key not in context.used_topic_keys
    )
    b = next(t for t in context.plan.topics if t.project_id == "B")
    context.state.remaining_seconds = 40
    InterviewPlannerAgent(service._llm, service._settings).accept(
        context, proposal(context, [b.topic_key, a.topic_key]), "TIME_DRIFT"
    )
    assert [t.project_id for t in execution_topics(context)] == ["B"]
    assert context.plan.topics[0].budget_seconds == 40
    assert context.plan.reserve_seconds == context.plan.closing_seconds == 0


@pytest.mark.asyncio
async def test_recovery_switches_when_current_project_has_no_askable_planned_topic():
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    for topic in context.plan.topics:
        if topic.project_id == "A":
            progress = context.topic_progress[topic.topic_key]
            progress.needs_assessed = True
            progress.information_needs = []
    target = recovery_target(context, service._settings)
    assert target.project.project_id == "B"
    assert target.probe.dialogue_action == "new_project"


@pytest.mark.asyncio
async def test_empty_revisit_after_background_result_arrives_is_discarded_safely():
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    a = next(t for t in context.plan.topics if t.project_id == "A")
    coordinator = BackgroundReplanner(service._interview_planner)
    coordinator._propose = AsyncMock(return_value=proposal(context, [a.topic_key]))
    coordinator.request(context, "PERIODIC_REVIEW")
    coordinator.start(context)
    await coordinator.tasks[context.interview_id]
    enter(context, "B")
    before = context.plan.model_dump()
    coordinator.consume(context)
    assert context.plan.model_dump() == before
    assert context.active_thread.project_id == "B"
    await coordinator.close()


@pytest.mark.asyncio
async def test_background_revisit_cannot_apply_after_project_transition():
    service, repo, _, initial = await multi_setup()
    context = await repo.get_interview_context(initial.interview_id)
    enter(context, "B")
    a = next(t for t in context.plan.topics if t.project_id == "A")
    pending = proposal(
        context, [a.topic_key], revisit_projects={"A": "Resolve critical validation evidence"}
    )
    service._interview_planner.revise = AsyncMock()
    coordinator = BackgroundReplanner(service._interview_planner)
    # Isolate acceptance of an already generated proposal from provider generation.
    coordinator._propose = AsyncMock(return_value=pending)
    coordinator.request(context, "REVISIT_GAP")
    coordinator.start(context)
    await coordinator.tasks[context.interview_id]
    enter(context, "C")
    before = context.plan.model_dump()
    coordinator.consume(context)
    assert context.plan.model_dump() == before
    assert all(t.project_id != "A" for t in execution_topics(context))
    await coordinator.close()
