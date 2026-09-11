import asyncio

import pytest

from agents.config import load_agent_settings
from agents.domain.errors import RepositoryUnavailable
from agents.domain.models import CommitTurnRequest
from agents.orchestrator import InterviewAgentService
from shared.contracts import CompetencyState, EvaluationFeedback, RetrievalSource
from tests.agent.integration.test_question_pipeline_integration import (
    configure_debugging_as_priority,
    pipeline_request,
)
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter, MockRAGAdapter


def strong_feedback(
    question_id: str,
    competency,
    difficulty: int,
    *,
    request_id: str = "phase3-feedback",
) -> EvaluationFeedback:
    return EvaluationFeedback(
        request_id=request_id,
        question_id=question_id,
        target_competency=competency,
        answer_relevance=0.95,
        evidence_strength=0.90,
        evaluation_confidence=0.90,
        rubric_level=4,
        updated_competency_state=CompetencyState(
            competency=competency,
            score=4.0,
            coverage=0.6,
            confidence=0.7,
            max_verified_difficulty=difficulty,
            evidence_count=1,
            independent_evidence_count=1,
        ),
        evidence_ids=[f"evidence-{request_id}"],
    )


@pytest.mark.asyncio
async def test_service_restart_restores_profiles_difficulty_and_feedback() -> None:
    repository = InMemoryRepository()
    service_a = InterviewAgentService(
        repository=repository,
        rag=MockRAGAdapter(),
        llm=MockLLMAdapter(),
    )
    initialized = await service_a.initialize_interview(pipeline_request("restart-interview"))
    first_question = initialized.first_action.question
    assert first_question is not None
    feedback = strong_feedback(
        first_question.question_id,
        first_question.target_competency,
        first_question.difficulty,
    )
    await service_a.apply_evaluation_feedback("restart-interview", feedback)
    persisted = await repository.get_interview_context("restart-interview")
    del service_a

    llm_b = MockLLMAdapter()
    service_b = InterviewAgentService(
        repository=repository,
        rag=MockRAGAdapter(),
        llm=llm_b,
    )
    continued = await service_b.next_action("restart-interview")

    assert continued.question is not None
    assert continued.question.project_id == "llm-serving"
    assert persisted.candidate_profile.candidate_id == "candidate-llm"
    assert persisted.job_profile.job_id == "ai-infra"
    assert persisted.current_difficulty[first_question.target_competency] == min(
        first_question.difficulty + 1,
        5,
    )
    assert persisted.recent_feedback == [feedback]
    assert persisted.state.project_visit_count["llm-serving"] >= 2
    assert "feedback competency=" in llm_b.calls[-1][1]["context"]


def test_service_has_no_interview_runtime_source_of_truth() -> None:
    service = InterviewAgentService(repository=InMemoryRepository())

    forbidden = {
        "_contexts",
        "_candidate_profiles",
        "_job_profiles",
        "_next_difficulty",
        "_recent_feedback",
        "_difficulty",
        "_feedback",
    }
    assert forbidden.isdisjoint(vars(service))


@pytest.mark.asyncio
async def test_duplicate_feedback_returns_atomic_stored_action() -> None:
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    initialized = await service.initialize_interview(pipeline_request("idempotent-interview"))
    question = initialized.first_action.question
    assert question is not None
    feedback = strong_feedback(
        question.question_id,
        question.target_competency,
        question.difficulty,
        request_id="ER001",
    )

    first = await service.apply_evaluation_feedback("idempotent-interview", feedback)
    context_after_first = await repository.get_interview_context("idempotent-interview")
    question_count = len(repository.questions)
    second = await service.apply_evaluation_feedback("idempotent-interview", feedback)

    assert second == first
    assert await repository.get_interview_context("idempotent-interview") == context_after_first
    assert len(repository.questions) == question_count
    assert context_after_first.processed_feedback_ids == ["ER001"]


class ConflictOnceRepository(InMemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.injected_conflict = False

    async def commit_turn(self, request: CommitTurnRequest):
        if request.feedback_request_id is not None and not self.injected_conflict:
            self.injected_conflict = True
            state = await self.get_state(request.interview_id)
            await self.save_state(state, expected_version=state.state_version)
        return await super().commit_turn(request)


@pytest.mark.asyncio
async def test_state_conflict_reloads_and_recomputes_feedback_once() -> None:
    repository = ConflictOnceRepository()
    service = InterviewAgentService(repository=repository)
    initialized = await service.initialize_interview(pipeline_request("conflict-interview"))
    question = initialized.first_action.question
    assert question is not None
    count_before = len(repository.questions)
    feedback = strong_feedback(
        question.question_id,
        question.target_competency,
        question.difficulty,
        request_id="conflict-feedback",
    )

    action = await service.apply_evaluation_feedback("conflict-interview", feedback)
    context = await repository.get_interview_context("conflict-interview")

    assert repository.injected_conflict is True
    assert action.question is not None
    assert len(repository.questions) == count_before + 1
    assert context.processed_feedback_ids == ["conflict-feedback"]


@pytest.mark.asyncio
async def test_partial_rag_timeout_continues_and_is_observable() -> None:
    settings = load_agent_settings()
    settings.timeouts.rag_seconds = 0.01
    repository = InMemoryRepository()
    service = InterviewAgentService(
        repository=repository,
        rag=MockRAGAdapter(
            delays_by_source={RetrievalSource.TECHNICAL: 0.03},
        ),
        llm=MockLLMAdapter(),
        settings=settings,
    )
    await service.initialize_interview(pipeline_request("partial-timeout"))
    await configure_debugging_as_priority(repository, "partial-timeout")

    action = await service.next_action("partial-timeout")
    log = repository.decision_logs[-1]

    assert action.question is not None
    assert action.decision_trace.details["rag_timeout_sources"] == ["technical"]
    assert log.failed_retrieval_sources == ["technical"]
    assert log.timeout_retrieval_sources == ["technical"]
    assert set(log.rag_sources_requested) == {"candidate", "technical", "question"}


@pytest.mark.asyncio
async def test_all_rag_timeout_uses_candidate_profile_fallback() -> None:
    settings = load_agent_settings()
    settings.timeouts.rag_seconds = 0.01
    repository = InMemoryRepository()
    service = InterviewAgentService(
        repository=repository,
        rag=MockRAGAdapter(delay_seconds=0.03),
        llm=MockLLMAdapter(),
        settings=settings,
    )

    response = await service.initialize_interview(pipeline_request("all-timeout"))
    log = repository.decision_logs[-1]

    assert response.first_action.question is not None
    assert log.failed_retrieval_sources == log.rag_sources_requested
    assert log.timeout_retrieval_sources == log.rag_sources_requested
    assert log.fallback_used is True


@pytest.mark.asyncio
async def test_llm_timeout_retries_once_then_commits_fallback() -> None:
    repository = InMemoryRepository()
    llm = MockLLMAdapter(failure_mode="timeout")
    service = InterviewAgentService(repository=repository, llm=llm)

    response = await service.initialize_interview(pipeline_request("llm-timeout"))

    assert response.first_action.question is not None
    assert len(llm.calls) == 2
    assert repository.decision_logs[-1].fallback_used is True
    assert (
        response.first_action.decision_trace.details["generation_reason"]
        == "CANDIDATE_SPECIFIC_FALLBACK"
    )


class SlowCommitRepository(InMemoryRepository):
    async def commit_turn(self, request: CommitTurnRequest):
        await asyncio.sleep(0.03)
        return await super().commit_turn(request)


@pytest.mark.asyncio
async def test_repository_timeout_never_reports_uncommitted_action() -> None:
    settings = load_agent_settings()
    settings.timeouts.repository_seconds = 0.01
    repository = SlowCommitRepository()
    service = InterviewAgentService(repository=repository, settings=settings)

    with pytest.raises(RepositoryUnavailable):
        await service.initialize_interview(pipeline_request("repository-timeout"))

    context = await repository.get_interview_context("repository-timeout")
    assert context.state.state_version == 1
    assert repository.questions == {}
    assert repository.decision_logs == []


@pytest.mark.asyncio
async def test_decision_log_has_phase3_observability_fields() -> None:
    repository = InMemoryRepository()
    service = InterviewAgentService(
        repository=repository,
        rag=MockRAGAdapter(),
        llm=MockLLMAdapter(),
    )

    await service.initialize_interview(pipeline_request("decision-log"))
    log = repository.decision_logs[-1]

    assert log.decision_id
    assert log.interview_id == "decision-log"
    assert log.state_version == 2
    assert log.action_type.value == "ask_question"
    assert log.selected_competency is not None
    assert log.selected_project == "llm-serving"
    assert log.selected_project_id == "llm-serving"
    assert log.selected_topic
    assert log.difficulty is not None
    assert log.probe_depth is not None
    assert log.rag_sources_requested
    assert log.retrieval_sources == log.rag_sources_requested
    assert isinstance(log.fallback_used, bool)
    assert log.planner_latency_ms >= 0
    assert log.retrieval_latency_ms >= 0
    assert log.generation_latency_ms >= 0
    assert log.total_agent_latency_ms >= 0
    assert log.reason_code
    assert log.planner_prompt_version == "question_planner_v1"
    assert log.generator_prompt_version == "question_generator_v1"
    assert log.policy_config_version == "agent-policy-v1"


@pytest.mark.asyncio
async def test_explicit_anchor_state_is_persisted_and_completed_by_its_feedback() -> None:
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    initialized = await service.initialize_interview(pipeline_request("anchor-state"))
    question = initialized.first_action.question
    assert question is not None
    before = await repository.get_interview_context("anchor-state")
    anchor = before.anchor_state[question.target_competency]
    assert anchor.asked is True
    assert anchor.completed is False
    assert anchor.question_id == question.question_id

    await service.apply_evaluation_feedback(
        "anchor-state",
        strong_feedback(
            question.question_id,
            question.target_competency,
            question.difficulty,
        ),
    )
    after = await repository.get_interview_context("anchor-state")
    assert after.anchor_state[question.target_competency].completed is True


@pytest.mark.asyncio
async def test_replay_recomputes_core_policy_fields_deterministically() -> None:
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)
    await service.initialize_interview(pipeline_request("replay-interview"))
    snapshot = await repository.get_interview_context("replay-interview")

    replayed = service.replay_decision(snapshot)
    await service.next_action("replay-interview")
    log = repository.decision_logs[-1]

    assert replayed.selected_competency == log.selected_competency
    assert replayed.selected_project_id == log.selected_project_id
    assert replayed.selected_topic == log.selected_topic
    assert replayed.difficulty == log.difficulty
    assert replayed.probe_depth == log.probe_depth
