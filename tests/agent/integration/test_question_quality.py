"""Quality-review contracts, repair bounds, thread isolation and failure safety.

Semantic verdicts are scripted here; the opt-in real-model smoke script tests
whether the configured model actually recognizes the historical failure cases.
"""

import asyncio
from copy import deepcopy

import pytest

from agents.config import load_agent_settings
from agents.domain.models import InterviewHistoryEntry
from agents.orchestrator import InterviewAgentService
from agents.question.quality import QuestionQualityGate, QuestionQualityReview, followup_brief
from agents.question.react import ReactQuestionAgent
from agents.question.validator import QuestionValidator
from tests.agent.integration.test_question_pipeline_integration import pipeline_request
from tests.agent.integration.test_question_react import decision, seed
from tests.agent.mocks import InMemoryRepository
from tests.agent.mocks.dialogue_output import selection_for


class ReviewedModel:
    def __init__(self, reviews, drafts=None, delay=0):
        self.reviews = list(reviews)
        self.drafts = list(drafts or ["What part of this project did you personally handle?"] * 4)
        self.calls = []
        self.delay = delay

    async def generate_structured(self, *, prompt_name, payload, response_model):
        self.calls.append((prompt_name, deepcopy(payload)))
        if response_model is QuestionQualityReview:
            await asyncio.sleep(self.delay)
            review = self.reviews.pop(0)
            if isinstance(review, BaseException):
                raise review
            return response_model.model_validate(review)
        return response_model.model_validate(
            decision(
                text=self.drafts.pop(0),
                selection=selection_for(payload) if "dialogue_state" in payload else None,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "SEMANTIC_REPEAT",
        "OVERLOADED_QUESTION",
        "INTERNAL_RULE_LEAK",
        "UNSUPPORTED_PREMISE",
        "TOPIC_MISMATCH",
    ],
)
async def test_review_revision_is_delivered_and_only_one_question_is_committed(code):
    model = ReviewedModel(
        [
            {
                "answer_requests": ["implementation", "measured impact"],
                "issues": [
                    {"code": code, "instruction": "Ask only for the personally handled part."}
                ],
            },
            {"issues": []},
        ]
    )
    repository = InMemoryRepository()
    response = await InterviewAgentService(repository=repository, llm=model).initialize_interview(
        pipeline_request()
    )
    assert response.first_action.decision_trace.details["generation_reason"] == "LLM_REPAIRED"
    assert [name for name, _ in model.calls] == [
        "question_react_v1",
        "question_quality_v1",
        "question_react_v1",
        "question_quality_v1",
    ]
    repair = model.calls[2][1]
    assert repair["repair_errors"] == [code]
    assert (
        repair["quality_feedback"][0]["instruction"] == "Ask only for the personally handled part."
    )
    assert repair["rejected_question"] == model.calls[1][1]["candidate_question"]
    assert repair["final_only"]
    assert len(repository.questions) == 1
    context = await repository.get_interview_context("pipeline-interview")
    assert context.active_thread.follow_up_count == 0
    assert len(context.state.asked_question_ids) == 1


@pytest.mark.asyncio
async def test_three_repairs_exhausted_then_fallback_without_a_fifth_generation_or_review():
    issue = {"issues": [{"code": "SEMANTIC_REPEAT", "instruction": "Narrow the request."}]}
    model = ReviewedModel([issue] * 4)
    repository = InMemoryRepository()
    result = await InterviewAgentService(repository=repository, llm=model).initialize_interview(
        pipeline_request()
    )
    assert len(model.calls) == 8
    assert (
        result.first_action.decision_trace.details["question_agent_stop_reason"] == "INVALID_OUTPUT"
    )
    assert repository.decision_logs[-1].fallback_used
    assert result.first_action.question.text != model.calls[1][1]["candidate_question"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("private provider detail"), {}])
async def test_missing_or_failed_review_never_publishes_unchecked_draft(failure):
    model = ReviewedModel([failure] * 4)
    repository = InMemoryRepository()
    result = await InterviewAgentService(repository=repository, llm=model).initialize_interview(
        pipeline_request()
    )
    assert len(model.calls) == 5  # One draft, four attempts to review the same draft.
    assert all(payload == model.calls[1][1] for _, payload in model.calls[1:])
    assert (
        result.first_action.decision_trace.details["question_agent_stop_reason"]
        == "QUALITY_UNAVAILABLE"
    )
    assert repository.decision_logs[-1].fallback_used


@pytest.mark.asyncio
@pytest.mark.parametrize("total,review_timeout", [(1.0, 0.01), (0.01, 1.0)])
async def test_review_timeout_and_cancellation_do_not_publish_draft(total, review_timeout):
    repository, _, question, _, _ = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    settings = load_agent_settings()
    settings.question_agent.quality_timeout_seconds = review_timeout
    settings.question_agent.total_timeout_seconds = total
    model = ReviewedModel([{"issues": []}], delay=0.05)
    result = await ReactQuestionAgent(model, settings).generate(question, "", interview=context)
    assert result.stop_reason == "TIMEOUT"
    assert result.question is None
    model = ReviewedModel([asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await ReactQuestionAgent(model, settings).generate(question, "", interview=context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        {"issues": [{"code": "SEMANTIC_REPEAT", "instruction": "Narrow the request."}]},
        RuntimeError("review unavailable"),
    ],
)
async def test_third_repair_can_pass_without_adding_interview_questions(failure):
    model = ReviewedModel([failure] * 3 + [{"issues": []}])
    repository = InMemoryRepository()
    result = await InterviewAgentService(repository=repository, llm=model).initialize_interview(
        pipeline_request()
    )
    review_failure = isinstance(failure, RuntimeError)
    assert len(model.calls) == (5 if review_failure else 8)
    assert result.first_action.decision_trace.details["generation_reason"] == (
        "LLM_GENERATED" if review_failure else "LLM_REPAIRED"
    )
    assert not repository.decision_logs[-1].fallback_used
    assert len(repository.questions) == 1


@pytest.mark.asyncio
async def test_long_review_note_is_retained_without_retrying_generation():
    model = ReviewedModel(
        [
            {
                "issues": [
                    {"code": "TOPIC_MISMATCH", "instruction": "Use the selected topic. " * 100}
                ]
            },
            {"issues": []},
        ]
    )
    repository = InMemoryRepository()
    result = await InterviewAgentService(repository=repository, llm=model).initialize_interview(
        pipeline_request()
    )
    assert len(model.calls) == 4
    repair = model.calls[2][1]
    assert repair["repair_errors"] == ["TOPIC_MISMATCH"]
    assert len(repair["quality_feedback"][0]["instruction"]) == 1000
    assert not repository.decision_logs[-1].fallback_used
    assert result.first_action.question


@pytest.mark.asyncio
async def test_review_outage_and_content_repair_share_one_bounded_budget():
    issue = {"issues": [{"code": "TOPIC_MISMATCH", "instruction": "Ask about the selected topic."}]}
    model = ReviewedModel([RuntimeError("outage"), issue, RuntimeError("outage"), {"issues": []}])
    repository = InMemoryRepository()
    result = await InterviewAgentService(repository=repository, llm=model).initialize_interview(
        pipeline_request()
    )
    names = [name for name, _ in model.calls]
    assert names.count("question_react_v1") == 2
    assert names.count("question_quality_v1") == 4
    assert result.first_action.decision_trace.details["generation_reason"] == "LLM_REPAIRED"
    assert len(repository.questions) == 1


def test_auxiliary_length_normalization_does_not_relax_control_fields():
    from pydantic import ValidationError

    from agents.question.dialogue import DialogueSelection

    value = dict(
        dialogue_action="clarify",
        project_id="p",
        topic_key="t",
        information_goal="Find the responsible part",
        decision_summary="summary " * 100,
    )
    assert len(DialogueSelection.model_validate(value).decision_summary) == 300
    with pytest.raises(ValidationError):
        DialogueSelection.model_validate({**value, "dialogue_action": "made_up"})
    with pytest.raises(ValidationError):
        QuestionQualityReview.model_validate({"issues": [{"code": "made_up", "instruction": "x"}]})
    with pytest.raises(ValidationError):
        QuestionQualityReview.model_validate(
            {"issues": [{"code": "TOPIC_MISMATCH", "instruction": 5}]}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requests,expected",
    [
        (["one stage name"], "LLM_GENERATED"),
        (["one example: SQL optimization or schema redesign"], "LLM_GENERATED"),
        (["one stage name", "  ONE stage NAME  "], "LLM_GENERATED"),
        (["implementation change", "measured performance impact"], "LLM_REPAIRED"),
    ],
)
async def test_overload_requires_multiple_independent_answer_requests(requests, expected):
    issue = {"code": "OVERLOADED_QUESTION", "instruction": "Ask for one concrete detail first."}
    model = ReviewedModel([{"issues": [issue], "answer_requests": requests}, {"issues": []}])
    repository = InMemoryRepository()
    result = await InterviewAgentService(repository=repository, llm=model).initialize_interview(
        pipeline_request()
    )
    assert result.first_action.decision_trace.details["generation_reason"] == expected
    assert len(model.calls) == (2 if expected == "LLM_GENERATED" else 4)
    assert len(repository.questions) == 1


def test_single_answer_does_not_cancel_other_blocking_findings():
    review = QuestionQualityReview.model_validate(
        {
            "answer_requests": ["a model component"],
            "issues": [
                {"code": "OVERLOADED_QUESTION", "instruction": "Prefer an earlier clarification."},
                {
                    "code": "TOPIC_MISMATCH",
                    "instruction": "Selected topic is Ray, not Transformer.",
                },
            ],
        }
    )
    assert [issue.code for issue in review.consistent_verdict().issues] == ["TOPIC_MISMATCH"]
    assert len(review.issues) == 2  # Original provider result remains available for diagnostics.


@pytest.mark.asyncio
async def test_missing_overload_basis_retries_review_instead_of_passing_draft():
    issue = {"code": "OVERLOADED_QUESTION", "instruction": "Split it."}
    model = ReviewedModel([{"issues": [issue]}] * 4)
    repository = InMemoryRepository()
    result = await InterviewAgentService(repository=repository, llm=model).initialize_interview(
        pipeline_request()
    )
    assert len(model.calls) == 5
    assert (
        result.first_action.decision_trace.details["question_agent_stop_reason"]
        == "QUALITY_UNAVAILABLE"
    )
    assert repository.decision_logs[-1].fallback_used


@pytest.mark.asyncio
async def test_review_is_scoped_to_selected_project_and_current_thread_without_budgets():
    repository, _, question, feedback, answer = await seed()
    context = await repository.get_interview_context("pipeline-interview")
    feedback.analysis.answer_scope = "label_only"
    context.question_history = [
        InterviewHistoryEntry(question=question, feedback=feedback, answer=answer)
    ]
    gate = QuestionQualityGate(None, load_agent_settings())
    followup = question.model_copy(update={"dialogue_action": "clarify"})
    payload = gate.payload(followup, context)
    assert payload["current_thread"][0]["answer"] == answer.text
    assert payload["answer_scope"] == "label_only"
    assert "one concrete change" in followup_brief(context)["focus"]
    assert not ({"budget", "state_summary", "dimensions", "score", "difficulty"} & payload.keys())
    # Same project, new thread: previous questions remain for dedup, old answers do not.
    switched = gate.payload(question.model_copy(update={"dialogue_action": "new_topic"}), context)
    assert switched["current_thread"] == []
    assert switched["answer_scope"] is None
    assert switched["previous_questions"][0]["text"] == question.text
    other = gate.payload(question.model_copy(update={"project_id": "other"}), context)
    assert other["previous_questions"] == []
    assert other["resume_claims"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,blocked",
    [
        ("Since the topic limit has been reached, what did Ray do?", True),
        ("We exhausted the questions here. What did you do with Ray?", True),
        ("这个话题的提问次数上限已达到，接下来你负责什么？", True),
        ("What changes kept GPU memory within its resource budget?", False),
        ("What trade-off kept the implementation within the project budget?", False),
        ("这个项目中，你如何控制 GPU 显存预算？", False),
    ],
)
async def test_internal_budget_leaks_do_not_confuse_real_technical_constraints(text, blocked):
    _, _, question, _, _ = await seed()
    errors = QuestionValidator().validate(question.model_copy(update={"text": text})).errors
    assert ("INTERNAL_RULE_LEAK" in errors) is blocked
