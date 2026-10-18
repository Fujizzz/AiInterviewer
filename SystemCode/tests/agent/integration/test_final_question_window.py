"""Preserve live goal evidence and admit a last question above the 30-second cutoff."""

from unittest.mock import Mock

import pytest

from agents.domain.models import InterviewHistoryEntry
from agents.planning.needs import open_needs, sync_needs
from agents.planning.wire import CompactPlanProposal
from shared.contracts import CandidateAnswer, InterviewActionType
from tests.agent.integration.test_interview_planning import PlannerLLM, feedback, setup


def initial_replace_proposal(payload, count):
    return CompactPlanProposal.model_validate(
        {
            "base_plan_version": payload["base_plan_version"],
            "topics": [
                {
                    "topic_key": topic["topic_key"],
                    "objective_change": "replace",
                    "objective": "Assess the implementation and validation mechanism",
                    "completion_criteria": "Implementation and validation evidence established",
                }
                for topic in payload["eligible_topics"][:2]
            ],
            "reason": "Initial semantic goals use replace, as in the live regression",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local_budget", "failed_model", "explicit_local_replace"])
async def test_initial_replace_is_not_replayed_by_budget_or_fallback_compilation(mode):
    def script(payload, count):
        if count > 1:
            raise ValueError("Invalid planner output")
        return initial_replace_proposal(payload, count)

    llm = PlannerLLM(script)
    service, repo, clock, initial = await setup(llm)
    context = await repo.get_interview_context(initial.interview_id)
    first = initial.first_action.question
    clock.value += context.plan.topics[0].budget_seconds
    service._sync_clock(context)
    progress = context.topic_progress[first.topic_key]
    progress.coverage_status = "partial"
    progress.missing_information = ["Concrete validation assertions"]
    progress.evidence_answer_ids = ["verified-answer"]
    progress.coverage_evidence = [
        {"answer_id": "verified-answer", "supporting_quotes": ["I implemented it"]}
    ]
    progress.completion_requirements[0].coverage_status = "partial"
    progress.completion_requirements[0].evidence = list(progress.coverage_evidence)
    sync_needs(progress, first.topic_key, source_answer_id="verified-answer")
    before = {key: p.model_copy(deep=True) for key, p in context.topic_progress.items()}
    original_goals = {
        t.topic_key: (t.objective, t.completion_criteria) for t in context.plan.topics
    }
    context.question_history.append(
        InterviewHistoryEntry(question=first, feedback=feedback(first))
    )

    if mode == "local_budget":
        await service._interview_planner.review(context, defer=Mock())
        assert len(llm.plans) == 1
    elif mode == "failed_model":
        await service._interview_planner.revise(context, "PERIODIC_REVIEW")
        assert len(llm.plans) == 2
        assert context.plan_history[-1].fallback_used
    else:
        proposal = context.plan_history[0].proposal.model_copy(deep=True)
        proposal.topics[0].objective = "Accidentally replayed new text"
        service._interview_planner.accept(
            context, proposal, "ALLOCATION_REACHED", local_only=True
        )

    assert context.plan.version == 2
    assert all(t.objective_change == "preserve" for t in context.plan_history[-1].proposal.topics)
    for item in context.plan.topics:
        previous = before[item.topic_key]
        current = context.topic_progress[item.topic_key]
        assert (item.objective, item.completion_criteria) == original_goals[item.topic_key]
        assert current.objective_version == previous.objective_version
        assert current.coverage_status == previous.coverage_status
        assert current.coverage_evidence == previous.coverage_evidence
        assert current.evidence_answer_ids == previous.evidence_answer_ids
        assert current.completion_requirements == previous.completion_requirements
        assert current.information_needs == previous.information_needs
        assert current.next_need_id == previous.next_need_id
        assert open_needs(current, item.topic_key)
    # A JSON reload must keep the same valid IDs for both the used and unasked goals.
    restored = type(context).model_validate(context.model_dump(mode="json"))
    assert restored.topic_progress == context.topic_progress


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [31, 40, 90, 149, 30, 29, 20, 0])
@pytest.mark.parametrize("new_topic", [False, True])
async def test_question_start_cutoff_applies_to_followups_and_new_topics(remaining, new_topic):
    llm = PlannerLLM(initial_replace_proposal)
    service, repo, clock, initial = await setup(llm)
    first = initial.first_action.question
    clock.value += 900 - remaining
    answer = CandidateAnswer(
        interview_id=initial.interview_id,
        question_id=first.question_id,
        answer_id="answer-before-final-window",
        text="I implemented the mechanism and tested the bounded queue.",
    )
    action = await service.apply_evaluation_feedback(
        initial.interview_id, feedback(first, complete=new_topic), answer=answer
    )
    context = await repo.get_interview_context(initial.interview_id)
    assert context.question_history[-1].answer == answer
    assert context.state.remaining_seconds == remaining
    if remaining <= 30:
        assert action.type == InterviewActionType.FINISH
        assert context.state.question_index == 1
        assert action.decision_trace.reason_code == (
            "TIME_EXHAUSTED" if remaining == 0 else "INSUFFICIENT_TIME_FOR_QUESTION"
        )
    else:
        assert action.type == InterviewActionType.ASK_QUESTION
        assert context.state.question_index == 2
        assert action.question.dialogue_action == ("new_topic" if new_topic else "probe")
        assert (action.question.topic_key != first.topic_key) == new_topic
        assert context.plan.closing_seconds == 0
        assert len(llm.plans) == 1  # Near the cutoff, compilation needs no new API call.


@pytest.mark.asyncio
async def test_unpublished_question_is_not_sent_if_generation_reaches_the_cutoff():
    llm = PlannerLLM(initial_replace_proposal)
    service, repo, clock, initial = await setup(llm)
    original = llm.generate_structured

    async def delayed(*, prompt_name, payload, response_model):
        if prompt_name == "question_react_v1":
            clock.value += 20
        return await original(
            prompt_name=prompt_name, payload=payload, response_model=response_model
        )

    llm.generate_structured = delayed
    clock.value += 860  # Starts generation at 14:20; it returns at 14:40.
    action = await service.apply_evaluation_feedback(
        initial.interview_id, feedback(initial.first_action.question)
    )
    assert action.type == InterviewActionType.FINISH
    assert action.decision_trace.reason_code == "INSUFFICIENT_TIME_FOR_QUESTION"
    context = await repo.get_interview_context(initial.interview_id)
    assert context.state.elapsed_seconds == 880
    assert context.state.question_index == len(repo.questions) == 1


@pytest.mark.asyncio
async def test_last_answer_may_cross_duration_and_is_saved_once_before_finish():
    service, repo, clock, initial = await setup(PlannerLLM(initial_replace_proposal))
    first = initial.first_action.question
    clock.value += 860  # 14:20: forty seconds remain, so a final question is allowed.
    first_answer = CandidateAnswer(
        interview_id=initial.interview_id,
        question_id=first.question_id,
        answer_id="first-window-answer",
        text="I implemented the mechanism.",
    )
    final_question = await service.apply_evaluation_feedback(
        initial.interview_id, feedback(first, complete=True), answer=first_answer
    )
    assert final_question.type == InterviewActionType.ASK_QUESTION
    clock.value += 75  # 15:35: the candidate finishes after the configured duration.
    last_answer = CandidateAnswer(
        interview_id=initial.interview_id,
        question_id=final_question.question.question_id,
        answer_id="last-window-answer",
        text="I verified the failure boundary and the recovery mechanism.",
    )
    last_feedback = feedback(final_question.question, index=2, complete=True)
    finished = await service.apply_evaluation_feedback(
        initial.interview_id, last_feedback, answer=last_answer
    )
    assert finished.type == InterviewActionType.FINISH
    assert finished.decision_trace.reason_code == "TIME_EXHAUSTED"
    context = await repo.get_interview_context(initial.interview_id)
    assert context.state.elapsed_seconds == 935
    assert context.state.remaining_seconds == 0
    assert context.state.question_index == 2
    assert [h.answer.answer_id for h in context.question_history] == [
        first_answer.answer_id, last_answer.answer_id
    ]
    assert await service.apply_evaluation_feedback(
        initial.interview_id, last_feedback, answer=last_answer
    ) == finished
    replayed = await repo.get_interview_context(initial.interview_id)
    assert replayed.model_dump() == context.model_dump()
