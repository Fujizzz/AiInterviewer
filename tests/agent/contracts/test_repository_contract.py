import pytest

from agents.domain.errors import StateConflictError
from agents.domain.models import (
    AgentDecisionLog,
    CommitTurnRequest,
    InterviewContext,
)
from agents.ports import InterviewRepositoryPort
from shared.contracts import (
    CandidateProfile,
    Competency,
    DecisionTrace,
    InterviewAction,
    InterviewActionType,
    JobProfile,
    PlannedQuestion,
    QuestionType,
)
from tests.agent.factories import interview_plan, interview_state
from tests.agent.mocks import InMemoryRepository


async def initialized_context(repository: InMemoryRepository) -> InterviewContext:
    context = InterviewContext(
        interview_id="interview-1",
        candidate_profile=CandidateProfile(candidate_id="candidate-1"),
        job_profile=JobProfile(
            job_id="job-1",
            title="Engineer",
            competency_importance={competency: 0.8 for competency in Competency},
        ),
        plan=interview_plan(),
        state=interview_state(),
        policy_config_version="agent-policy-v1",
    )
    return await repository.initialize_interview(context)


def atomic_turn(context: InterviewContext, *, expected_version: int) -> CommitTurnRequest:
    question = PlannedQuestion(
        question_id="atomic-question",
        target_competency=Competency.DEBUGGING,
        difficulty=3,
        probe_depth=1,
        question_type=QuestionType.FAILURE_ANALYSIS,
        intent="test root-cause analysis",
        text="Describe a failure and how you identified its root cause?",
    )
    state = context.state.model_copy(deep=True)
    state.question_index += 1
    state.current_question_id = question.question_id
    state.asked_question_ids.append(question.question_id)
    action = InterviewAction(
        action_id="atomic-action",
        interview_id=context.interview_id,
        type=InterviewActionType.ASK_QUESTION,
        question=question,
        decision_trace=DecisionTrace(reason_code="ATOMIC_TEST"),
    )
    log = AgentDecisionLog(
        decision_id=action.action_id,
        interview_id=context.interview_id,
        state_version=context.state.state_version + 1,
        selected_competency=Competency.DEBUGGING,
        difficulty=3,
        probe_depth=1,
        action_type=action.type,
        reason_code="ATOMIC_TEST",
        policy_config_version=context.policy_config_version,
    )
    updated_context = context.model_copy(deep=True)
    updated_context.state = state
    return CommitTurnRequest(
        interview_id=context.interview_id,
        expected_state_version=expected_version,
        new_state=state,
        new_context=updated_context,
        question=question,
        decision_log=log,
        feedback_request_id="feedback-atomic",
        resulting_action=action,
    )


@pytest.mark.asyncio
async def test_repository_adapter_contract_and_state_version() -> None:
    repository: InterviewRepositoryPort = InMemoryRepository()
    state = interview_state()

    saved = await repository.save_state(state, expected_version=0)
    loaded = await repository.get_state(state.interview_id)

    assert saved.state_version == 1
    assert loaded == saved


@pytest.mark.asyncio
async def test_repository_rejects_stale_expected_version() -> None:
    repository = InMemoryRepository()
    state = interview_state()
    saved = await repository.save_state(state, expected_version=0)

    with pytest.raises(StateConflictError):
        await repository.save_state(saved, expected_version=0)


@pytest.mark.asyncio
async def test_repository_v11_atomic_commit_success() -> None:
    repository = InMemoryRepository()
    context = await initialized_context(repository)

    result = await repository.commit_turn(
        atomic_turn(context, expected_version=context.state.state_version)
    )

    stored = await repository.get_interview_context(context.interview_id)
    assert result.committed is True
    assert stored.contract_version == "1.1"
    assert result.contract_version == "1.1"
    assert result.state.state_version == 2
    assert "atomic-question" in repository.questions
    assert repository.question_interviews["atomic-question"] == context.interview_id
    assert repository.decision_logs[-1].decision_id == "atomic-action"
    assert stored.processed_feedback_ids == ["feedback-atomic"]
    assert (
        await repository.get_processed_feedback_action(
            context.interview_id,
            "feedback-atomic",
        )
        == result.action
    )


@pytest.mark.asyncio
async def test_repository_v11_conflict_commits_nothing() -> None:
    repository = InMemoryRepository()
    context = await initialized_context(repository)
    request = atomic_turn(context, expected_version=0)
    before = await repository.get_interview_context(context.interview_id)

    with pytest.raises(StateConflictError):
        await repository.commit_turn(request)

    assert await repository.get_interview_context(context.interview_id) == before
    assert "atomic-question" not in repository.questions
    assert repository.decision_logs == []
    assert (
        await repository.get_processed_feedback_action(
            context.interview_id,
            "feedback-atomic",
        )
        is None
    )
