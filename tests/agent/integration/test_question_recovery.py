"""Local failures must advance safely, never manufacture interview completion."""

import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from agents.config import load_agent_settings
from agents.domain.errors import ProviderUnavailable, RepositoryUnavailable, StateConflictError
from agents.model_calls import run_model_call, unavailable_provider_reason
from agents.orchestrator import InterviewAgentService
from agents.planning.wire import CompactPlanProposal
from agents.question.quality import QuestionQualityReview
from agents.question.react import QuestionAgentDecision, QuestionAgentResult
from shared.contracts import CandidateAnswer, InterviewActionType
from tests.agent.integration.test_interview_planning import Clock, feedback, setup
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter


class RejectedAgent:
    prompt_name = "question_react_v1"

    def __init__(self, issue):
        self.issue, self.calls = issue, 0

    async def generate(self, plan, *args, **kwargs):
        self.calls += 1
        return QuestionAgentResult(
            locked_intent=plan, blocking_issues=[self.issue], stop_reason="INVALID_OUTPUT"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("issue", ["UNSUPPORTED_PREMISE", "SEMANTIC_REPEAT"])
async def test_blocked_question_advances_without_replaying_unsafe_goal(issue):
    service, repo, _, initialized = await setup(None)
    current = initialized.first_action.question
    agent = RejectedAgent(issue)
    service._question_agent = agent
    fb = feedback(current, complete=False)
    answer = CandidateAnswer(
        interview_id=initialized.interview_id,
        question_id=current.question_id,
        answer_id="unknown-results",
        text="I can explain the method, but have no reliable measured results.",
    )
    action = await service.apply_evaluation_feedback(initialized.interview_id, fb, answer=answer)
    assert action.type == InterviewActionType.ASK_QUESTION
    assert action.question.topic_key != current.topic_key
    assert action.decision_trace.details["generation_reason"] == "NEXT_TOPIC_RECOVERY_UNREVIEWED"
    context = await repo.get_interview_context(initialized.interview_id)
    assert context.state.status == "active"
    assert context.state.question_index == 2
    registered = context.topic_progress[action.question.topic_key].information_needs
    assert (
        next(n for n in registered if n.need_id == action.question.need_id).target
        == action.question.information_goal
    )
    assert context.state.remaining_seconds == initialized.plan.duration_seconds
    assert context.topic_progress[current.topic_key].reason == "QUESTION_GENERATION_FAILED"
    assert context.topic_progress[current.topic_key].coverage_status != "sufficient"
    assert agent.calls == 1  # no vendor retry or second model turn
    snapshot = context.model_dump()
    assert (
        await service.apply_evaluation_feedback(initialized.interview_id, fb, answer=answer)
        == action
    )
    assert (await repo.get_interview_context(initialized.interview_id)).model_dump() == snapshot
    assert agent.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("bug"), ValueError("invalid output"), TimeoutError()]
)
async def test_generation_exception_recovers_and_feedback_commits_once(failure):
    service, repo, _, initialized = await setup(None)
    service._generate_question = AsyncMock(side_effect=failure)
    question = initialized.first_action.question
    action = await service.apply_evaluation_feedback(
        initialized.interview_id,
        feedback(question, complete=False),
    )
    assert action.type == InterviewActionType.ASK_QUESTION
    assert action.question.topic_key != question.topic_key
    assert service._generate_question.await_count == 1
    assert (await repo.get_interview_context(initialized.interview_id)).state.question_index == 2


@pytest.mark.asyncio
async def test_empty_plan_with_unasked_resume_topics_recovers_locally():
    service, repo, _, initialized = await setup(None)
    context = await repo.get_interview_context(initialized.interview_id)
    context.plan.topics = []
    context.active_thread = None
    repo.contexts[initialized.interview_id] = context
    service._interview_planner.review = AsyncMock()
    action = await service.next_action(initialized.interview_id)
    assert action.type == InterviewActionType.ASK_QUESTION
    assert action.question.topic_key not in context.used_topic_keys
    assert (await repo.get_interview_context(initialized.interview_id)).plan_history[
        -1
    ].trigger == "QUESTION_RECOVERY"


@pytest.mark.asyncio
async def test_only_resume_target_failing_uses_explicitly_hypothetical_reserve():
    request = pipeline_request("only-failing-topic")
    request.planning_enabled = True
    project = request.candidate_profile.projects[0]
    project.claims = project.claims[:1]
    project.technologies, project.metrics = [], []
    repo = InMemoryRepository()
    service = InterviewAgentService(repository=repo, settings=load_agent_settings(), clock=Clock())
    service._question_agent = RejectedAgent("UNSUPPORTED_PREMISE")
    initialized = await service.initialize_interview(request)
    action = initialized.first_action
    assert action.type == InterviewActionType.ASK_QUESTION
    assert action.question.topic_key.startswith("recovery:")
    assert "hypothetical" in action.question.text
    context = await repo.get_interview_context(request.interview_id)
    assert context.state.status == "active"
    assert context.state.question_index == 1
    assert len(context.state.asked_question_ids) == 1
    assert (
        context.topic_progress[project.project_id + ":claim:memory"].coverage_status == "unassessed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "component", ["selection", "planner", "question_plan", "context", "feedback"]
)
async def test_internal_component_failure_does_not_finish(component, monkeypatch):
    service, repo, _, initialized = await setup(None)
    if component == "selection":

        def fail(*args):
            raise RuntimeError("selection bug")

        monkeypatch.setattr("agents.orchestrator.service.choose_dialogue", fail)
    elif component == "planner":
        service._interview_planner.review = AsyncMock(side_effect=RuntimeError("planner bug"))
    elif component == "question_plan":
        service._question_planner.plan = lambda **kwargs: (_ for _ in ()).throw(
            ValueError("planning bug")
        )
    elif component == "feedback":
        service._interview_planner.feedback = lambda *args: (_ for _ in ()).throw(
            RuntimeError("feedback bug")
        )
    else:
        service._context_builder.build = lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("context bug")
        )
    action = await service.apply_evaluation_feedback(
        initialized.interview_id,
        feedback(initialized.first_action.question, complete=True),
    )
    assert action.type == InterviewActionType.ASK_QUESTION
    assert (await repo.get_interview_context(initialized.interview_id)).state.status == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["API_QUOTA_EXHAUSTED", "API_ACCESS_DENIED"])
async def test_provider_unavailable_propagates_without_publishing_finish(reason):
    service, repo, _, initialized = await setup(None)
    before = (await repo.get_interview_context(initialized.interview_id)).model_dump()
    service._generate_question = AsyncMock(side_effect=ProviderUnavailable(reason))
    with pytest.raises(ProviderUnavailable) as caught:
        await service.apply_evaluation_feedback(
            initialized.interview_id, feedback(initialized.first_action.question, complete=True)
        )
    assert caught.value.reason_code == reason
    assert (await repo.get_interview_context(initialized.interview_id)).model_dump() == before


@pytest.mark.asyncio
async def test_recovery_cas_conflict_recomputes_without_duplicate_answer_or_question():
    service, repo, _, initialized = await setup(None)
    service._question_agent = RejectedAgent("SEMANTIC_REPEAT")
    commit = repo.commit_turn

    async def conflict_once(request):
        if not getattr(conflict_once, "called", False):
            conflict_once.called = True
            raise StateConflictError("injected conflict")
        return await commit(request)

    repo.commit_turn = conflict_once
    action = await service.apply_evaluation_feedback(
        initialized.interview_id,
        feedback(initialized.first_action.question, complete=False),
    )
    assert action.type == InterviewActionType.ASK_QUESTION
    context = await repo.get_interview_context(initialized.interview_id)
    assert context.state.question_index == 2
    assert len(context.question_history) == 1
    assert len(context.processed_feedback_ids) == 1


@pytest.mark.asyncio
async def test_storage_failure_does_not_become_finish_or_fake_success():
    service, repo, _, initialized = await setup(None)
    service._question_agent = RejectedAgent("SEMANTIC_REPEAT")
    before = (await repo.get_interview_context(initialized.interview_id)).model_dump()
    repo.commit_turn = AsyncMock(side_effect=RepositoryUnavailable("database unavailable"))
    with pytest.raises(RepositoryUnavailable):
        await service.next_action(initialized.interview_id)
    assert (await repo.get_interview_context(initialized.interview_id)).model_dump() == before


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (400, {"code": "Arrearage"}, "API_QUOTA_EXHAUSTED"),
        (429, {"error": {"code": "insufficient_quota"}}, "API_QUOTA_EXHAUSTED"),
        (402, {}, "API_QUOTA_EXHAUSTED"),
        (401, {}, "API_ACCESS_DENIED"),
        (403, {}, "API_ACCESS_DENIED"),
        (429, {"code": "rate_limit_exceeded"}, None),
        (500, {}, None),
        (400, {"code": "invalid_request"}, None),
    ],
)
def test_structured_provider_failure_classification(status, body, expected):
    error = RuntimeError("sensitive provider text must not be logged")
    error.status_code, error.body = status, body
    wrapper = RuntimeError("wrapped")
    wrapper.__cause__ = error
    assert unavailable_provider_reason(wrapper) == expected


@pytest.mark.asyncio
async def test_model_call_converts_quota_to_sanitized_fatal_error():
    async def fail():
        error = RuntimeError("sensitive provider content")
        error.status_code, error.body = 400, {"code": "Arrearage"}
        raise error

    with pytest.raises(ProviderUnavailable, match="API_QUOTA_EXHAUSTED") as caught:
        await run_model_call(fail, operation="question", timeout_seconds=1)
    assert "sensitive" not in str(caught.value)


@pytest.mark.asyncio
async def test_analysis_unavailable_on_last_scope_is_not_normal_completion():
    request = pipeline_request("analysis-unavailable-last-topic")
    request.planning_enabled = True
    project = request.candidate_profile.projects[0]
    project.claims, project.technologies, project.metrics = project.claims[:1], [], []
    repo = InMemoryRepository()
    service = InterviewAgentService(repository=repo, clock=Clock())
    first = (await service.initialize_interview(request)).first_action.question
    fb = feedback(first, complete=False)
    fb.analysis_status = "unavailable"
    action = await service.apply_evaluation_feedback(request.interview_id, fb)
    assert action.type == InterviewActionType.ASK_QUESTION
    assert action.question.topic_key.startswith("recovery:")
    assert (await repo.get_interview_context(request.interview_id)).state.status == "active"


@pytest.mark.asyncio
async def test_failed_generation_at_actual_deadline_still_finishes_for_time():
    service, repo, clock, initialized = await setup(None)

    async def delayed(*args, **kwargs):
        clock.value += initialized.plan.duration_seconds
        return None, "UNSAFE_INTENT_FALLBACK_BLOCKED"

    service._generate_question = delayed
    action = await service.apply_evaluation_feedback(
        initialized.interview_id,
        feedback(initialized.first_action.question, complete=True),
    )
    assert action.type == InterviewActionType.FINISH
    assert action.decision_trace.reason_code == "TIME_EXHAUSTED"
    assert (await repo.get_interview_context(initialized.interview_id)).state.remaining_seconds == 0


@pytest.mark.asyncio
async def test_ongoing_rejections_do_not_reactivate_quarantined_target():
    service, repo, _, initialized = await setup(None, max_questions=5)
    service._question_agent = RejectedAgent("SEMANTIC_REPEAT")
    action = initialized.first_action
    seen = set()
    for index in range(4):
        seen.add(action.question.topic_key)
        action = await service.apply_evaluation_feedback(
            initialized.interview_id,
            feedback(action.question, index, complete=False),
        )
        assert action.type == InterviewActionType.ASK_QUESTION
        assert action.question.topic_key not in seen
        context = await repo.get_interview_context(initialized.interview_id)
        assert context.state.question_index == index + 2
        assert context.state.status == "active"
    final = await service.apply_evaluation_feedback(
        initialized.interview_id,
        feedback(action.question, 5, complete=True),
    )
    assert final.type == InterviewActionType.FINISH
    assert final.decision_trace.reason_code == "QUESTION_SAFETY_LIMIT"


@pytest.mark.parametrize(
    "imports",
    [
        "import agents.model_calls; import app.application",
        "import app.providers.llm; import agents.orchestrator",
    ],
)
def test_provider_errors_do_not_introduce_cold_start_import_cycles(imports):
    result = subprocess.run([sys.executable, "-c", imports], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema", [QuestionAgentDecision, QuestionQualityReview, CompactPlanProposal]
)
async def test_quota_is_not_swallowed_by_react_quality_or_planner(schema):
    class FatalProvider(MockLLMAdapter):
        async def generate_structured(self, *, prompt_name, payload, response_model):
            if response_model is schema:
                raise ProviderUnavailable("API_QUOTA_EXHAUSTED")
            return await super().generate_structured(
                prompt_name=prompt_name, payload=payload, response_model=response_model
            )

    request = pipeline_request("fatal-provider-boundary")
    request.planning_enabled = schema is CompactPlanProposal
    repo = InMemoryRepository()
    service = InterviewAgentService(repository=repo, llm=FatalProvider(), clock=Clock())
    with pytest.raises(ProviderUnavailable):
        await service.initialize_interview(request)
    assert not repo.questions
    if request.interview_id in repo.contexts:
        assert repo.contexts[request.interview_id].state.status == "active"
