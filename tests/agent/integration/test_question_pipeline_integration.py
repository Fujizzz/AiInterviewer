import pytest

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from shared.contracts import (
    CandidateClaim,
    CandidateProfile,
    CandidateProject,
    Competency,
    InitializeInterviewRequest,
    InterviewActionType,
    InterviewStage,
    JobProfile,
    QuestionType,
    RetrievalSource,
)
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter, MockRAGAdapter


def pipeline_request(interview_id: str = "pipeline-interview") -> InitializeInterviewRequest:
    return InitializeInterviewRequest(
        interview_id=interview_id,
        candidate_profile=CandidateProfile(
            candidate_id="candidate-llm",
            projects=[
                CandidateProject(
                    project_id="llm-serving",
                    name="LLM Serving Platform",
                    domain="AI Infrastructure",
                    technologies=["PyTorch", "vLLM", "CUDA"],
                    claims=[
                        CandidateClaim(
                            claim_id="memory",
                            text="Reduced GPU memory usage",
                        ),
                        CandidateClaim(
                            claim_id="multi-gpu",
                            text="Implemented multi-GPU inference",
                        ),
                        CandidateClaim(
                            claim_id="throughput",
                            text="Improved throughput",
                        ),
                    ],
                    metrics=["latency", "throughput", "gpu_memory"],
                )
            ],
        ),
        job_profile=JobProfile(
            job_id="ai-infra",
            title="AI Infrastructure Engineer",
            domains=["AI Infrastructure"],
            competency_importance={
                Competency.TECHNICAL_DEPTH: 1.0,
                Competency.OWNERSHIP: 0.7,
                Competency.DECISION_MAKING: 0.9,
                Competency.DEBUGGING: 0.9,
                Competency.EVALUATION: 0.6,
                Competency.ADAPTABILITY: 0.8,
            },
        ),
        duration_seconds=900,
        enabled_stages=[InterviewStage.PROJECT_DEEP_DIVE],
    )


async def configure_debugging_as_priority(
    repository: InMemoryRepository,
    interview_id: str,
) -> None:
    state = await repository.get_state(interview_id)
    for competency, competency_state in state.competencies.items():
        competency_state.coverage = 0.9
        competency_state.evidence_count = 1
        if competency == Competency.DEBUGGING:
            competency_state.coverage = 0.2
            competency_state.evidence_count = 0
    await repository.save_state(state, expected_version=state.state_version)


@pytest.mark.asyncio
async def test_full_mock_question_pipeline_persists_explainable_action() -> None:
    repository = InMemoryRepository()
    rag = MockRAGAdapter(failing_sources=[RetrievalSource.TECHNICAL])
    settings = load_agent_settings()
    settings.question_agent.enabled = False  # Legacy optional RAG pipeline.
    service = InterviewAgentService(
        repository=repository,
        rag=rag,
        llm=MockLLMAdapter(),
        settings=settings,
    )
    await service.initialize_interview(pipeline_request())
    await configure_debugging_as_priority(repository, "pipeline-interview")

    action = await service.next_action("pipeline-interview")
    state = await repository.get_state("pipeline-interview")

    assert action.type == InterviewActionType.ASK_QUESTION
    assert action.question is not None
    assert "target_competency" not in action.question.model_dump()
    assert action.question.project_id == "llm-serving"
    assert action.question.topic == "Implemented multi-GPU inference"
    assert action.question.question_type == QuestionType.DESCRIPTION
    assert 1 <= action.question.difficulty <= 5
    assert action.question.text
    assert action.question.question_id in repository.questions
    assert state.current_question_id == action.question.question_id
    assert state.question_index == 2
    assert action.decision_trace.details["rag_failed_sources"] == []
    assert repository.decision_logs[-1].selected_project_id == "llm-serving"
    assert repository.decision_logs[-1].rag_sources_requested == [
        "candidate",
    ]


@pytest.mark.asyncio
async def test_redundant_debugging_plan_uses_alternate_topic() -> None:
    repository = InMemoryRepository()
    service = InterviewAgentService(
        repository=repository,
        rag=MockRAGAdapter(),
        llm=MockLLMAdapter(),
    )
    await service.initialize_interview(pipeline_request("redundancy-interview"))
    await configure_debugging_as_priority(repository, "redundancy-interview")

    first = await service.next_action("redundancy-interview")
    state = await repository.get_state("redundancy-interview")
    state.last_topic = first.question.topic if first.question is not None else None
    await repository.save_state(state, expected_version=state.state_version)
    second = await service.next_action("redundancy-interview")

    assert first.question is not None
    assert second.question is not None
    assert second.question.topic != first.question.topic
    assert second.question.topic_key != first.question.topic_key


@pytest.mark.asyncio
async def test_invalid_llm_output_repairs_once_then_uses_fallback() -> None:
    repository = InMemoryRepository()
    llm = MockLLMAdapter(failure_mode="invalid")
    service = InterviewAgentService(repository=repository, llm=llm)

    response = await service.initialize_interview(pipeline_request("fallback-interview"))

    assert response.first_action.question is not None
    assert response.first_action.question.text
    assert len(llm.calls) == 2
    assert (
        response.first_action.decision_trace.details["generation_reason"]
        == "CANDIDATE_SPECIFIC_FALLBACK"
    )


@pytest.mark.asyncio
async def test_question_ids_are_unique_across_interviews() -> None:
    repository = InMemoryRepository()
    service = InterviewAgentService(repository=repository)

    first = await service.initialize_interview(pipeline_request("interview-a"))
    second = await service.initialize_interview(pipeline_request("interview-b"))

    assert first.first_action.question is not None
    assert second.first_action.question is not None
    assert first.first_action.question.question_id != second.first_action.question.question_id
