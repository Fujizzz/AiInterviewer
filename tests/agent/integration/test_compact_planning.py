"""Compact planning preserves coverage authority and avoids calls for soft budgets."""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agents.config import load_agent_settings
from agents.domain.models import InterviewContext, InterviewHistoryEntry
from agents.planning.allocation import PlanConstraintError
from agents.planning.background import BackgroundReplanner
from agents.planning.payload import payload_chars, planner_payload
from agents.planning.planner import InterviewPlannerAgent, planning_view
from agents.planning.wire import CompactPlanProposal
from shared.contracts import CandidateAnswer, EvaluationFeedback
from shared.contracts.planning import InformationNeed
from tests.agent.integration.test_interview_planning import PlannerLLM, feedback, setup


def choice(context, **updates):
    return CompactPlanProposal.model_validate(
        {
            "base_plan_version": context.plan.version,
            "topics": [{"topic_key": context.plan.topics[0].topic_key, **updates}],
            "reason": "Focus on the remaining validation evidence",
        }
    )


@pytest.mark.asyncio
async def test_compact_replan_preserves_exact_contract_and_proof_without_echoed_text():
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    item = context.plan.topics[0]
    progress = context.topic_progress[item.topic_key]
    progress.coverage_status = "partial"
    progress.missing_information = ["Actual validation results"]
    progress.completion_requirements[0].evidence = [{"answer_id": "proof", "quote": "Verified"}]
    evidence = progress.model_copy(deep=True)

    class CompactProvider:
        async def generate_structured(self, *, prompt_name, payload, response_model):
            assert prompt_name == "interview_planner_v2"
            assert response_model is CompactPlanProposal
            assert payload["current_goals"][0]["gaps"] == progress.missing_information
            return choice(context, relative_weight=3, depth="deep")

    await InterviewPlannerAgent(CompactProvider(), service._settings).revise(context, "REVISIT_GAP")
    updated = context.plan.topics[0]
    assert updated.project_id == item.project_id
    assert updated.objective == item.objective
    assert updated.completion_criteria == item.completion_criteria
    assert context.topic_progress[item.topic_key] == evidence
    assert context.plan_history[-1].source == "model"
    assert context.plan_history[-1].proposal.topics[0].depth == "deep"


@pytest.mark.asyncio
@pytest.mark.parametrize("new_goal", [False, True])
async def test_compact_new_and_replacement_goals_expand_with_ownership_and_evidence_floor(new_goal):
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    item = context.plan.topics[0]
    progress = context.topic_progress[item.topic_key]
    progress.coverage_status = "partial"
    progress.coverage_evidence = [{"answer_id": "old-answer", "supporting_quotes": ["Old fact"]}]
    old_version = progress.objective_version
    if new_goal:
        context.plan.topics = []
        context.topic_progress = {}

    class NewGoalProvider:
        async def generate_structured(self, *, prompt_name, payload, response_model):
            return response_model.model_validate(
                {
                    "base_plan_version": context.plan.version,
                    "topics": [
                        {
                            "topic_key": item.topic_key,
                            "depth": "deep",
                            "objective_change": "preserve" if new_goal else "replace",
                            "objective": "Assess durable state recovery",
                            "completion_criteria": "Recover state after an interrupted write",
                        }
                    ],
                    "reason": "Focus on unresolved recovery boundaries",
                }
            )

    await InterviewPlannerAgent(NewGoalProvider(), service._settings).revise(context, "REVISIT_GAP")
    updated = context.plan.topics[0]
    updated_progress = context.topic_progress[item.topic_key]
    assert context.plan_history[-1].source == "model"
    assert updated.project_id == item.project_id
    assert updated.objective == "Assess durable state recovery"
    assert "failure boundary" in updated.completion_criteria
    assert updated_progress.coverage_status == "unassessed"
    assert not updated_progress.coverage_evidence
    assert updated_progress.objective_version == (1 if new_goal else old_version + 1)
    assert updated_progress.completion_requirements


@pytest.mark.asyncio
async def test_payload_omits_private_raw_evidence_but_retains_gaps_and_blocked_needs():
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    progress = context.topic_progress[context.plan.topics[0].topic_key]
    proof = {"quote": "PRIVATE_EVIDENCE " * 2000, "answer_id": "private-answer"}
    progress.coverage_evidence = [proof]
    progress.completion_requirements[0].evidence = [proof]
    progress.coverage_status = "partial"
    progress.missing_information = ["Actual results missing; validation method already covered"]
    progress.information_needs.append(
        InformationNeed(
            need_id="blocked",
            objective_id=progress.objective_id,
            target="Production statistics unavailable",
            status="blocked",
        )
    )
    context.candidate_profile.source_coverage["raw_resume"] = "PRIVATE_RESUME"
    snapshot = context.model_dump()
    payload = planner_payload(
        context, "PERIODIC_REVIEW", service._interview_planner._eligible(context)
    )
    assert "PRIVATE_EVIDENCE" not in str(payload)
    assert "PRIVATE_RESUME" not in str(payload)
    assert payload["current_goals"][0]["gaps"] == progress.missing_information
    assert payload["current_goals"][0]["blocked_needs"] == ["Production statistics unavailable"]
    assert payload["current_goals"][0]["coverage"] == "partial"
    assert payload_chars(payload) < payload_chars(planning_view(context)) // 10
    assert context.model_dump() == snapshot
    assert "PRIVATE_EVIDENCE" in str(planning_view(context))  # Executor still sees full facts.


@pytest.mark.asyncio
async def test_payload_growth_is_independent_of_raw_history_and_evidence_growth():
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    question = initial.first_action.question
    eligible = service._interview_planner._eligible(context)

    def entry(index):
        return InterviewHistoryEntry(
            question=question,
            answer=CandidateAnswer(
                interview_id=context.interview_id,
                question_id=question.question_id,
                answer_id=f"answer-{index}",
                text="FULL_TRANSCRIPT " * 1000,
            ),
            feedback=feedback(question, index),
        )

    context.question_history = [entry(i) for i in range(3)]
    small = planner_payload(context, "PERIODIC_REVIEW", eligible)
    context.question_history = [entry(i) for i in range(100)]
    large = planner_payload(context, "PERIODIC_REVIEW", eligible)
    assert len(large["recent_changes"]) == 3
    assert "FULL_TRANSCRIPT" not in str(large)
    assert payload_chars(large) - payload_chars(small) < 30


@pytest.mark.asyncio
async def test_invalid_analysis_and_closed_scope_are_explicit_without_false_completion():
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    question = initial.first_action.question
    context.question_history.append(
        InterviewHistoryEntry(
            question=question,
            feedback=EvaluationFeedback(
                request_id="unavailable",
                question_id=question.question_id,
                analysis_status="unavailable",
                answer_relevance=0,
                evidence_strength=0,
            ),
        )
    )
    progress = context.topic_progress[question.topic_key]
    progress.status = "skipped"
    progress.coverage_status = "partial"
    payload = planner_payload(
        context, "PERIODIC_REVIEW", service._interview_planner._eligible(context)
    )
    assert question.topic_key in payload["closed_topic_keys"]
    assert question.topic_key not in {t["topic_key"] for t in payload["eligible_topics"]}
    assert payload["recent_changes"][-1] == {
        "topic_key": question.topic_key,
        "answer_id": None,
        "analysis_status": "unavailable",
    }
    assert progress.coverage_status == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unknown", "closed", "stale", "missing_new_text"])
async def test_compact_output_cannot_invent_reopen_or_replace_without_a_contract(failure):
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    proposal = choice(context)
    if failure == "unknown":
        proposal.topics[0].topic_key = "invented"
    elif failure == "closed":
        context.topic_progress[proposal.topics[0].topic_key].status = "completed"
    elif failure == "stale":
        proposal.base_plan_version += 1
    else:
        proposal.topics[0].objective_change = "replace"
    with pytest.raises(PlanConstraintError):
        proposal.expand(context, service._interview_planner._eligible(context))


@pytest.mark.parametrize(
    "extra",
    [
        {"objective": "What did you implement?"},
        {"completion_criteria": "如何验证？"},
        {"text": "A candidate-facing question"},
        {"budget_seconds": 60},
        {"project_id": "invented-owner"},
    ],
)
def test_planner_wire_rejects_question_text_budgets_and_supplied_ownership(extra):
    with pytest.raises(ValidationError):
        CompactPlanProposal.model_validate(
            {
                "base_plan_version": 1,
                "topics": [{"topic_key": "topic", **extra}],
                "reason": "Preserve the accepted goal",
            }
        )


@pytest.mark.asyncio
async def test_soft_budget_expiry_extends_open_scope_without_calling_planner():
    llm = PlannerLLM()
    service, repo, _, initial = await setup(llm)
    context = await repo.get_interview_context(initial.interview_id)
    question = initial.first_action.question
    progress = context.topic_progress[question.topic_key]
    progress.coverage_status = "partial"
    progress.missing_information = ["Actual validation result"]
    progress.elapsed_seconds = context.plan.topics[0].budget_seconds
    context.question_history.append(
        InterviewHistoryEntry(question=question, feedback=feedback(question))
    )
    calls = len(llm.plans)
    await service._interview_planner.review(context)
    assert len(llm.plans) == calls
    assert context.plan_history[-1].source == "local_compilation"
    assert context.topic_progress[question.topic_key].coverage_status == "partial"
    assert context.topic_progress[question.topic_key].missing_information == [
        "Actual validation result"
    ]
    assert context.plan.topics[0].budget_seconds > progress.elapsed_seconds


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["contradiction", "pace", "periodic"])
async def test_material_changes_still_request_semantic_planning_with_current_base(kind):
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    question = initial.first_action.question
    fb = feedback(question)
    context.question_history.append(InterviewHistoryEntry(question=question, feedback=fb))
    context.state.question_index = 2
    if kind == "contradiction":
        fb.analysis.contradictions = ["Conflicting implementation claims"]
    elif kind == "pace":
        context.estimated_question_seconds = 180
    else:
        context.state.question_index = 4
    context.topic_progress[question.topic_key].elapsed_seconds = context.plan.topics[
        0
    ].budget_seconds
    requested = Mock()
    await service._interview_planner.review(context, defer=requested)
    requested.assert_called_once()
    assert (
        requested.call_args.args[1]
        == {
            "contradiction": "CONTRADICTION_FOUND",
            "pace": "PACE_CHANGED",
            "periodic": "PERIODIC_REVIEW",
        }[kind]
    )
    assert context.plan_history[-1].source == "local_compilation"
    assert requested.call_args.args[0].plan.version == context.plan.version


@pytest.mark.asyncio
async def test_local_compilation_does_not_starve_periodic_review_or_erase_cooldown_on_reload():
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    question = initial.first_action.question
    context.question_history.append(
        InterviewHistoryEntry(question=question, feedback=feedback(question))
    )
    context.last_planner_request_question_index = 1
    context.last_replan_question_index = 3  # A later local compilation is not a model request.
    context.state.question_index = 5
    restored = InterviewContext.model_validate(context.model_dump())
    coordinator = BackgroundReplanner(service._interview_planner)
    await service._interview_planner.review(restored, defer=coordinator.request)
    assert restored.pending_replan_trigger == "PERIODIC_REVIEW"
    assert restored.last_planner_request_question_index == 5
    assert (
        InterviewContext.model_validate(restored.model_dump()).last_planner_request_question_index
        == 5
    )
    requested = Mock()
    restored.state.question_index += 1
    restored.recent_feedback = []
    await service._interview_planner.review(restored, defer=requested)
    requested.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_still_preserves_known_objectives_and_does_not_complete_them():
    service, repo, _, initial = await setup(PlannerLLM())
    context = await repo.get_interview_context(initial.interview_id)
    item = context.plan.topics[0]
    context.topic_progress[item.topic_key].coverage_status = "partial"

    class TimeoutProvider:
        async def generate_structured(self, **kwargs):
            raise TimeoutError("Injected timeout")

    await InterviewPlannerAgent(TimeoutProvider(), load_agent_settings()).revise(
        context, "PERIODIC_REVIEW"
    )
    assert context.plan_history[-1].fallback_used
    assert context.plan.topics[0].objective == item.objective
    assert context.topic_progress[item.topic_key].coverage_status == "partial"
    assert context.state.status == "active"
