import asyncio

import pytest

from agents.orchestrator import InterviewAgentService
from agents.planning.background import BackgroundReplanner
from shared.contracts import CandidateAnswer
from tests.agent.integration.test_interview_planning import (
    PlannerLLM,
    covered_feedback,
    feedback,
    setup,
)


class SlowPlanner(PlannerLLM):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_structured(self, **kwargs):
        if kwargs["prompt_name"] == "interview_planner_v1" and self.plans:
            self.entered.set()
            await self.release.wait()
        return await super().generate_structured(**kwargs)


@pytest.mark.asyncio
async def test_slow_planner_does_not_block_next_question_or_write_state():
    llm = SlowPlanner()
    original, repo, clock, initial = await setup(llm)
    service = InterviewAgentService(
        repository=repo,
        llm=llm,
        settings=original._settings,
        clock=clock,
        background_replanning=True,
    )
    q = initial.first_action.question
    answer = CandidateAnswer(
        interview_id=initial.interview_id,
        question_id=q.question_id,
        answer_id="one",
        text="I implemented the mechanism and explained its trade-off.",
    )
    action = await asyncio.wait_for(
        service.apply_evaluation_feedback(
            initial.interview_id, covered_feedback(q, answer.answer_id, answer.text), answer=answer
        ),
        1,
    )
    assert action.question and action.question.question_id != q.question_id
    await asyncio.wait_for(llm.entered.wait(), 1)
    before = await repo.get_interview_context(initial.interview_id)
    llm.release.set()
    await asyncio.wait_for(service._background_replanner.tasks[initial.interview_id], 1)
    assert await repo.get_interview_context(initial.interview_id) == before
    coordinator = service._background_replanner
    coordinator.consume(before)
    assert before.state.current_question_id == action.question.question_id
    assert before.topic_progress[q.topic_key].status == "completed"
    assert before.plan.version > initial.plan.version
    second = CandidateAnswer(
        interview_id=initial.interview_id,
        question_id=action.question.question_id,
        answer_id="two",
        text="I implemented a bounded queue.",
    )
    await service.apply_evaluation_feedback(
        initial.interview_id, feedback(action.question, index=2), answer=second
    )
    committed = await repo.get_interview_context(initial.interview_id)
    assert committed.plan.version == before.plan.version
    assert committed.topic_progress[q.topic_key].status == "completed"
    await service.close_background()


@pytest.mark.asyncio
async def test_ready_plan_cannot_resurrect_refused_goal_or_apply_stale_version():
    service, repo, _, initial = await setup(PlannerLLM())
    ctx = await repo.get_interview_context(initial.interview_id)
    coordinator = BackgroundReplanner(service._interview_planner)
    coordinator.request(ctx, "TIME_DRIFT")
    coordinator.start(ctx)
    await coordinator.tasks[ctx.interview_id]
    current = ctx.model_copy(deep=True)
    key = current.plan.topics[0].topic_key
    current.topic_progress[key].status = "skipped"
    coordinator.consume(current)
    assert current.topic_progress[key].status == "skipped"
    assert key not in {
        t.topic_key
        for t in current.plan.topics
        if current.topic_progress[t.topic_key].status in {"active", "pending"}
    }
    coordinator.request(current, "TIME_DRIFT")
    coordinator.start(current)
    await coordinator.tasks[current.interview_id]
    current.plan.version += 1
    before = current.plan.model_dump()
    coordinator.consume(current)
    assert current.plan.model_dump() == before
    await coordinator.close()


@pytest.mark.asyncio
async def test_closing_session_cancels_pending_plan_without_publishing():
    llm = SlowPlanner()
    service, repo, _, initial = await setup(llm)
    ctx = await repo.get_interview_context(initial.interview_id)
    coordinator = BackgroundReplanner(service._interview_planner)
    coordinator.request(ctx, "TIME_DRIFT")
    coordinator.start(ctx)
    await llm.entered.wait()
    await coordinator.close()
    assert not coordinator.tasks
    assert (await repo.get_interview_context(initial.interview_id)).plan == ctx.plan


@pytest.mark.asyncio
async def test_cas_recomputation_can_consume_the_same_ready_plan_without_another_model_call():
    service, repo, _, initial = await setup(PlannerLLM())
    ctx = await repo.get_interview_context(initial.interview_id)
    coordinator = BackgroundReplanner(service._interview_planner)
    coordinator.request(ctx, "TIME_DRIFT")
    coordinator.start(ctx)
    await coordinator.tasks[ctx.interview_id]
    first_attempt = ctx.model_copy(deep=True)
    coordinator.consume(first_attempt)
    # Failed CAS means the changed snapshot is discarded; the ready result must survive.
    second_attempt = ctx.model_copy(deep=True)
    coordinator.consume(second_attempt)
    assert second_attempt.plan == first_attempt.plan
    assert len(service._llm.plans) == 2  # Initial plan and exactly one background request.
    coordinator.start(second_attempt)  # Simulated successful commit acknowledges the result.
    assert not coordinator.tasks
    await coordinator.close()


@pytest.mark.asyncio
async def test_objective_replacement_is_discarded_after_new_feedback_even_without_new_question():
    service, repo, _, initial = await setup(PlannerLLM())
    ctx = await repo.get_interview_context(initial.interview_id)
    coordinator = BackgroundReplanner(service._interview_planner)
    coordinator.request(ctx, "TIME_DRIFT")
    coordinator.start(ctx)
    proposal = await coordinator.tasks[ctx.interview_id]
    proposal.topics[0].objective_change = "replace"
    current = ctx.model_copy(deep=True)
    current.processed_feedback_ids.append("new-answer-feedback")
    before = current.plan.model_dump()
    coordinator.consume(current)
    assert current.plan.model_dump() == before
    assert current.topic_progress == ctx.topic_progress
    await coordinator.close()
