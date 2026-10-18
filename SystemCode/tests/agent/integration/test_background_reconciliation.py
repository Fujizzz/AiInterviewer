"""Speculative plans merge new goals while retaining current evidence and execution limits."""

from unittest.mock import AsyncMock

import pytest

from agents.domain.models import InterviewHistoryEntry
from agents.planning.background import BackgroundReplanner
from agents.planning.planner import catalog
from agents.tracing import trace_sink
from shared.contracts import CandidateAnswer
from shared.contracts.planning import PlanProposal
from tests.agent.integration.test_interview_planning import PlannerLLM, feedback, setup


def proposal_for(context, keys):
    known = catalog(context)
    existing = {t.topic_key: t for t in context.plan.topics}
    return PlanProposal.model_validate(
        {
            "base_plan_version": context.plan.version,
            "topics": [
                {
                    "topic_key": key,
                    "project_id": known[key]["project_id"],
                    "objective": existing[key].objective
                    if key in existing
                    else "Assess the additional implementation mechanism",
                    "completion_criteria": existing[key].completion_criteria
                    if key in existing
                    else "Establish the implementation and its boundary validation",
                    "objective_change": "preserve" if key in existing else "replace",
                    "depth": "deep",
                    "relative_weight": 4,
                }
                for key in keys
            ],
            "reason": "Prioritize usable unresolved implementation goals",
        }
    )


async def ready(service, context, proposal):
    coordinator = BackgroundReplanner(service._interview_planner)
    coordinator._propose = AsyncMock(return_value=proposal)
    coordinator.request(context, "PACE_CHANGED")
    coordinator.start(context)
    await coordinator.tasks[context.interview_id]
    return coordinator


@pytest.mark.asyncio
async def test_new_goal_survives_next_answer_and_preserves_omitted_active_goal():
    llm = PlannerLLM()
    service, repo, clock, initial = await setup(llm)
    context = await repo.get_interview_context(initial.interview_id)
    new_key = next(
        k for k in catalog(context) if k not in {t.topic_key for t in context.plan.topics}
    )
    proposal = proposal_for(context, [new_key])
    coordinator = await ready(service, context, proposal)
    active_key = context.active_thread.topic_key
    context.processed_feedback_ids.append("new-feedback")
    context.state.question_index += 1
    context.topic_progress[active_key].coverage_status = "partial"
    context.topic_progress[active_key].coverage_evidence = [{"answer_id": "new", "quote": "Proof"}]
    proof = context.topic_progress[active_key].model_copy(deep=True)
    clock.value += 35
    try:
        assert coordinator.consume(context) is True
        assert new_key in {t.topic_key for t in context.plan.topics}
        assert context.plan.topics[0].topic_key == active_key
        assert context.topic_progress[active_key].coverage_evidence == proof.coverage_evidence
        assert context.topic_progress[active_key].objective_version == proof.objective_version
        assert context.plan_history[-1].remaining_at_compile == 865
        assert len(llm.plans) == 1  # Only setup; consuming a proposal never calls the provider.
    finally:
        await coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("exclusion", ["completed", "skipped", "covered", "limit", "no_needs"])
async def test_one_newly_unavailable_topic_does_not_discard_other_goals(exclusion):
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    closed_key = context.plan.topics[1].topic_key
    new_key = next(
        k for k in catalog(context) if k not in {t.topic_key for t in context.plan.topics}
    )
    coordinator = await ready(service, context, proposal_for(context, [closed_key, new_key]))
    progress = context.topic_progress[closed_key]
    if exclusion in {"completed", "skipped"}:
        progress.status = exclusion
    elif exclusion == "covered":
        progress.coverage_status = "sufficient"
    elif exclusion == "limit":
        progress.questions_asked = context.plan.max_questions_per_topic
    else:
        progress.questions_asked = 1
        progress.needs_assessed = True
        progress.information_needs = []
    before = progress.model_copy(deep=True)
    events = []
    token = trace_sink.set(lambda event, data: events.append((event, data)))
    try:
        assert coordinator.consume(context) is True
        assert context.topic_progress[closed_key].model_dump(exclude={"status", "reason"}) == (
            before.model_dump(exclude={"status", "reason"})
        )
        if exclusion in {"completed", "skipped"}:
            assert context.topic_progress[closed_key].status == exclusion
        assert new_key in {t.topic_key for t in context.plan_history[-1].proposal.topics}
        assert closed_key not in {t.topic_key for t in context.plan_history[-1].proposal.topics}
        accepted = next(d for e, d in events if e == "planning.background_accepted")
        assert accepted["adoption"] == "partial"
        assert any(a.get("topic_key") == closed_key for a in accepted["adjustments"])
        assert accepted["allocations"]
    finally:
        trace_sink.reset(token)
        await coordinator.close()


@pytest.mark.asyncio
async def test_changed_existing_objective_does_not_erase_new_evidence_or_reject_new_topic():
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    key = context.plan.topics[0].topic_key
    new_key = next(
        k for k in catalog(context) if k not in {t.topic_key for t in context.plan.topics}
    )
    proposal = proposal_for(context, [key, new_key])
    proposal.topics[0].objective_change = "replace"
    coordinator = await ready(service, context, proposal)
    current = context.plan.topics[0]
    current.objective = "Assess the current validated recovery boundary"
    progress = context.topic_progress[key]
    progress.objective_version += 1
    progress.coverage_evidence = [{"answer_id": "latest", "quote": "New recovery proof"}]
    before = progress.model_copy(deep=True)
    try:
        assert coordinator.consume(context) is True
        assert context.topic_progress[key] == before
        assert context.plan.topics[0].objective == current.objective
        assert new_key in {t.topic_key for t in context.plan_history[-1].proposal.topics}
    finally:
        await coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["unknown", "wrong_owner", "version", "closed", "no_time"])
async def test_invalid_or_empty_result_preserves_current_plan_without_provider_retry(invalid):
    llm = PlannerLLM()
    service, repo, clock, initial = await setup(llm)
    context = await repo.get_interview_context(initial.interview_id)
    proposal = proposal_for(context, [context.plan.topics[1].topic_key])
    if invalid == "unknown":
        proposal.topics[0].topic_key = "unknown-topic"
    elif invalid == "wrong_owner":
        proposal.topics[0].project_id = "another-project"
    coordinator = await ready(service, context, proposal)
    if invalid == "version":
        context.plan.version += 1
    elif invalid == "closed":
        context.topic_progress[proposal.topics[0].topic_key].status = "completed"
    elif invalid == "no_time":
        clock.value += 900
    before = context.model_copy(deep=True)
    try:
        assert coordinator.consume(context) is False
        assert context.plan == before.plan
        assert context.topic_progress == before.topic_progress
        assert context.plan_history == before.plan_history
        assert context.state.status == "active"
        assert len(llm.plans) == 1
        assert coordinator._propose.await_count == 1
    finally:
        await coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["known_closed", "unknown", "only_closed"])
async def test_generation_prunes_known_closed_topics_but_rejects_unknown_ids(invalid):
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    closed_key = context.plan.topics[1].topic_key
    context.topic_progress[closed_key].status = "completed"
    active_key = context.active_thread.topic_key

    class Provider:
        calls = 0

        async def generate_structured(self, *, response_model, payload, **kwargs):
            self.calls += 1
            keys = [closed_key] if invalid == "only_closed" else [closed_key, active_key]
            if invalid == "unknown":
                keys[0] = "not-in-the-resume"
            return response_model.model_validate(
                {
                    "base_plan_version": payload["base_plan_version"],
                    "topics": [{"topic_key": key} for key in keys],
                    "reason": "Retain an available implementation scope",
                }
            )

    provider = Provider()
    service._interview_planner.llm = provider
    coordinator = BackgroundReplanner(service._interview_planner)
    coordinator.request(context, "AGENDA_EXHAUSTED")
    coordinator.start(context)
    try:
        await coordinator.tasks[context.interview_id]
        version = context.plan.version
        accepted = coordinator.consume(context)
        assert accepted is (invalid == "known_closed")
        assert context.plan.version == version + int(bool(accepted))
        assert context.topic_progress[closed_key].status == "completed"
        assert provider.calls == 1
        if accepted:
            assert any(
                a["action"] == "background_topic_filtered"
                for a in context.plan_history[-1].adjustments
            )
    finally:
        await coordinator.close()


@pytest.mark.asyncio
async def test_rejected_background_result_uses_local_review_without_immediate_model_retry():
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    question = initial.first_action.question
    answer = CandidateAnswer(
        interview_id=initial.interview_id,
        question_id=question.question_id,
        answer_id="answer",
        text="I implemented and checked the cache boundary.",
    )
    context.question_history.append(
        InterviewHistoryEntry(
            question=question,
            answer=answer,
            feedback=feedback(question),
        )
    )
    for progress in context.topic_progress.values():
        progress.status = "completed"
    coordinator = await ready(service, context, proposal_for(context, [question.topic_key]))
    try:
        result = coordinator.consume(context)
        assert result is False
        revision_count = len(context.plan_history)
        await service._interview_planner.review(
            context, defer=coordinator.request, allow_model_request=result is not False
        )
        assert len(context.plan_history) > revision_count  # Local agenda recovery still runs.
        assert context.pending_replan_trigger is None
        assert coordinator._propose.await_count == 1
        assert len(service._llm.plans) == 1
    finally:
        await coordinator.close()
