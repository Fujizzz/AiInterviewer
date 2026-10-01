"""Exercise the actual action loop and atomic history handoff without a provider."""

import asyncio
from copy import deepcopy

import pytest

from agents.config import load_agent_settings
from agents.domain.errors import InvalidAgentState, StateConflictError
from agents.domain.models import InterviewContext, InterviewHistoryEntry
from agents.orchestrator import InterviewAgentService
from agents.question.react import QuestionAgentDecision, ReactQuestionAgent
from shared.contracts import CandidateAnswer
from tests.agent.integration.test_phase3_hardening import ConflictOnceRepository, strong_feedback
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter


def decision(action="final", *, text="How did you validate the cache hit rate?", **kwargs):
    return {
        "action": action,
        "topic": None,
        "limit": None,
        "text": text if action == "final" else None,
        **kwargs,
    }


class ScriptedLLM:
    def __init__(self, *responses, delay=0):
        self.responses = list(responses)
        self.calls = []
        self.delay = delay

    async def generate_structured(self, *, prompt_name, payload, response_model):
        # Scripted semantic pass; quality rejection is tested separately.
        if response_model.__name__ == "QuestionQualityReview":
            return response_model(issues=[])
        self.calls.append(deepcopy(payload))
        await asyncio.sleep(self.delay)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response_model.model_validate(response)


async def seed(repository=None, settings=None):
    repository = repository or InMemoryRepository()
    service = InterviewAgentService(repository=repository, settings=settings)
    response = await service.initialize_interview(pipeline_request())
    question = response.first_action.question
    feedback = strong_feedback(question.question_id, question.difficulty)
    answer = CandidateAnswer(
        interview_id="pipeline-interview",
        question_id=question.question_id,
        answer_id="answer-1",
        text="I measured the cache hit rate.",
    )
    return repository, service, question, feedback, answer


@pytest.mark.asyncio
async def test_tools_observations_then_final_preserve_plan_and_context():
    repository, _, question, feedback, answer = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    context.question_history = [
        InterviewHistoryEntry(question=question, feedback=feedback, answer=answer)
    ]
    before = context.model_dump()
    llm = ScriptedLLM(decision("get_history", limit=3), decision("get_plan"), decision())
    result = await ReactQuestionAgent(llm, load_agent_settings()).generate(
        question, "profile data", interview=context
    )

    assert result.stop_reason == "FINAL"
    assert result.question.model_dump(exclude={"text"}) == question.model_dump(exclude={"text"})
    history = llm.calls[1]["observations"][0]["result"]["entries"][0]
    assert history["answer"]["text"] == answer.text
    assert history["question"]["question_id"] == feedback.question_id
    assert llm.calls[2]["observations"][1]["result"]["plan"] == context.plan.model_dump(mode="json")
    assert llm.calls[2]["final_only"] is True
    assert context.model_dump() == before
    assert [step["action"] for step in result.steps] == ["get_history", "get_plan", "final"]


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [False, True])
async def test_writing_scope_disables_old_followup_brief_after_thread_closes(blocked):
    from tests.agent.mocks.dialogue_output import selection_for

    repository, _, question, feedback, answer = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    feedback.analysis.status = "explicit_unknown" if blocked else "partial"
    feedback.analysis.thread_complete = False
    feedback.analysis.missing_information = ["Explain the old implementation detail"]
    context.question_history = [
        InterviewHistoryEntry(question=question, feedback=feedback, answer=answer)
    ]
    before = context.model_dump()

    class ScopeModel(ScriptedLLM):
        async def generate_structured(self, *, prompt_name, payload, response_model):
            if response_model.__name__ == "QuestionQualityReview":
                return response_model(issues=[])
            self.calls.append(deepcopy(payload))
            return response_model.model_validate(decision(selection=selection_for(payload)))

    model = ScopeModel()
    result = await ReactQuestionAgent(model, load_agent_settings()).generate(
        question, "", interview=context, autonomous=True
    )
    assert result.question
    payload = model.calls[0]
    brief = payload["writing_brief"]
    assert (brief["mode"] == "open_new_scope") is blocked
    assert (brief["active_scope"] is None) is blocked
    assert (payload["followup_brief"] is None) is blocked
    assert brief["next_available_scope"]["topic_key"] != question.topic_key
    assert context.model_dump() == before


@pytest.mark.asyncio
async def test_repeated_tool_call_is_observed_and_cannot_loop_forever():
    repository, _, question, _, _ = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    llm = ScriptedLLM(*(decision("get_plan") for _ in range(3)))
    result = await ReactQuestionAgent(llm, load_agent_settings()).generate(
        question, "", interview=context
    )
    assert result.question is None
    assert result.stop_reason == "TOOL_LIMIT"
    assert len(llm.calls) == 3
    assert llm.calls[-1]["observations"][-1]["result"]["error"] == "REPEATED_TOOL_CALL"


@pytest.mark.asyncio
async def test_shared_agent_keeps_concurrent_interview_observations_isolated():
    repository, _, question, _, _ = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    other = context.model_copy(deep=True)
    other.interview_id = "other-interview"
    other.plan.interview_id = "other-interview"
    other.state.interview_id = "other-interview"

    class PlanReader:
        async def generate_structured(self, *, prompt_name, payload, response_model):
            # Scripted semantic pass; quality rejection is tested separately.
            if response_model.__name__ == "QuestionQualityReview":
                return response_model(issues=[])
            await asyncio.sleep(0)
            if not payload["observations"]:
                return response_model.model_validate(decision("get_plan"))
            plan = payload["observations"][0]["result"]["plan"]
            return response_model.model_validate(
                decision(text=f"What did you personally implement in {plan['interview_id']}?")
            )

    agent = ReactQuestionAgent(PlanReader(), load_agent_settings())
    first, second = await asyncio.gather(
        agent.generate(question, "", interview=context),
        agent.generate(question, "", interview=other),
    )
    assert "pipeline-interview" in first.question.text
    assert "other-interview" in second.question.text
    assert len(first.steps) == len(second.steps) == 2


@pytest.mark.asyncio
async def test_total_deadline_covers_successive_tool_and_model_rounds():
    repository, _, question, _, _ = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    settings = load_agent_settings()
    settings.question_agent.total_timeout_seconds = 0.1
    settings.timeouts.llm_generation_seconds = 1
    llm = ScriptedLLM(decision("get_plan"), decision(), delay=0.07)
    result = await ReactQuestionAgent(llm, settings).generate(question, "", interview=context)
    assert result.stop_reason == "TIMEOUT"
    assert result.question is None
    assert result.steps == [{"action": "get_plan", "status": "OK", "result_count": 0}]


@pytest.mark.asyncio
async def test_invalid_action_and_history_limit_return_bounded_feedback():
    repository, _, question, _, _ = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    llm = ScriptedLLM(decision("unknown"), decision("get_history", limit=50), decision())
    result = await ReactQuestionAgent(llm, load_agent_settings()).generate(
        question, "", interview=context
    )
    assert result.question is not None
    assert llm.calls[1]["repair_errors"] == [
        "INVALID_DECISION:ValidationError",
        "action:literal_error",
    ]
    assert llm.calls[2]["observations"][0]["result"]["error"] == "HISTORY_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_invalid_final_is_repaired_without_changing_plan():
    repository, _, question, _, _ = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    llm = ScriptedLLM(
        decision(text="The expected answer is that you should mention caching."), decision()
    )
    result = await ReactQuestionAgent(llm, load_agent_settings()).generate(
        question, "", interview=context
    )
    assert result.question is not None
    assert result.repaired
    assert "RUBRIC_OR_EXPECTED_ANSWER_LEAK" in llm.calls[1]["repair_errors"]
    assert llm.calls[1]["final_only"]


@pytest.mark.asyncio
async def test_duplicate_question_is_rejected_and_repaired():
    repository, _, question, _, _ = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    llm = ScriptedLLM(decision(text=question.text), decision())
    result = await ReactQuestionAgent(llm, load_agent_settings()).generate(
        question, "", interview=context, previous_questions=[question]
    )
    assert result.question is not None
    assert "REPEATED_QUESTION" in llm.calls[1]["repair_errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("total,per_call", [(0.01, 1.0), (1.0, 0.01)])
async def test_timeout_commits_fallback_with_diagnostics(total, per_call):
    settings = load_agent_settings()
    settings.question_agent.total_timeout_seconds = total
    settings.timeouts.llm_generation_seconds = per_call
    llm = ScriptedLLM(decision(), delay=0.05)
    repository = InMemoryRepository()
    response = await InterviewAgentService(
        repository=repository, llm=llm, settings=settings
    ).initialize_interview(pipeline_request())
    assert response.first_action.question.text
    log = repository.decision_logs[-1]
    assert log.fallback_used
    assert log.question_agent_stop_reason == "TIMEOUT"
    assert len(repository.questions) == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_without_publishing_question():
    repository = InMemoryRepository()
    llm = ScriptedLLM(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await InterviewAgentService(repository=repository, llm=llm).initialize_interview(
            pipeline_request()
        )
    assert not repository.questions
    assert not repository.decision_logs


@pytest.mark.asyncio
async def test_history_survives_restart_and_conflict_without_duplicate_entries():
    repository, service, question, feedback, answer = await seed(ConflictOnceRepository())
    first = await service.apply_evaluation_feedback("pipeline-interview", feedback, answer=answer)
    again = await service.apply_evaluation_feedback("pipeline-interview", feedback, answer=answer)
    assert again == first
    context = await repository.get_interview_context("pipeline-interview")
    assert len(context.question_history) == 1
    assert context.question_history[0].answer == answer
    # Round-trip through JSON, as the database repository does on restart.
    assert InterviewContext.model_validate_json(context.model_dump_json()) == context
    llm = ScriptedLLM(decision("get_history", limit=1), decision())
    await InterviewAgentService(repository=repository, llm=llm).next_action("pipeline-interview")
    assert llm.calls[0]["latest_turn"]["answer"]["text"] == answer.text
    entry = llm.calls[1]["observations"][0]["result"]["entries"][0]
    assert entry["question"]["question_id"] == question.question_id
    assert entry["answer"]["answer_id"] == answer.answer_id


@pytest.mark.asyncio
async def test_failed_commit_does_not_save_history():
    repository, service, _, feedback, answer = await seed()
    before = await repository.get_interview_context("pipeline-interview")

    async def fail_commit(request):
        raise StateConflictError("forced conflict")

    repository.commit_turn = fail_commit
    with pytest.raises(StateConflictError):
        await service.apply_evaluation_feedback("pipeline-interview", feedback, answer=answer)
    assert await repository.get_interview_context("pipeline-interview") == before


@pytest.mark.asyncio
async def test_answer_cannot_cross_interviews_or_questions():
    repository, service, _, feedback, answer = await seed()
    for updates in ({"interview_id": "other"}, {"question_id": "other"}):
        with pytest.raises(InvalidAgentState):
            await service.apply_evaluation_feedback(
                "pipeline-interview", feedback, answer=answer.model_copy(update=updates)
            )
    assert not (await repository.get_interview_context("pipeline-interview")).question_history


@pytest.mark.asyncio
async def test_history_filter_truncation_and_empty_legacy_session():
    repository, _, question, feedback, answer = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    old = context.model_dump()
    old.pop("question_history")
    assert InterviewContext.model_validate(old).question_history == []
    settings = load_agent_settings()
    settings.question_agent.text_char_limit = 100
    context.question_history = [
        InterviewHistoryEntry(
            question=question,
            feedback=feedback,
            answer=answer.model_copy(update={"text": "x" * 200}),
        )
    ]
    llm = ScriptedLLM(
        decision("get_history", topic="unrelated", limit=1),
        decision("get_history", topic=question.topic.upper(), limit=1),
        decision(),
    )
    await ReactQuestionAgent(llm, settings).generate(question, "", interview=context)
    assert llm.calls[1]["observations"][0]["result"]["entries"] == []
    entry = llm.calls[2]["observations"][1]["result"]["entries"][0]
    assert entry["answer"]["text"] == "x" * 100
    assert entry["answer_truncated"]


@pytest.mark.asyncio
async def test_history_retention_and_legacy_generation_switch():
    settings = load_agent_settings()
    settings.question_agent.history_retention = 1
    settings.question_agent.enabled = False
    repository, _, question, feedback, answer = await seed(settings=settings)
    llm = MockLLMAdapter()
    service = InterviewAgentService(repository=repository, llm=llm, settings=settings)
    next_action = await service.apply_evaluation_feedback(
        "pipeline-interview", feedback, answer=answer
    )
    second = next_action.question
    second_feedback = strong_feedback(second.question_id, second.difficulty, request_id="second")
    await service.apply_evaluation_feedback("pipeline-interview", second_feedback)
    context = await repository.get_interview_context("pipeline-interview")
    assert len(context.question_history) == 1
    assert context.question_history[0].question.question_id == second.question_id
    assert context.question_history[0].answer is None
    assert all(name == "question_generator_v1" for name, _ in llm.calls)
    assert answer.text in llm.calls[0][1]["context"]


@pytest.mark.parametrize(
    "payload",
    [
        decision("unknown"),
        decision("get_history", limit=0),
        decision("get_plan", topic="change plan"),
        decision(difficulty=5),
    ],
)
def test_action_schema_rejects_unknown_tools_and_writable_plan_fields(payload):
    with pytest.raises(ValueError):
        QuestionAgentDecision.model_validate(payload)
