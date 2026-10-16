"""Bound lightweight review costs without approving rejected drafts or losing state."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents.domain.models import InterviewHistoryEntry
from agents.model_calls import QualityCallBudget, quality_call_budget
from agents.orchestrator import InterviewAgentService
from agents.question.compact import quality_payload
from agents.question.prepared import PreparedUseDecision
from agents.question.quality import CompactQualityReview, QuestionQualityGate
from agents.question.react import ReactQuestionAgent
from app.adapters.assessment import RealtimeDecisionAdapter
from app.providers.llm import LLMError, OpenAILLM
from app.settings import interview_settings
from shared.contracts import InterviewActionType
from tests.agent.integration.test_prepared_questions import prepared
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.integration.test_question_react import decision, seed
from tests.agent.mocks import InMemoryRepository
from tests.agent.mocks.dialogue_output import selection_for
from tests.app.test_objective_coverage import evaluation_request, objective_repository
from tests.app.test_qwen_pdf import response


class Model:
    def __init__(self, issue=None, failure=None, delay=0):
        self.issue, self.failure, self.delay = issue, failure, delay
        self.calls = []

    async def generate_structured(self, *, prompt_name, payload, response_model):
        self.calls.append((prompt_name, deepcopy(payload)))
        if response_model is CompactQualityReview:
            await asyncio.sleep(self.delay)
            if self.failure:
                raise self.failure
            issue = {**(self.issue or {}), "question_quote": payload["candidate_question"]}
            return response_model(issues=[issue] if self.issue else [])
        if response_model is PreparedUseDecision:
            return response_model(usable=False, reason="Already covered")
        draft = (
            f"Which implementation detail numbered {len(self.calls)} "
            "did you handle in this project?"
        )
        return response_model.model_validate(
            decision(
                text=draft,
                selection=selection_for(payload)
                if "dialogue_state" in payload and "locked_intent" not in payload
                else None,
                intent_id=(payload.get("locked_intent") or {}).get("intent_id"),
            )
        )


def test_runtime_uses_relaxed_review_with_generous_timeouts(monkeypatch):
    monkeypatch.delenv("QUESTION_QUALITY_MODE", raising=False)
    settings = interview_settings()
    assert settings.question_agent.quality_mode == "relaxed"
    assert settings.question_agent.quality_timeout_seconds == 10
    assert settings.question_agent.max_quality_calls == 3
    assert settings.timeouts.evaluation_seconds == 20
    assert settings.question_validation.maximum_question_marks == 2
    monkeypatch.setenv("QUESTION_QUALITY_MODE", "strict")
    assert interview_settings().question_agent.quality_mode == "strict"


def test_verbose_review_note_is_shortened_without_repairing_the_review():
    from agents.question.quality import CompactQualityIssue

    issue = CompactQualityIssue(
        code="UNSUPPORTED_PREMISE",
        question_quote="80%",
        instruction="Remove the invented performance result. " * 30,
    )
    assert 0 < len(issue.instruction) <= 300
    assert issue.code == "UNSUPPORTED_PREMISE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issue",
    [
        dict(
            code="TOPIC_MISMATCH", scope_relation="subgoal", instruction="Prefer a different angle"
        ),
        dict(code="OVERLOADED_QUESTION", independent_requests=2, instruction="Prefer one request"),
    ],
)
async def test_reasonable_subgoals_and_method_with_reason_do_not_trigger_rewrite(issue):
    repo, _, question, _, _ = await seed()
    context = await repo.get_interview_context("pipeline-interview")
    model = Model(issue)
    result = await ReactQuestionAgent(model, interview_settings()).generate(
        question, "", interview=context
    )
    assert result.question is not None
    assert len(model.calls) == 2
    assert [p for p, _ in model.calls] == [
        "question_react_relaxed_v1",
        "question_quality_relaxed_v1",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["TOPIC_MISMATCH", "UNSUPPORTED_PREMISE"])
async def test_three_total_reviews_then_fallback_continues_without_publishing_bad_draft(code):
    repo = InMemoryRepository()
    model = Model(
        dict(code=code, scope_relation="outside_target", instruction="Remove the unrelated claim")
    )
    service = InterviewAgentService(repository=repo, llm=model, settings=interview_settings())
    result = await service.initialize_interview(pipeline_request())
    action = result.first_action
    assert action.type == InterviewActionType.ASK_QUESTION
    assert len(model.calls) == 6  # three drafts and three reviews, no fourth draft/adjudication
    rejected = {p["candidate_question"] for name, p in model.calls if "quality" in name}
    assert action.question.text not in rejected
    assert repo.decision_logs[-1].fallback_used
    context = await repo.get_interview_context("pipeline-interview")
    assert context.state.status != "finished"
    if code == "UNSUPPORTED_PREMISE":
        assert (
            action.decision_trace.details["generation_reason"] == "NEXT_TOPIC_RECOVERY_UNREVIEWED"
        )
    assert quality_call_budget.get() is None


@pytest.mark.asyncio
async def test_prepared_admission_and_dynamic_reviews_share_one_limit():
    req = pipeline_request()
    model = Model(
        dict(code="TOPIC_MISMATCH", scope_relation="outside_target", instruction="Stay on topic")
    )
    item = prepared(req).model_copy(update={"information_goal": "Explain GPU memory tradeoffs"})
    service = InterviewAgentService(
        repository=InMemoryRepository(),
        llm=model,
        settings=interview_settings(),
        prepared_questions=[item],
    )
    action = (await service.initialize_interview(req)).first_action
    assert action.type == InterviewActionType.ASK_QUESTION
    assert [n for n, _ in model.calls].count("prepared_question_use_v1") == 1
    assert [n for n, _ in model.calls].count("question_quality_relaxed_v1") == 2
    assert len(model.calls) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,delay", [(RuntimeError("bad review"), 0), (None, 0.03)])
async def test_failed_or_timed_out_review_is_not_retried_and_session_continues(failure, delay):
    settings = interview_settings()
    settings.question_agent.quality_timeout_seconds = 0.01
    model, repo = Model(failure=failure, delay=delay), InMemoryRepository()
    action = (
        await InterviewAgentService(
            repository=repo, llm=model, settings=settings
        ).initialize_interview(pipeline_request())
    ).first_action
    assert action.type == InterviewActionType.ASK_QUESTION
    assert len(model.calls) == 2
    assert repo.decision_logs[-1].fallback_used
    assert action.question.text != model.calls[1][1]["candidate_question"]


@pytest.mark.asyncio
async def test_compact_review_cites_previous_answers_once_and_keeps_explicit_unknown():
    repo, _, question, feedback, answer = await seed()
    context = await repo.get_interview_context("pipeline-interview")
    answer.text = "I cannot provide the exact private TeaCache formula."
    feedback.analysis.status = "explicit_unknown"
    context.question_history = [
        InterviewHistoryEntry(question=question, feedback=feedback, answer=answer)
    ]
    settings = interview_settings()
    payload = quality_payload(question, context, settings)
    assert json.dumps(payload).count(answer.text) == 1
    assert payload["previous_questions"][0]["status"] == "explicit_unknown"
    strict = QuestionQualityGate(None, settings).payload(question, context)
    assert len(json.dumps(payload)) < len(json.dumps(strict))

    class RepeatModel(Model):
        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs["prompt_name"])
            return kwargs["response_model"](
                issues=[
                    dict(
                        code="SEMANTIC_REPEAT",
                        question_quote=question.text,
                        instruction="Do not request this unavailable detail again",
                        comparison_question_id=question.question_id,
                        repeat_relation="same_unanswered_request",
                    )
                ]
            )

    model = RepeatModel()
    review = await QuestionQualityGate(model, settings).review(question, context)
    assert review.issues[0].code == "SEMANTIC_REPEAT"
    assert model.calls == ["question_quality_relaxed_v1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested,blocked",
    [
        ("How did you validate the alignment of the input tensors?", False),
        ("What is the private layer count in this model?", True),
    ],
)
async def test_a_local_unknown_does_not_close_other_answerable_details(requested, blocked):
    repo, _, question, feedback, answer = await seed()
    context = await repo.get_interview_context("pipeline-interview")
    limitation = "I do not know the private layer count."
    answer.text = "I verified input alignment. " + limitation
    feedback.analysis.status = "substantive"
    feedback.analysis.limitations = [limitation]
    context.question_history = [
        InterviewHistoryEntry(question=question, feedback=feedback, answer=answer)
    ]
    candidate = question.model_copy(update={"text": requested})

    class ScopeReview(Model):
        async def generate_structured(self, **kwargs):
            return kwargs["response_model"](
                issues=[
                    dict(
                        code="SEMANTIC_REPEAT",
                        question_quote=requested,
                        instruction="Avoid unavailable details",
                        comparison_question_id=question.question_id,
                        comparison_answer_quote=limitation,
                        repeat_relation="same_unanswered_request",
                    )
                ]
            )

    review = await QuestionQualityGate(ScopeReview(), interview_settings()).review(
        candidate, context
    )
    assert bool(review.issues) == blocked


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coverage,quote_case,blocked",
    [
        ("direct", "testing", True),
        ("related", "mechanism", False),
        ("missing", "mechanism", False),
        ("uncertain", "mechanism", False),
        (None, "mechanism", False),
        ("direct", "fabricated", False),
        ("direct", "empty", False),
    ],
)
async def test_answered_repeat_requires_direct_coverage_and_a_real_answer_quote(
    coverage, quote_case, blocked
):
    """Gate model findings once; related implementation context cannot prove testing coverage.

    The model is scripted, so this checks evidence/coverage handling, not semantic capability.
    Real prompt behavior is verified separately against the configured provider.
    """
    repo, _, previous, feedback, answer = await seed()
    mechanism = (
        "I used Pydantic schemas, emitted short error codes, capped retries at three, "
        "and used a fallback when attempts were exhausted."
    )
    testing = (
        "I injected malformed JSON and missing required fields, asserted three failed "
        "attempts, and checked the fallback result."
    )
    answer.text = testing if quote_case == "testing" else mechanism
    previous.text = "How did you implement schema validation and retries?"
    previous.information_goal = "Explain schema validation and retry handling"
    # Use a real initialized context and history, including the exact candidate quote.
    context = await repo.get_interview_context("pipeline-interview")
    context.question_history = [
        InterviewHistoryEntry(question=previous, feedback=feedback, answer=answer)
    ]
    candidate = previous.model_copy(
        update={
            "question_id": "followup-testing",
            "text": "How did you test invalid JSON and verify the retry and fallback behavior?",
            "information_goal": "Describe schema failure and fallback tests",
        }
    )
    quote = {
        "testing": testing,
        "mechanism": mechanism,
        "fabricated": "I ran an experiment that was never mentioned.",
        "empty": "",
    }[quote_case]

    class RepeatEvidenceModel(Model):
        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs["prompt_name"])
            return kwargs["response_model"](
                issues=[
                    dict(
                        code="SEMANTIC_REPEAT",
                        question_quote=candidate.text,
                        instruction="Avoid asking for already supplied test details",
                        comparison_question_id=previous.question_id,
                        comparison_answer_quote=quote,
                        repeat_relation="already_answered",
                        answer_coverage=coverage,
                        answer_evidence_kind="concrete_answer",
                    )
                ]
            )

    model = RepeatEvidenceModel()
    budget = QualityCallBudget(limit=3, used=2)
    token = quality_call_budget.set(budget)
    try:
        review = await QuestionQualityGate(model, interview_settings()).review(candidate, context)
        assert bool(review.issues) is blocked
        assert model.calls == ["question_quality_relaxed_v1"]
        assert budget.used == 3
    finally:
        quality_call_budget.reset(token)


@pytest.mark.asyncio
async def test_realtime_analysis_focuses_current_objective_and_preserves_omitted_goal():
    repo, req = objective_repository(), evaluation_request()
    req.answer.text = "I implemented version checks in the shared state module."
    before = deepcopy(repo.context.topic_progress)

    def model(prompt, data, schema):
        assert [o["goal"] for o in data["objectives"]] == ["Explain shared state design"]
        return schema(limits=[], relations=[], status="substantive", scope="concrete")

    result = await RealtimeDecisionAdapter(model, repo).evaluate(req)
    assert result.analysis_status == "valid"
    assert repo.context.topic_progress["latency"].__dict__ == before["latency"].__dict__


def test_realtime_json_failure_does_not_hide_a_second_provider_request():
    from app.adapters.decision import CompactAnswerDecision

    model = OpenAILLM.__new__(OpenAILLM)
    model.provider, model.model, model.options, model.request_timeout = "dashscope", "fake", {}, 20
    create = Mock(return_value=response("{}"))
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(LLMError):
        model("Analyze", {}, CompactAnswerDecision)
    assert create.call_count == 1


@pytest.mark.asyncio
async def test_shared_limit_is_not_reset_by_a_new_question_agent():
    repo, _, question, _, _ = await seed()
    context = await repo.get_interview_context("pipeline-interview")
    token = quality_call_budget.set(QualityCallBudget(limit=3, used=3))
    model = Model()
    try:
        result = await ReactQuestionAgent(model, interview_settings()).generate(
            question, "", interview=context
        )
        assert result.stop_reason == "QUALITY_CALL_LIMIT"
        assert not model.calls
    finally:
        quality_call_budget.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prior_revision",
    ["strict-v3", "prepared-v4:fdc791c15fffd3da", "prepared-v4:bcb1b5eff581ab25"],
)
async def test_previous_strict_prepared_questions_remain_usable_without_regeneration(
    prior_revision,
):
    from agents.question.prepared import accepted_review_versions, review_version

    req = pipeline_request()
    legacy = (
        next(v for v in accepted_review_versions() if v.startswith("prepared-v3:"))
        if prior_revision == "strict-v3"
        else prior_revision
    )
    assert legacy in accepted_review_versions()
    assert legacy != review_version()
    item = prepared(req, version=legacy)
    model = Model()
    service = InterviewAgentService(
        repository=InMemoryRepository(),
        settings=interview_settings(),
        llm=model,
        prepared_questions=[item],
    )
    action = (await service.initialize_interview(req)).first_action
    assert action.question.text == item.text
    assert action.decision_trace.details["generation_reason"] == "PREPARED_QUESTION"
    assert not model.calls
