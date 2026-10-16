"""Retain the final review for cache recovery without increasing the three-call limit."""

from unittest.mock import Mock

import pytest

from agents.model_calls import QualityCallBudget, quality_call_budget
from agents.policies.topic_selector import TopicSelector
from agents.question.prepared import PreparedUseDecision, opening_plan, review_version
from agents.question.react import ReactQuestionAgent
from agents.tracing import trace_sink
from shared.contracts.prepared import PreparedQuestion
from tests.agent.integration.test_interview_planning import feedback, setup
from tests.agent.integration.test_relaxed_question_review import Model


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_checks", [0, 1])
@pytest.mark.parametrize("admission", ["accepted", "rejected", "unavailable"])
@pytest.mark.parametrize("code", ["UNSUPPORTED_PREMISE", "TOPIC_MISMATCH"])
async def test_failed_drafts_leave_last_review_for_recovery_cache(prior_checks, admission, code):
    service, repository, _, initial = await setup(None)
    current = initial.first_action.question
    context = await repository.get_interview_context(initial.interview_id)
    bank = []
    for project in context.candidate_profile.projects:
        for topic in TopicSelector().candidates(project):
            if topic.topic_key == current.topic_key:
                continue
            plan = opening_plan(project, topic)
            bank.append(
                PreparedQuestion(
                    cache_id=f"recovery-{len(bank)}",
                    kind="opening",
                    project_id=project.project_id,
                    topic_key=topic.topic_key,
                    topic=topic.topic,
                    text=f"How did you implement {topic.topic} in the serving project?",
                    information_goal=plan.information_goal,
                    difficulty=plan.difficulty,
                    question_type=plan.question_type,
                    review_version=review_version(),
                )
            )
    context.prepared_questions = bank
    repository.contexts[initial.interview_id] = context

    class FailingDrafts(Model):
        async def generate_structured(self, **kwargs):
            if kwargs["response_model"] is PreparedUseDecision:
                self.calls.append((kwargs["prompt_name"], kwargs["payload"]))
                if admission == "unavailable":
                    raise RuntimeError("Unavailable cache admission")
                return PreparedUseDecision(usable=admission == "accepted", reason="Test verdict")
            return await super().generate_structured(**kwargs)

    model = FailingDrafts(
        dict(code=code, scope_relation="outside_target", instruction="Remove an unrelated claim")
    )
    service._settings.question_agent.quality_mode = "relaxed"
    service._llm = model
    service._question_agent = ReactQuestionAgent(model, service._settings)
    events = []
    sink = trace_sink.set(lambda event, data: events.append((event, data)))
    budget = QualityCallBudget(limit=3, used=prior_checks, recovery_reserve=1)
    token = quality_call_budget.set(budget)
    fb = feedback(current)
    try:
        action = await service.apply_evaluation_feedback(initial.interview_id, fb)
        names = [name for name, _ in model.calls]
        assert names.count("question_quality_relaxed_v1") == 2 - prior_checks
        assert names.count("question_react_relaxed_v1") == 2 - prior_checks
        assert names.count("prepared_question_use_v1") == 1
        assert budget.used == 3
        assert any(event == "question.quality_reserved" for event, _ in events)
        saved = await repository.get_interview_context(initial.interview_id)
        assert saved.state.status == "active"
        assert action.question.topic_key != current.topic_key
        if admission == "accepted":
            assert action.decision_trace.details["generation_reason"] == "PREPARED_QUESTION"
            assert action.question.text in {item.text for item in bank}
        else:
            assert (
                action.decision_trace.details["generation_reason"]
                == "NEXT_TOPIC_RECOVERY_UNREVIEWED"
            )
        previous_calls = list(model.calls)
        replay = await service.apply_evaluation_feedback(initial.interview_id, fb)
        assert replay.action_id == action.action_id
        assert model.calls == previous_calls
    finally:
        quality_call_budget.reset(token)
        trace_sink.reset(sink)
        await service.close_background()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "used", "rejected", "stale", "closed"])
async def test_no_usable_recovery_cache_keeps_all_three_draft_reviews(case):
    from shared.contracts.planning import TopicProgress
    from tests.agent.integration.test_prepared_questions import prepared
    from tests.agent.integration.test_question_pipeline_integration import pipeline_request

    service, repo, _, initial = await setup(None)
    context = await repo.get_interview_context(initial.interview_id)
    item = prepared(pipeline_request())
    item.topic_key = "other-scope"
    context.prepared_questions = [] if case == "missing" else [item]
    if case == "used":
        context.used_prepared_question_ids = [item.cache_id]
    elif case == "rejected":
        context.rejected_prepared_questions[item.cache_id] = "REPEAT"
    elif case == "stale":
        item.review_version = "outdated"
    elif case == "closed":
        context.topic_progress[item.topic_key] = TopicProgress(status="completed")
    model = Model(dict(code="UNSUPPORTED_PREMISE", instruction="Remove an invented claim"))
    service._settings.question_agent.quality_mode = "relaxed"
    budget = QualityCallBudget(limit=3, recovery_reserve=1)
    token = quality_call_budget.set(budget)
    try:
        result = await ReactQuestionAgent(model, service._settings).generate(
            initial.first_action.question, "", interview=context
        )
        assert result.question is None
        assert budget.used == 3
        assert [name for name, _ in model.calls].count("question_quality_relaxed_v1") == 3
    finally:
        quality_call_budget.reset(token)
        await service.close_background()


@pytest.mark.asyncio
async def test_live_turn_installs_reservation_but_does_not_leak_it_after_commit():
    service, _, _, initial = await setup(None)
    observe = Mock(wraps=service._ask_question_with_budget)

    async def capture(*args, **kwargs):
        assert quality_call_budget.get().recovery_reserve == 1
        return await observe(*args, **kwargs)

    service._settings.question_agent.quality_mode = "relaxed"
    service._ask_question_with_budget = capture
    await service.apply_evaluation_feedback(
        initial.interview_id, feedback(initial.first_action.question)
    )
    assert quality_call_budget.get() is None


@pytest.mark.asyncio
async def test_rag_and_normal_generation_share_the_reserved_recovery_review():
    """A failed knowledge draft cannot spend the reserve by restarting the writer loop."""
    from tests.agent.integration.test_prepared_questions import prepared
    from tests.agent.integration.test_question_pipeline_integration import pipeline_request

    service, repo, _, initial = await setup(None)
    context = await repo.get_interview_context(initial.interview_id)
    plan = initial.first_action.question
    request = pipeline_request()
    topic = next(
        t
        for t in TopicSelector().candidates(request.candidate_profile.projects[0])
        if t.topic_key != plan.topic_key
    )
    item = prepared(request).model_copy(update={"topic_key": topic.topic_key})
    context.prepared_questions = [item]
    service._settings.question_agent.quality_mode = "relaxed"
    model = Model(dict(code="UNSUPPORTED_PREMISE", instruction="Remove an invented claim"))
    agent = ReactQuestionAgent(model, service._settings)
    budget = QualityCallBudget(limit=3, used=1, recovery_reserve=1)
    token = quality_call_budget.set(budget)
    try:
        knowledge_result = await agent.generate(plan, "", interview=context)
        assert knowledge_result.stop_reason == "QUALITY_RESERVED_FOR_RECOVERY"
        assert budget.used == 2
        calls = len(model.calls)
        normal_result = await agent.generate(plan, "", interview=context)
        assert normal_result.stop_reason == "QUALITY_RESERVED_FOR_RECOVERY"
        assert len(model.calls) == calls
        assert budget.used == 2
        assert budget.has_rejected_draft
    finally:
        quality_call_budget.reset(token)
        await service.close_background()
