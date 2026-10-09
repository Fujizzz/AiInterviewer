"""Exercise agenda execution, replanning, clocks and safety ceilings through the service."""

import pytest
from pydantic import ValidationError

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from shared.contracts import AnswerAnalysis, CandidateAnswer, EvaluationFeedback, ObjectiveCoverage
from shared.contracts.planning import PlanDraft, PlanProposal, TopicAllocation
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter


class Clock:
    value = 1000.0

    def __call__(self):
        return self.value


class PlannerLLM(MockLLMAdapter):
    def __init__(self, script=None):
        super().__init__()
        self.plans = []
        self.script = script

    async def generate_structured(self, *, prompt_name, payload, response_model):
        if response_model is PlanProposal:
            self.plans.append(payload)
            if self.script:
                return self.script(payload, len(self.plans))
            eligible = payload["eligible_topics"][:2]
            available = payload["remaining_seconds"] - 30
            return PlanDraft(
                topics=[
                    TopicAllocation(
                        project_id=t["project_id"],
                        topic_key=t["topic_key"],
                        objective="Assess personal implementation details",
                        completion_criteria="Personal implementation established",
                        budget_seconds=available // len(eligible),
                        expected_questions=1,
                    )
                    for t in eligible
                ],
                reserve_seconds=0,
                closing_seconds=30,
                reason="Prioritize implementation evidence",
            )
        return await super().generate_structured(
            prompt_name=prompt_name,
            payload=payload,
            response_model=response_model,
        )


async def setup(llm=None, **limits):
    clock, repo = Clock(), InMemoryRepository()
    settings = load_agent_settings()
    for key, value in limits.items():
        setattr(settings, key, value)
    settings.planning.replan_cooldown_questions = 1
    request = pipeline_request()
    request.planning_enabled = True
    service = InterviewAgentService(repository=repo, llm=llm, settings=settings, clock=clock)
    initialized = await service.initialize_interview(request)
    return service, repo, clock, initialized


def feedback(question, index=1, complete=False):
    return EvaluationFeedback(
        request_id=f"feedback-{index}",
        question_id=question.question_id,
        answer_relevance=0.8,
        evidence_strength=0.8,
        analysis=AnswerAnalysis(
            status="substantive",
            new_information=True,
            thread_complete=complete,
            missing_information=[] if complete else [f"Implementation step {index}"],
        ),
    )


def covered_feedback(question, answer_id, quote, index=1):
    result = feedback(question, index, complete=True)
    result.objective_coverage_status = "valid"
    result.objective_coverage = [
        ObjectiveCoverage(
            objective_id=question.topic_key,
            coverage_status="sufficient",
            answer_id=answer_id,
            supporting_quotes=[quote],
            supporting_segment_ids=[f"{answer_id}:s0"],
        )
    ]
    return result


@pytest.mark.asyncio
async def test_planner_selects_agenda_but_only_question_agent_writes_questions():
    llm = PlannerLLM()
    service, repo, clock, result = await setup(llm)
    first = result.first_action.question
    context = await repo.get_interview_context(result.interview_id)
    assert first.topic_key == result.plan.topics[0].topic_key
    assert first.text.endswith("?")
    assert "text" not in result.plan.topics[0].model_dump()
    assert context.plan_history[0].fallback_used is False
    assert context.state.clock_started_at == clock.value
    assert context.state.elapsed_seconds == 0
    question_payload = next(p for name, p in llm.calls if name == "question_react_v1")
    assert question_payload["dialogue_state"]["agenda"]["version"] == 1


@pytest.mark.asyncio
async def test_expected_count_is_soft_replan_adds_followup_and_replay_is_idempotent():
    llm = PlannerLLM()
    service, repo, clock, result = await setup(llm)
    question = result.first_action.question
    # Exhaust the soft topic allocation, so a semantic replan can extend its open goal.
    allocation = result.plan.topics[0].budget_seconds
    clock.value += allocation
    fb = feedback(question)
    answer = CandidateAnswer(
        interview_id=result.interview_id,
        question_id=question.question_id,
        answer_id="answer-1",
        text="Implemented a custom cache",
    )
    action = await service.apply_evaluation_feedback(result.interview_id, fb, answer=answer)
    context = await repo.get_interview_context(result.interview_id)
    assert action.question.topic_key == question.topic_key
    assert action.question.parent_question_id == question.question_id
    assert context.plan.version == 2
    assert context.topic_progress[question.topic_key].questions_asked == 2
    assert context.topic_progress[question.topic_key].elapsed_seconds == allocation
    assert context.plan.topics[0].expected_questions >= 2
    assert context.plan_history[0].version == 1
    assert context.plan_history[1].answer_id == "answer-1"
    snapshot = context.model_dump()
    clock.value += 10
    # A fresh service instance can resume from persisted clock/plan/progress.
    resumed = InterviewAgentService(repository=repo, llm=llm, clock=clock)
    replay = await resumed.apply_evaluation_feedback(result.interview_id, fb, answer=answer)
    assert replay.action_id == action.action_id
    assert (await repo.get_interview_context(result.interview_id)).model_dump() == snapshot
    next_action = await resumed.apply_evaluation_feedback(
        result.interview_id,
        covered_feedback(
            action.question,
            "answer-2",
            "I implemented the missing operation and verified it with an integration test.",
            index=2,
        ),
        answer=CandidateAnswer(
            interview_id=result.interview_id,
            question_id=action.question.question_id,
            answer_id="answer-2",
            text="I implemented the missing operation and verified it with an integration test.",
        ),
    )
    context = await repo.get_interview_context(result.interview_id)
    assert context.state.elapsed_seconds == allocation + 10
    assert next_action.question.topic_key != question.topic_key
    assert context.topic_progress[question.topic_key].status == "completed"


@pytest.mark.asyncio
async def test_closed_topic_is_not_reopened_and_failed_replan_compiles_known_fallback():
    initial = None

    def script(payload, count):
        nonlocal initial
        if count == 1:
            initial = payload["eligible_topics"][0]
        return PlanDraft(
            topics=[
                TopicAllocation(
                    project_id=initial["project_id"],
                    topic_key=initial["topic_key"],
                    objective="Assess implementation",
                    completion_criteria="Implementation established",
                    budget_seconds=100,
                    expected_questions=1,
                )
            ],
            reserve_seconds=0,
            closing_seconds=0,
            reason="Attempt to reopen completed topic",
        )

    service, repo, clock, result = await setup(PlannerLLM(script))
    clock.value += 40
    action = await service.apply_evaluation_feedback(
        result.interview_id,
        covered_feedback(
            result.first_action.question,
            "completed-answer",
            "I implemented the custom mechanism and verified its behavior.",
        ),
        answer=CandidateAnswer(
            interview_id=result.interview_id,
            question_id=result.first_action.question.question_id,
            answer_id="completed-answer",
            text="I implemented the custom mechanism and verified its behavior.",
        ),
    )
    context = await repo.get_interview_context(result.interview_id)
    assert context.plan.version == 2
    assert context.plan_history[-1].fallback_used
    assert context.topic_progress[initial["topic_key"]].status == "completed"
    assert action.type.value == "ask_question"
    assert action.question.topic_key != initial["topic_key"]


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["overbudget", "unknown", "question_text", "timeout"])
async def test_bad_initial_plan_falls_back_to_executable_agenda(problem):
    def script(payload, count):
        if problem == "timeout":
            raise TimeoutError("provider unavailable")
        topic = payload["eligible_topics"][0]
        return PlanDraft(
            topics=[
                TopicAllocation(
                    project_id=topic["project_id"],
                    topic_key="invented" if problem == "unknown" else topic["topic_key"],
                    objective="What did you build?"
                    if problem == "question_text"
                    else "Assess implementation",
                    completion_criteria="Personal implementation established",
                    budget_seconds=10000 if problem == "overbudget" else 100,
                    expected_questions=1,
                )
            ],
            reserve_seconds=0,
            closing_seconds=0,
            reason="Initial test plan",
        )

    service, repo, clock, result = await setup(PlannerLLM(script))
    context = await repo.get_interview_context(result.interview_id)
    assert context.plan_history[0].fallback_used is (problem != "overbudget")
    assert result.first_action.type.value == "ask_question"
    assert (
        sum(t.budget_seconds for t in context.plan.topics)
        + (context.plan.reserve_seconds + context.plan.closing_seconds)
        <= context.plan.duration_seconds
    )


@pytest.mark.asyncio
async def test_real_elapsed_time_exhausts_budget_without_another_question():
    service, repo, clock, result = await setup(PlannerLLM())
    clock.value += 950
    action = await service.apply_evaluation_feedback(
        result.interview_id,
        feedback(result.first_action.question),
        elapsed_seconds=1,
    )
    context = await repo.get_interview_context(result.interview_id)
    assert action.type.value == "finish"
    assert action.decision_trace.reason_code == "TIME_EXHAUSTED"
    assert context.state.elapsed_seconds == 950
    assert context.state.remaining_seconds == 0
    assert context.state.question_index == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "guard", ["max_questions", "max_questions_per_project", "max_questions_per_topic"]
)
async def test_safety_ceilings_are_enforced_independently_of_time(guard):
    service, repo, clock, result = await setup(None, **{guard: 1})
    first = result.first_action.question
    clock.value += 1
    action = await service.apply_evaluation_feedback(result.interview_id, feedback(first))
    context = await repo.get_interview_context(result.interview_id)
    assert context.state.remaining_seconds > 800
    if guard == "max_questions_per_topic":
        assert action.question.topic_key != first.topic_key
    else:
        assert action.type.value == "finish"


@pytest.mark.asyncio
async def test_question_generation_latency_cannot_publish_past_deadline():
    llm = PlannerLLM()
    service, repo, clock, result = await setup(llm)
    original = llm.generate_structured

    async def delayed(*, prompt_name, payload, response_model):
        if prompt_name == "question_react_v1":
            clock.value += 900
        return await original(
            prompt_name=prompt_name, payload=payload, response_model=response_model
        )

    llm.generate_structured = delayed
    clock.value += 10
    action = await service.apply_evaluation_feedback(
        result.interview_id,
        feedback(result.first_action.question),
    )
    context = await repo.get_interview_context(result.interview_id)
    assert action.type.value == "finish"
    assert context.state.elapsed_seconds == 910
    assert context.state.question_index == 1
    assert len(repo.questions) == 1


@pytest.mark.asyncio
async def test_planner_latency_is_removed_from_remaining_allocations():
    llm = PlannerLLM()
    service, repo, clock, result = await setup(llm)
    original = llm.generate_structured

    async def delayed(*, prompt_name, payload, response_model):
        draft = await original(
            prompt_name=prompt_name, payload=payload, response_model=response_model
        )
        if response_model is PlanProposal:
            clock.value += 40
        return draft

    llm.generate_structured = delayed
    spent = result.plan.topics[0].budget_seconds
    clock.value += spent
    await service.apply_evaluation_feedback(
        result.interview_id,
        feedback(result.first_action.question),
    )
    context = await repo.get_interview_context(result.interview_id)
    assert context.state.elapsed_seconds == spent + 40
    remaining = sum(
        max(0, item.budget_seconds - context.topic_progress[item.topic_key].elapsed_seconds)
        for item in context.plan.topics
        if context.topic_progress[item.topic_key].status in {"active", "pending"}
    )
    assert remaining + context.plan.reserve_seconds + context.plan.closing_seconds <= (
        context.plan.duration_seconds - spent - 40
    )


def test_plan_schema_rejects_question_payload():
    with pytest.raises(ValidationError):
        TopicAllocation(
            project_id="p",
            topic_key="t",
            objective="Assess ownership",
            completion_criteria="Ownership established",
            budget_seconds=120,
            expected_questions=1,
            text="What did you implement?",
        )
