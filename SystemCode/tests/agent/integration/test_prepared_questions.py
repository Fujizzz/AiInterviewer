"""Prepared content skips static review, but cannot bypass live dialogue or turn commits."""

import pytest

from agents.orchestrator import InterviewAgentService
from agents.policies.topic_selector import TopicSelector
from agents.question.prepared import PreparedUseDecision, opening_plan, review_version
from shared.contracts.prepared import PreparedQuestion
from tests.agent.integration.test_project_knowledge import answer, evidence
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.mocks import InMemoryRepository, MockLLMAdapter


def test_empty_quality_units_do_not_relax_overload_evidence_requirements():
    from agents.question.quality import QuestionQualityReview, ReviewEvidenceError

    assert (
        QuestionQualityReview.model_validate({"issues": [], "answer_units": None}).answer_units
        == []
    )
    review = QuestionQualityReview.model_validate(
        {
            "issues": [
                {
                    "code": "OVERLOADED_QUESTION",
                    "instruction": "Ask one thing",
                    "repair_action": "focus_primary_request",
                    "question_quote": "Design and measure",
                }
            ],
            "answer_units": None,
        }
    )
    with pytest.raises(ReviewEvidenceError):
        review.consistent_verdict()


def test_verbose_grounding_note_keeps_verdict_and_evidence_strict():
    from pydantic import ValidationError

    from agents.question.quality import GroundingCheck

    data = {
        "issue_index": 0,
        "verdict": "confirmed",
        "relation": "unestablished_experience",
        "request_quote": "your implementation",
        "reason": "Explanation " * 100,
    }
    check = GroundingCheck.model_validate(data)
    assert len(check.reason) == 500
    assert check.verdict == "confirmed"
    with pytest.raises(ValidationError):
        GroundingCheck.model_validate({**data, "verdict": "unsupported_verdict"})


def prepared(request, *, kind="opening", version=None):
    project = request.candidate_profile.projects[0]
    topic = TopicSelector().candidates(project)[0]
    plan = opening_plan(project, topic)
    return PreparedQuestion(
        cache_id="prepared-1" if kind == "opening" else "prepared-knowledge",
        project_id=project.project_id,
        topic_key=topic.topic_key,
        topic=topic.topic,
        kind=kind,
        text="How did you implement the GPU memory reduction in your serving project?",
        information_goal=plan.information_goal,
        difficulty=2,
        question_type=plan.question_type,
        review_version=version or review_version(),
        evidence=[evidence()] if kind == "knowledge" else [],
    )


class AdmissionLLM(MockLLMAdapter):
    def __init__(self, *, usable=True, fail_check=False):
        super().__init__()
        self.usable, self.fail_check = usable, fail_check
        self.prompts = []

    async def generate_structured(self, *, prompt_name, payload, response_model):
        self.prompts.append(prompt_name)
        if response_model is PreparedUseDecision:
            if self.fail_check:
                raise RuntimeError("offline check unavailable")
            return response_model(
                usable=self.usable, reason="Suitable" if self.usable else "Already covered"
            )
        return await super().generate_structured(
            prompt_name=prompt_name, payload=payload, response_model=response_model
        )


@pytest.mark.asyncio
async def test_prepared_opening_has_fresh_ids_and_no_generation_or_quality_call():
    request = pipeline_request()
    item = prepared(request)
    ids = []
    for _ in range(2):
        repo, llm = InMemoryRepository(), AdmissionLLM()
        service = InterviewAgentService(repository=repo, llm=llm, prepared_questions=[item])
        action = (await service.initialize_interview(request)).first_action
        assert action.question.text == item.text
        assert action.decision_trace.details["generation_reason"] == "PREPARED_QUESTION"
        assert action.decision_trace.details["fallback_used"] is False
        assert llm.prompts == []
        context = await repo.get_interview_context(request.interview_id)
        assert context.used_prepared_question_ids == [item.cache_id]
        ids.append(action.question.question_id)
    assert ids[0] != ids[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stale", "covered", "unavailable"])
async def test_prepared_rejection_returns_to_original_generator_and_full_quality(failure):
    request = pipeline_request()
    request.planning_enabled = True  # A different live objective needs a contextual admission.
    repo = InMemoryRepository()
    llm = AdmissionLLM(usable=failure != "covered", fail_check=failure == "unavailable")
    item = prepared(request, version="outdated" if failure == "stale" else None)
    service = InterviewAgentService(repository=repo, llm=llm, prepared_questions=[item])
    action = (await service.initialize_interview(request)).first_action
    assert action.question.text != item.text
    assert "question_react_v1" in llm.prompts
    assert "question_quality_v1" in llm.prompts
    context = await repo.get_interview_context(request.interview_id)
    assert item.cache_id not in context.used_prepared_question_ids
    assert item.cache_id in context.rejected_prepared_questions
    assert item.text == prepared(request).text  # Never edit shared content after a rejection.


@pytest.mark.asyncio
@pytest.mark.parametrize("loading", [False, True])
async def test_prepared_rag_uses_saved_references_without_live_rag_and_survives_restart(loading):
    from tests.agent.mocks import MockRAGAdapter

    request = pipeline_request()
    repo, llm = InMemoryRepository(), AdmissionLLM()
    rag = MockRAGAdapter() if loading else None
    if rag is not None:
        rag.is_ready = False
    item = prepared(request, kind="knowledge").model_copy(
        update={
            "text": "How does PagedAttention locate physical KV cache blocks during attention?",
            "information_goal": "Explain the underlying KV cache block lookup mechanism",
        }
    )
    service = InterviewAgentService(repository=repo, llm=llm, rag=rag, prepared_questions=[item])
    first = (await service.initialize_interview(request)).first_action.question
    restarted = InterviewAgentService(repository=repo, llm=llm, rag=rag)
    llm.prompts.clear()
    action = await answer(restarted, first)
    assert action.question.text == item.text
    assert llm.prompts == ["prepared_question_use_v1"]
    context = await repo.get_interview_context(request.interview_id)
    attempt = context.project_knowledge[first.project_id]
    assert attempt.status == "asked"
    assert attempt.chunk_ids == [evidence().chunk_id]
    assert item.cache_id in context.used_prepared_question_ids
    assert action.decision_trace.details["prepared_question_id"] == item.cache_id
    replay = await answer(restarted, first)
    assert replay.action_id == action.action_id
    if rag is not None:
        assert rag.requests == []


@pytest.mark.asyncio
async def test_prepared_use_is_not_published_when_turn_commit_fails():
    from agents.domain.errors import RepositoryUnavailable

    class RejectCommit(InMemoryRepository):
        async def commit_turn(self, request):
            raise RepositoryUnavailable("test write failed")

    request = pipeline_request()
    repo = RejectCommit()
    service = InterviewAgentService(repository=repo, prepared_questions=[prepared(request)])
    with pytest.raises(RepositoryUnavailable):
        await service.initialize_interview(request)
    assert (await repo.get_interview_context(request.interview_id)).used_prepared_question_ids == []


@pytest.mark.asyncio
async def test_followup_still_uses_dynamic_generation_and_full_quality():
    request = pipeline_request()
    repo, llm = InMemoryRepository(), AdmissionLLM()
    service = InterviewAgentService(
        repository=repo, llm=llm, prepared_questions=[prepared(request)]
    )
    first = (await service.initialize_interview(request)).first_action.question
    llm.prompts.clear()
    action = await answer(service, first)
    assert action.question is not None
    assert "question_react_v1" in llm.prompts
    assert "question_quality_v1" in llm.prompts
    assert action.decision_trace.details["prepared_question_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize(
    "case", ["accepted", "missing", "stale", "used", "rejected", "unavailable", "exhausted"]
)
async def test_recovery_prefers_cached_opening_within_the_existing_review_budget(case, locked):
    """Drive a timed-out follow-up through recovery, admission, commit and idempotent replay.

    Only the failed writer and admission model are offline doubles. Recovery must reuse a
    suitable opening once, or preserve its template fallback without restarting generation
    or resetting an already consumed three-check budget.
    """
    from agents.model_calls import QualityCallBudget, quality_call_budget
    from tests.agent.integration.test_interview_planning import feedback, setup
    from tests.agent.integration.test_question_recovery import RejectedAgent

    service, repo, _, initialized = await setup(None)
    current = initialized.first_action.question
    context = await repo.get_interview_context(initialized.interview_id)
    items = []
    for project in context.candidate_profile.projects:
        for topic in TopicSelector().candidates(project):
            if topic.topic_key == current.topic_key:
                continue
            plan = opening_plan(project, topic)
            items.append(
                PreparedQuestion(
                    cache_id=f"recovery-{len(items)}",
                    project_id=project.project_id,
                    topic_key=topic.topic_key,
                    topic=topic.topic,
                    kind="opening",
                    text=f"How did you implement {topic.topic} in your serving project?",
                    information_goal=plan.information_goal,
                    difficulty=plan.difficulty,
                    question_type=plan.question_type,
                    review_version="stale" if case == "stale" else review_version(),
                )
            )
    context.prepared_questions = [] if case == "missing" else items
    if case == "used":
        context.used_prepared_question_ids = [item.cache_id for item in items]
    repo.contexts[initialized.interview_id] = context
    model = AdmissionLLM(usable=case != "rejected", fail_check=case == "unavailable")
    service._llm = model
    service._question_agent = RejectedAgent(
        None,
        stop_reason="QUALITY_CALL_LIMIT" if case == "exhausted" else "TIMEOUT",
        locked=locked,
    )
    service._settings.question_agent.quality_mode = "relaxed"
    budget = QualityCallBudget(limit=3, used=3 if case == "exhausted" else 2)
    token = quality_call_budget.set(budget)
    fb = feedback(current, complete=False)
    try:
        action = await service.apply_evaluation_feedback(initialized.interview_id, fb)
        saved = await repo.get_interview_context(initialized.interview_id)
        assert saved.state.status == "active"
        assert saved.state.question_index == 2
        assert action.question.topic_key != current.topic_key
        assert service._question_agent.calls == 1
        assert budget.used <= 3
        assert quality_call_budget.get() is budget
        if case == "accepted":
            item = next(item for item in items if item.topic_key == action.question.topic_key)
            assert action.question.text == item.text
            assert action.decision_trace.details["generation_reason"] == "PREPARED_QUESTION"
            assert action.decision_trace.details["prepared_question_id"] == item.cache_id
            assert action.decision_trace.details["fallback_used"] is False
            assert saved.used_prepared_question_ids == [item.cache_id]
            assert budget.used == 3
            assert action.question.question_id != current.question_id
            progress = saved.topic_progress[action.question.topic_key]
            need = next(
                n for n in progress.information_needs if n.need_id == action.question.need_id
            )
            assert need.target == action.question.information_goal
        else:
            assert (
                action.decision_trace.details["generation_reason"]
                == "NEXT_TOPIC_RECOVERY_UNREVIEWED"
            )
            assert action.decision_trace.details["prepared_question_id"] is None
            assert action.question.text not in [item.text for item in items]
            if case == "exhausted":
                assert "QUALITY_CALL_LIMIT" in saved.rejected_prepared_questions.values()
        expected_checks = 1 if case in {"accepted", "rejected", "unavailable"} else 0
        assert model.prompts == ["prepared_question_use_v1"] * expected_checks
        snapshot = saved.model_dump()
        assert await service.apply_evaluation_feedback(initialized.interview_id, fb) == action
        assert (await repo.get_interview_context(initialized.interview_id)).model_dump() == snapshot
        assert service._question_agent.calls == 1
        assert len(model.prompts) == expected_checks
    finally:
        quality_call_budget.reset(token)
