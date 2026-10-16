"""Opening planning is speculative, bounded and never delays a submitted introduction."""

import asyncio

import pytest

from agents.config import load_agent_settings
from agents.domain.errors import StateConflictError
from agents.orchestrator import InterviewAgentService
from agents.planning.wire import CompactPlanProposal
from shared.contracts import CandidateAnswer, InterviewStage
from tests.agent.integration.test_interview_planning import Clock, PlannerLLM
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository


class OpeningPlanner(PlannerLLM):
    def __init__(self, *, fail=False):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.attempts = 0
        self.fail = fail

    async def generate_structured(self, **kwargs):
        if kwargs["prompt_name"] == "interview_planner_v2":
            self.attempts += 1
            self.entered.set()
            await self.release.wait()
            if self.fail:
                raise ValueError("Unavailable opening plan")
        return await super().generate_structured(**kwargs)


def test_verbose_planning_reason_is_bounded_without_weakening_topic_validation():
    proposal = CompactPlanProposal.model_validate(
        {"base_plan_version": 0, "topics": [], "reason": "Detailed plan explanation. " * 40}
    )
    assert len(proposal.reason) == 300
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompactPlanProposal.model_validate(
            {"base_plan_version": 0, "topics": [{"topic_key": ""}], "reason": "Valid reason"}
        )


async def opening(*, fail=False, planning=True):
    llm, repository, clock = OpeningPlanner(fail=fail), InMemoryRepository(), Clock()
    request = pipeline_request()
    request.planning_enabled = planning
    request.include_introduction = True
    service = InterviewAgentService(
        repository=repository, llm=llm, settings=load_agent_settings(), clock=clock
    )
    initial = await asyncio.wait_for(service.initialize_interview(request), 1)
    answer = CandidateAnswer(
        interview_id=initial.interview_id,
        question_id=initial.first_action.question.question_id,
        answer_id="intro-answer",
        text="I built a serving platform and worked on GPU memory management.",
    )
    return service, repository, clock, llm, initial, answer


@pytest.mark.asyncio
async def test_ready_initial_plan_is_adopted_once_with_current_remaining_time():
    service, repo, clock, llm, initial, answer = await opening()
    try:
        await asyncio.wait_for(llm.entered.wait(), 1)
        assert initial.state.stage == InterviewStage.INTRO
        assert initial.plan.version == 0
        before = await repo.get_interview_context(initial.interview_id)
        llm.release.set()
        await asyncio.wait_for(service._initial_planner.tasks[initial.interview_id], 1)
        assert await repo.get_interview_context(initial.interview_id) == before
        clock.value += 45
        action = await service.complete_introduction(
            initial.interview_id, answer, request_id="intro-request"
        )
        context = await repo.get_interview_context(initial.interview_id)
        revision = context.plan_history[0]
        assert revision.trigger == "INITIAL_PLAN"
        assert revision.source == "model"
        assert revision.remaining_at_compile == 855
        assert action.question.intent != "SELF_INTRODUCTION"
        assert not context.question_history and not context.evidence_records
        assert context.introduction_answer == answer
        replay = await service.complete_introduction(
            initial.interview_id, answer, request_id="intro-request"
        )
        assert replay.action_id == action.action_id
        assert llm.attempts == 1
        assert not service._initial_planner.tasks
    finally:
        await service.close_background()


@pytest.mark.asyncio
async def test_initial_plan_survives_commit_conflict_without_another_planner_request(monkeypatch):
    service, repo, _, llm, initial, answer = await opening()
    try:
        llm.release.set()
        await service._initial_planner.tasks[initial.interview_id]
        commit = repo.commit_turn
        attempts = 0

        async def conflict_once(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise StateConflictError("Concurrent state update")
            return await commit(request)

        monkeypatch.setattr(repo, "commit_turn", conflict_once)
        await service.complete_introduction(initial.interview_id, answer, request_id="intro-request")
        context = await repo.get_interview_context(initial.interview_id)
        assert attempts == 2
        assert llm.attempts == 1
        assert len(context.plan_history) == 1
        assert context.plan_history[0].source == "model"
    finally:
        await service.close_background()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_task", [False, True])
async def test_pending_or_lost_initial_plan_uses_local_agenda_without_second_model_call(missing_task):
    service, repo, _, llm, initial, answer = await opening()
    try:
        await asyncio.wait_for(llm.entered.wait(), 1)
        if missing_task:
            await service.close_background()  # Equivalent to a resumed service without its task.
        action = await asyncio.wait_for(
            service.complete_introduction(initial.interview_id, answer, request_id="intro-request"),
            1,
        )
        context = await repo.get_interview_context(initial.interview_id)
        assert action.question.intent != "SELF_INTRODUCTION"
        assert context.plan_history[0].fallback_used
        assert context.plan_history[0].trigger == "INITIAL_PLAN"
        assert llm.attempts == 1
        assert not service._initial_planner.tasks
        llm.release.set()
        await asyncio.sleep(0)
        assert (await repo.get_interview_context(initial.interview_id)).plan == context.plan
    finally:
        await service.close_background()


@pytest.mark.asyncio
async def test_failed_initial_model_plan_does_not_retry_on_introduction_submission():
    service, repo, _, llm, initial, answer = await opening(fail=True)
    try:
        llm.release.set()
        await service._initial_planner.tasks[initial.interview_id]
        await service.complete_introduction(initial.interview_id, answer, request_id="intro-request")
        context = await repo.get_interview_context(initial.interview_id)
        assert context.plan_history[0].fallback_used
        assert llm.attempts == 1
    finally:
        await service.close_background()


@pytest.mark.asyncio
async def test_user_finish_during_opening_cancels_initial_plan_without_technical_question():
    service, repo, _, llm, initial, answer = await opening()
    try:
        await asyncio.wait_for(llm.entered.wait(), 1)
        action = await asyncio.wait_for(
            service.complete_introduction(
                initial.interview_id, answer, request_id="intro-finish", finish=True
            ),
            1,
        )
        context = await repo.get_interview_context(initial.interview_id)
        assert action.type.value == "finish"
        assert context.state.question_index == 1
        assert not context.plan_history
        assert not service._initial_planner.tasks
        assert llm.attempts == 1
    finally:
        await service.close_background()


@pytest.mark.asyncio
async def test_planning_disabled_does_not_start_opening_model_call():
    service, _, _, llm, _, _ = await opening(planning=False)
    try:
        await asyncio.sleep(0)
        assert not service._initial_planner.tasks
        assert llm.attempts == 0
    finally:
        await service.close_background()
