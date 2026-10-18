"""Budget compilation, coverage and resumable objectives without live model calls."""

import pytest

from agents.config import load_agent_settings
from agents.domain.models import InterviewHistoryEntry
from agents.planning.allocation import PlanConstraintError
from agents.planning.pace import round_cost
from agents.planning.planner import InterviewPlannerAgent, execution_topics
from agents.policies.dialogue_controller import DialogueController
from agents.tracing import trace_sink
from shared.contracts import CandidateAnswer, ObjectiveCoverage
from shared.contracts.planning import PlanDraft, PlanProposal, TopicAllocation, TopicProgress
from tests.agent.integration.test_interview_planning import PlannerLLM, feedback, setup


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budgets,reserve,closing,remaining",
    [
        ([90, 120, 90, 60], 56, 60, 434),
        ([68, 30], 9, 5, 98),
        ([120, 110, 100, 90, 80, 70], 60, 45, 594),
        ([60, 60, 50, 40], 17, 45, 227),
    ],
)
async def test_four_recorded_overallocations_compile_without_another_model_call(
    budgets, reserve, closing, remaining
):
    # Budget shapes are the four rejected real outputs. IDs are fixture catalog IDs;
    # remaining values are conservative reconstructions, not original request snapshots.
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    context.state.remaining_seconds = remaining
    planner = InterviewPlannerAgent(None, load_agent_settings())
    eligible = planner._eligible(context)
    selected = list(eligible.values())[: len(budgets)]
    legacy = PlanDraft(
        topics=[
            TopicAllocation(
                project_id=item["project_id"],
                topic_key=item["topic_key"],
                objective="Assess concrete implementation",
                completion_criteria="Mechanism established",
                budget_seconds=seconds,
                expected_questions=2,
            )
            for item, seconds in zip(selected, budgets, strict=True)
        ],
        reserve_seconds=reserve,
        closing_seconds=closing,
        reason="Recorded allocation regression",
    )
    assert sum(budgets) + reserve + closing > remaining
    proposal = PlanProposal.model_validate(legacy)
    draft, adjustments = planner._compile(proposal, context, eligible)
    assert draft.topics
    if remaining == 98:
        # The requested final-round policy admits a question above thirty
        # seconds, even if the observed round estimate is longer than this.
        assert len(draft.topics) == 1
        assert draft.topics[0].budget_seconds == 98
        assert draft.closing_seconds == draft.reserve_seconds == 0
    else:
        assert all(
            t.budget_seconds >= round_cost(context, load_agent_settings()) for t in draft.topics
        )
    assert (
        sum(t.budget_seconds for t in draft.topics) + draft.reserve_seconds + draft.closing_seconds
        <= remaining
    )
    assert [t.topic_key for t in draft.topics] == [t.topic_key for t in proposal.topics][
        : len(draft.topics)
    ]
    assert adjustments


@pytest.mark.asyncio
async def test_invalid_project_identity_is_rejected_not_silently_normalized():
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    planner = InterviewPlannerAgent(None, load_agent_settings())
    proposal = PlanProposal.model_validate(
        {
            "topics": [
                {
                    "project_id": "fabricated-project",
                    "topic_key": context.plan.topics[0].topic_key,
                    "objective": "Assess implementation",
                    "completion_criteria": "Mechanism established",
                }
            ],
            "reason": "Identity failure fixture",
        }
    )
    with pytest.raises(PlanConstraintError, match="PROJECT_TOPIC_MISMATCH"):
        planner._compile(proposal, context, planner._eligible(context))


@pytest.mark.asyncio
async def test_expected_question_count_does_not_trigger_replanning():
    llm = PlannerLLM()
    service, repo, clock, result = await setup(llm)
    context = await repo.get_interview_context(result.interview_id)
    context.plan.topics[0].expected_questions = 1
    question = result.first_action.question
    context.question_history.append(
        InterviewHistoryEntry(question=question, feedback=feedback(question))
    )
    await InterviewPlannerAgent(llm, load_agent_settings()).review(context)
    assert len(llm.plans) == 1
    assert context.plan.version == 1


@pytest.mark.asyncio
async def test_moving_on_defers_gap_and_explicit_replan_resumes_without_resetting_counts():
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    first = result.first_action.question
    progress = context.topic_progress[first.topic_key]
    progress.coverage_status = "partial"
    progress.missing_information = ["Persistent storage mechanism"]
    second_topic = context.plan.topics[1]
    second = first.model_copy(
        update={
            "question_id": "second",
            "thread_id": "second",
            "topic_key": second_topic.topic_key,
            "topic": second_topic.objective,
            "information_goal": "Inspect a separate mechanism",
            "dialogue_action": "new_topic",
        }
    )
    controller = DialogueController(context)
    controller.record_question(second, closed_reason="EXECUTOR_SWITCH")
    context.state.question_index += 1
    assert progress.status == "deferred"
    assert progress.coverage_status == "partial"
    assert progress.missing_information == ["Persistent storage mechanism"]
    assert not controller.can_resume(first.topic_key)

    def script(payload, count):
        return PlanProposal.model_validate(
            {
                "topics": [
                    {
                        "project_id": first.project_id,
                        "topic_key": first.topic_key,
                        "objective": "Resolve the existing storage gap",
                        "completion_criteria": "Persistent storage mechanism explained",
                    }
                ],
                "reason": "Return to important unresolved evidence",
                "base_plan_version": payload["base_plan_version"],
            }
        )

    await InterviewPlannerAgent(PlannerLLM(script), load_agent_settings()).revise(
        context, "REVISIT_GAP"
    )
    assert controller.can_resume(first.topic_key)
    resumed = first.model_copy(
        update={
            "question_id": "resumed",
            "thread_id": "resumed",
            "information_goal": "Resolve storage gap",
        }
    )
    controller.record_question(resumed, closed_reason="REVISIT_GAP")
    assert controller.topic_questions(first.topic_key) == 2
    assert controller.project_questions(first.project_id) == 3
    assert progress.questions_asked == 2
    assert progress.resume_count == 1
    assert context.used_topic_keys.count(first.topic_key) == 1
    assert progress.coverage_status == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer_status", ["explicit_unknown", "refusal"])
async def test_terminal_answer_cannot_mark_topic_sufficient_even_with_complete_flag(answer_status):
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    question = result.first_action.question
    fb = feedback(question, complete=True)
    fb.analysis.status = answer_status
    answer = CandidateAnswer(
        interview_id=result.interview_id,
        question_id=question.question_id,
        answer_id="terminal-answer",
        text="I cannot provide that information.",
    )
    context.question_history.append(
        InterviewHistoryEntry(question=question, answer=answer, feedback=fb)
    )
    InterviewPlannerAgent(None, load_agent_settings()).feedback(context, question, fb, 10)
    progress = context.topic_progress[question.topic_key]
    assert progress.status == "skipped"
    assert progress.coverage_status != "sufficient"


@pytest.mark.asyncio
async def test_unavailable_analysis_preserves_gap_and_coverage():
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    question = result.first_action.question
    progress = context.topic_progress[question.topic_key]
    progress.missing_information = ["Storage durability"]
    progress.coverage_status = "partial"
    fb = feedback(question, complete=True).model_copy(update={"analysis_status": "unavailable"})
    InterviewPlannerAgent(None, load_agent_settings()).feedback(context, question, fb, 10)
    assert progress.status == "active"
    assert progress.coverage_status == "partial"
    assert progress.missing_information == ["Storage durability"]


@pytest.mark.asyncio
async def test_rejected_semantic_proposal_is_traced_and_old_agenda_recompiled():
    def script(payload, count):
        if count > 1:
            raise TimeoutError("Injected planner timeout")
        return PlanProposal.model_validate(
            {
                "topics": [
                    {
                        **{
                            k: payload["eligible_topics"][0][k] for k in ("project_id", "topic_key")
                        },
                        "objective": "Assess implementation",
                        "completion_criteria": "Mechanism established",
                    }
                ],
                "reason": "Initial semantic selection",
                "base_plan_version": payload["base_plan_version"],
            }
        )

    service, repo, clock, result = await setup(PlannerLLM(script))
    context = await repo.get_interview_context(result.interview_id)
    context.state.remaining_seconds = 500
    clock.value += 400
    events = []
    token = trace_sink.set(lambda event, data: events.append((event, data)))
    try:
        await service._interview_planner.revise(context, "TIME_DRIFT")
    finally:
        trace_sink.reset(token)
    assert context.plan_history[-1].fallback_used
    assert context.plan.topics[0].topic_key == result.first_action.question.topic_key
    total = sum(
        t.budget_seconds - context.topic_progress[t.topic_key].elapsed_seconds
        for t in execution_topics(context)
    )
    assert (
        total + context.plan.reserve_seconds + context.plan.closing_seconds
        <= context.state.remaining_seconds
    )
    rejected = next(data for event, data in events if event == "planning.fallback")
    assert rejected["reason_code"]
    assert any(event == "planning.requested" for event, _ in events)
    assert any(event == "planning.compiled" for event, _ in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remaining", [0, 10, 20, 29, 30, 31, 40, 59, 60, 89, 90, 98, 150, 179, 180, 200, 900]
)
async def test_allocator_respects_deadline_and_reserve_at_short_boundaries(remaining):
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    context.state.remaining_seconds = remaining
    planner = InterviewPlannerAgent(None, load_agent_settings())
    proposal = context.plan_history[0].proposal
    draft, adjustments = planner._compile(proposal, context, planner._eligible(context))
    assert (
        sum(t.budget_seconds for t in draft.topics) + draft.reserve_seconds + draft.closing_seconds
        <= remaining
    )
    if remaining <= 30:
        assert not draft.topics
    elif remaining < 180:
        # Cold-start round estimate is 120s and closing is 60s. A single last
        # question uses the remaining time, without forcing its answer to fit.
        assert len(draft.topics) == 1
        assert draft.topics[0].budget_seconds == remaining
        assert draft.closing_seconds == draft.reserve_seconds == 0
    else:
        assert draft.topics
        assert draft.closing_seconds == 60
        assert all(t.budget_seconds >= 120 for t in draft.topics)


@pytest.mark.asyncio
async def test_budget_safety_capacity_remains_hard_when_resume_is_requested():
    service, repo, clock, result = await setup(PlannerLLM(), max_questions_per_topic=1)
    context = await repo.get_interview_context(result.interview_id)
    question = result.first_action.question
    progress = context.topic_progress[question.topic_key]
    progress.status, progress.reason = "deferred", "EXECUTOR_MOVED_ON"
    planner = InterviewPlannerAgent(None, load_agent_settings())
    assert question.topic_key not in planner._eligible(context)
    assert not DialogueController(context).can_resume(question.topic_key)
    proposal = PlanProposal.model_validate(
        {
            "topics": [
                {
                    "project_id": question.project_id,
                    "topic_key": question.topic_key,
                    "objective": "Probe a remaining gap",
                    "completion_criteria": "Concrete detail established",
                }
            ],
            "reason": "Attempt to reopen a topic at its safety ceiling",
        }
    )
    with pytest.raises(PlanConstraintError, match="CLOSED_OR_EXHAUSTED_TOPIC"):
        planner._compile(proposal, context, planner._eligible(context))


@pytest.mark.asyncio
async def test_complete_flag_without_candidate_evidence_does_not_grant_coverage():
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    question = result.first_action.question
    fb = feedback(question, complete=True)
    context.question_history.append(InterviewHistoryEntry(question=question, feedback=fb))
    InterviewPlannerAgent(None, load_agent_settings()).feedback(context, question, fb, 10)
    assert context.topic_progress[question.topic_key].coverage_status == "unassessed"
    assert context.topic_progress[question.topic_key].status == "active"


@pytest.mark.asyncio
async def test_resume_counters_survive_history_truncation_and_context_reload():
    from agents.domain.models import InterviewContext

    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    first = result.first_action.question
    controller = DialogueController(context)
    second_topic = context.plan.topics[1]
    second = first.model_copy(
        update={
            "question_id": "counter-second",
            "thread_id": "counter-second",
            "topic_key": second_topic.topic_key,
            "topic": second_topic.objective,
            "information_goal": "Inspect the second target",
        }
    )
    controller.record_question(second, closed_reason="SWITCH")
    context.state.question_index += 1
    progress = context.topic_progress[first.topic_key]
    progress.status, progress.reason = "pending", "RESUMED_BY_PLAN"
    controller.record_question(
        first.model_copy(
            update={
                "question_id": "counter-resumed",
                "thread_id": "counter-resumed",
                "information_goal": "Inspect the unresolved implementation detail",
            }
        ),
        closed_reason="RESUMED_BY_PLAN",
    )
    context.state.question_index += 1
    context.question_history = []
    restored = InterviewContext.model_validate(context.model_dump())
    persisted = DialogueController(restored)
    assert persisted.topic_questions(first.topic_key) == 2
    assert persisted.project_questions(first.project_id) == 3
    assert restored.topic_progress[first.topic_key].questions_asked == 2
    assert restored.topic_progress[first.topic_key].resume_count == 1
    assert restored.state.question_index == 3


@pytest.mark.asyncio
async def test_clock_sync_is_idempotent_across_compilation_and_scope_switch():
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    first = result.first_action.question
    clock.value += 40
    service._sync_clock(context)
    service._sync_clock(context)
    first_spent = context.topic_progress[first.topic_key].elapsed_seconds
    assert first_spent == 40
    second_topic = context.plan.topics[1]
    second = first.model_copy(
        update={
            "question_id": "timed-second",
            "thread_id": "timed-second",
            "topic_key": second_topic.topic_key,
            "topic": second_topic.objective,
            "information_goal": "Inspect the next mechanism",
        }
    )
    DialogueController(context).record_question(second, closed_reason="SWITCH")
    clock.value += 15
    service._sync_clock(context)
    service._sync_clock(context)
    assert context.state.elapsed_seconds == 55
    assert context.state.remaining_seconds == 845
    assert context.topic_progress[first.topic_key].elapsed_seconds == 40
    assert context.topic_progress[second.topic_key].elapsed_seconds == 15


@pytest.mark.asyncio
async def test_replanned_unresolved_goal_can_resume_even_when_its_wording_was_asked():
    from agents.question.dialogue import DialogueSelection, resolve_selection

    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    first = result.first_action.question
    progress = context.topic_progress[first.topic_key]
    progress.coverage_status = "partial"
    progress.missing_information = [first.information_goal]
    second_topic = context.plan.topics[1]
    second = first.model_copy(
        update={
            "question_id": "scope-switch",
            "thread_id": "scope-switch",
            "topic_key": second_topic.topic_key,
            "topic": second_topic.objective,
            "information_goal": "Inspect a different mechanism",
        }
    )
    controller = DialogueController(context)
    controller.record_question(second, closed_reason="SWITCH")
    context.state.question_index += 1

    def script(payload, count):
        return PlanProposal.model_validate(
            {
                "topics": [
                    {
                        "project_id": first.project_id,
                        "topic_key": first.topic_key,
                        "objective": first.information_goal,
                        "completion_criteria": "Previously missing implementation detail supplied",
                    }
                ],
                "reason": "Explicitly revisit a still unresolved information need",
                "base_plan_version": payload["base_plan_version"],
            }
        )

    await InterviewPlannerAgent(PlannerLLM(script), load_agent_settings()).revise(
        context, "REVISIT_GAP"
    )
    assert controller.can_resume(first.topic_key)
    selection = DialogueSelection(
        dialogue_action="new_topic",
        project_id=first.project_id,
        topic_key=first.topic_key,
        information_goal=first.information_goal,
        decision_summary="Return to the unresolved information need",
    )
    restored_question = resolve_selection(selection, context, load_agent_settings())
    assert restored_question.topic_key == first.topic_key
    assert restored_question.thread_id != first.thread_id
    assert controller.topic_questions(first.topic_key) == 1


@pytest.mark.asyncio
async def test_complete_narrow_answer_does_not_implicitly_complete_the_plan_objective():
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    question = result.first_action.question
    answer = CandidateAnswer(
        interview_id=result.interview_id,
        question_id=question.question_id,
        answer_id="narrow-answer",
        text="The state fields are question ID and answer ID.",
    )
    fb = feedback(question, complete=True)
    context.question_history.append(
        InterviewHistoryEntry(question=question, answer=answer, feedback=fb)
    )
    InterviewPlannerAgent(None, load_agent_settings()).feedback(context, question, fb, 10)
    assert context.topic_progress[question.topic_key].coverage_status != "sufficient"
    assert context.topic_progress[question.topic_key].status == "active"


@pytest.mark.asyncio
async def test_explicit_objective_gap_survives_complete_narrow_answer():
    service, repo, clock, result = await setup(PlannerLLM())
    context = await repo.get_interview_context(result.interview_id)
    question = result.first_action.question
    answer = CandidateAnswer(
        interview_id=result.interview_id,
        question_id=question.question_id,
        answer_id="objective-answer",
        text="The state fields are question ID and answer ID.",
    )
    update = ObjectiveCoverage(
        objective_id=question.topic_key,
        coverage_status="partial",
        missing_information=["How the state is durably stored and loaded"],
        supporting_segment_ids=["a1:s1"],
        supporting_quotes=[answer.text],
        answer_id=answer.answer_id,
        rationale="Fields are clear but storage is not established",
    )
    fb = feedback(question, complete=True).model_copy(
        update={
            "objective_coverage": [update],
            "objective_coverage_status": "valid",
        }
    )
    context.question_history.append(
        InterviewHistoryEntry(question=question, answer=answer, feedback=fb)
    )
    InterviewPlannerAgent(None, load_agent_settings()).feedback(context, question, fb, 10)
    progress = context.topic_progress[question.topic_key]
    assert progress.coverage_status == "partial"
    assert progress.status == "active"
    assert progress.missing_information == ["How the state is durably stored and loaded"]
    assert progress.next_need_id


def test_legacy_execution_migration_never_invents_coverage():
    progress = TopicProgress.model_validate({"status": "completed", "reason": "EXECUTOR_MOVED_ON"})
    assert progress.status == "deferred"
    assert progress.coverage_status == "unassessed"
    sufficient = TopicProgress.model_validate(
        {"status": "completed", "reason": "OBJECTIVE_COMPLETED"}
    )
    assert sufficient.coverage_status == "sufficient"


def test_semantic_schema_excludes_concrete_budgets_and_questions():
    properties = PlanProposal.model_json_schema()["$defs"]["TopicPreference"]["properties"]
    assert "budget_seconds" not in properties
    assert "expected_questions" not in properties
    assert "text" not in properties
