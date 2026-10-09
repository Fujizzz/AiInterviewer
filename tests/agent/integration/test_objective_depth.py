import pytest

from agents.config import load_agent_settings
from agents.planning.objectives import evidence_criteria
from agents.planning.planner import InterviewPlannerAgent
from tests.agent.integration.test_interview_planning import PlannerLLM, setup


@pytest.mark.asyncio
async def test_fallback_keeps_room_for_depth_and_avoids_metric_only_goals():
    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    context.plan.version = 0
    context.plan.topics = []
    context.plan_history = []
    planner = InterviewPlannerAgent(None, load_agent_settings())
    eligible = planner._eligible(context)
    proposal = planner._fallback(context, eligible)
    assert len({t.project_id for t in proposal.topics}) == len(proposal.topics)
    assert all(evidence_criteria("standard") in t.completion_criteria for t in proposal.topics)
    assert len(proposal.topics) <= context.state.remaining_seconds // 240


@pytest.mark.asyncio
async def test_new_goals_have_depth_floor_and_preserved_goals_do_not_change():
    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    planner = InterviewPlannerAgent(None, load_agent_settings())
    proposal = context.plan_history[-1].proposal.model_copy(deep=True)
    existing = context.plan.topics[0]
    existing.completion_criteria = "An intentionally preserved criterion"
    proposal.topics[0].completion_criteria = existing.completion_criteria
    proposal.topics[1].objective_change = "replace"
    proposal.topics[1].depth = "deep"
    draft, _ = planner._compile(proposal, context, planner._eligible(context))
    assert draft.topics[0].completion_criteria == existing.completion_criteria
    assert evidence_criteria("deep") in draft.topics[1].completion_criteria


@pytest.mark.asyncio
async def test_new_topic_binds_first_intent_to_mechanism_not_model_ownership_label():
    from agents.question.dialogue import DialogueSelection, resolve_selection

    _, repo, _, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    topic = context.plan.topics[1]
    selected = DialogueSelection(
        dialogue_action="new_topic",
        project_id=topic.project_id,
        topic_key=topic.topic_key,
        information_goal="Identify personal responsibilities",
        decision_summary="Open the next topic",
    )
    question = resolve_selection(selected, context, load_agent_settings())
    assert question.need_id == context.topic_progress[topic.topic_key].next_need_id
    assert question.information_goal.startswith("Concrete implementation mechanism within")
