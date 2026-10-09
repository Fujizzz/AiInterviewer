"""Contract regressions for intent preservation and evidence-backed review.

Verdicts are scripted: these tests verify state/evidence boundaries, not model accuracy.
"""

from copy import deepcopy

import pytest

from agents.config import load_agent_settings
from agents.domain.models import InterviewHistoryEntry
from agents.question.quality import GroundingAdjudication, QuestionQualityGate, QuestionQualityReview, ReviewEvidenceError
from agents.question.react import ReactQuestionAgent
from tests.agent.integration.test_question_react import decision, seed
from tests.agent.mocks.dialogue_output import selection_for


class IntentModel:
    def __init__(self, repair):
        self.repair = repair
        self.calls = []
        self.initial_selection = None
        self.generations = 0

    async def generate_structured(self, *, prompt_name, payload, response_model):
        self.calls.append((prompt_name, deepcopy(payload)))
        if response_model is GroundingAdjudication:
            return response_model(checks=[dict(issue_index=0, verdict="confirmed",
                relation="suggested_answer", request_quote="caching or batching",
                reason="These mechanisms were not established")])
        if response_model is QuestionQualityReview:
            if self.generations == 1:
                return response_model.model_validate(
                    {
                        "issues": [
                            {
                                "code": "ANSWER_HINT",
                                "instruction": "Remove the suggested methods.",
                                "question_quote": "caching or batching",
                                "repair_action": "remove_hint",
                            }
                        ]
                    }
                )
            return response_model(issues=[])
        self.generations += 1
        if self.generations == 1:
            self.initial_selection = selection_for(payload)
            return response_model.model_validate(
                decision(
                    text=(
                        "In this project, what implementation change did you make, "
                        "such as caching or batching?"
                    ),
                    selection=self.initial_selection,
                )
            )
        return response_model.model_validate(self.repair(payload, self.initial_selection))


@pytest.mark.asyncio
async def test_hint_repair_keeps_confirmed_intent_without_extra_selection_call():
    repo, _, question, _, _ = await seed()
    context = await repo.get_interview_context("pipeline-interview")
    question = question.model_copy(update={"intent_id": ""})
    before = context.model_dump()
    model = IntentModel(
        lambda payload, _: decision(
            text="In this project, what implementation change did you make?",
            intent_id=payload["locked_intent"]["intent_id"],
            selection=None,
        )
    )
    result = await ReactQuestionAgent(model, load_agent_settings()).generate(
        question,
        "",
        interview=context,
        autonomous=True,
    )
    assert result.stop_reason == "FINAL"
    assert result.repaired
    assert result.model_calls == 5
    assert result.repairs_used == 2
    assert result.question.information_goal == model.initial_selection["information_goal"]
    assert result.question.intent_id == result.locked_intent.intent_id
    assert result.question.need_id == result.locked_intent.need_id
    assert context.model_dump() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("information_goal", "Describe the assessment metrics instead"),
        ("topic_key", "another-topic"),
        ("project_id", "another-project"),
        ("answer_unit", "measured benchmark improvement"),
    ],
)
async def test_repair_cannot_escape_by_changing_selection(field, value):
    repo, _, question, _, _ = await seed()
    context = await repo.get_interview_context("pipeline-interview")
    question = question.model_copy(update={"intent_id": ""})
    model = IntentModel(
        lambda payload, selection: decision(
            text="What assessment metric did you use instead?",
            selection={**selection, field: value},
        )
    )
    result = await ReactQuestionAgent(model, load_agent_settings()).generate(
        question,
        "",
        interview=context,
        autonomous=True,
    )
    assert result.stop_reason == "RETARGET_REQUIRED"
    assert result.retarget_requested
    assert result.question is None
    assert result.locked_intent.information_goal == model.initial_selection["information_goal"]
    assert result.model_calls == 4  # Never review or publish the escaped draft.
    assert result.blocking_issues == ["ANSWER_HINT"]


@pytest.mark.asyncio
async def test_explicit_retarget_is_a_request_not_a_route_commit():
    repo, _, question, _, _ = await seed()
    context = await repo.get_interview_context("pipeline-interview")
    question = question.model_copy(update={"intent_id": ""})
    model = IntentModel(
        lambda payload, _: {
            "action": "request_retarget",
            "intent_id": payload["locked_intent"]["intent_id"],
            "retarget_reason": "The confirmed target is already answered.",
        }
    )
    result = await ReactQuestionAgent(model, load_agent_settings()).generate(
        question,
        "",
        interview=context,
        autonomous=True,
    )
    assert result.retarget_requested and result.question is None
    assert len(repo.questions) == 1  # Only the seed question was committed.


@pytest.fixture
def evidence_payload():
    return {
        "candidate_question": "How did you store and access shared state?",
        "previous_questions": [
            {
                "question_id": "q1",
                "text": "How did you maintain consistency?",
                "answer_id": "a1",
                "answer": "The controller validates updates before committing them.",
            }
        ],
    }


def repeat_review(payload, relation="already_answered"):
    return QuestionQualityReview.model_validate(
        {
            "issues": [
                {
                    "code": "SEMANTIC_REPEAT",
                    "instruction": "Clarify the remaining need.",
                    "question_quote": payload["candidate_question"],
                    "comparison_question_id": "q1",
                    "comparison_question_quote": "maintain consistency",
                    "comparison_answer_id": "a1",
                    "comparison_answer_quote": "validates updates",
                    "repeat_relation": relation,
                    "actual_request": "Comparison of storage and consistency scope",
                    "repair_action": "narrow_unanswered_request",
                }
            ]
        }
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("comparison_question_id", "absent"),
        ("comparison_question_quote", "invented question"),
        ("comparison_answer_id", "absent"),
        ("comparison_answer_quote", "A relational database"),
        ("question_quote", "invented current wording"),
    ],
)
def test_repeat_requires_existing_question_and_answer_evidence(evidence_payload, field, value):
    review = repeat_review(evidence_payload)
    setattr(review.issues[0], field, value)
    with pytest.raises(ReviewEvidenceError):
        review.validate_evidence(evidence_payload)


def test_narrower_unanswered_request_cannot_be_a_blocking_repeat(evidence_payload):
    with pytest.raises(ReviewEvidenceError) as error:
        repeat_review(evidence_payload, "narrower_unanswered_request").validate_evidence(
            evidence_payload
        )
    assert error.value.code == "REVIEW_CONFLICT"


def test_grounded_repeat_is_still_blocking():
    payload = {
        "candidate_question": "What database stored the shared state?",
        "previous_questions": [
            {
                "question_id": "q1",
                "text": "What database stored the shared state?",
                "answer_id": "a1",
                "answer": "The shared state was stored in PostgreSQL.",
            }
        ],
    }
    review = repeat_review(payload)
    review.issues[0].comparison_question_quote = payload["previous_questions"][0]["text"]
    review.issues[0].comparison_answer_quote = "stored in PostgreSQL"
    assert review.validate_evidence(payload).issues[0].code == "SEMANTIC_REPEAT"


@pytest.mark.parametrize("relation", ["same_target", "subgoal"])
def test_compound_parent_does_not_require_every_subgoal(evidence_payload, relation):
    review = QuestionQualityReview.model_validate(
        {
            "issues": [
                {
                    "code": "TOPIC_MISMATCH",
                    "instruction": "Also ask all the other parent factors.",
                    "question_quote": evidence_payload["candidate_question"],
                    "repair_action": "restore_intent",
                    "scope_relation": relation,
                    "actual_request": "The chosen child objective",
                }
            ]
        }
    )
    with pytest.raises(ReviewEvidenceError) as error:
        review.validate_evidence(evidence_payload)
    assert error.value.code == "REVIEW_CONFLICT"


def test_factors_of_one_rule_do_not_prove_independent_outputs():
    review = QuestionQualityReview.model_validate(
        {
            "issues": [{"code": "OVERLOADED_QUESTION", "instruction": "Split the factors."}],
            "answer_units": [
                {"request": "priority", "quote": "priority", "relation": "input_to_same_output"},
                {
                    "request": "difficulty",
                    "quote": "difficulty",
                    "relation": "input_to_same_output",
                },
            ],
        }
    )
    with pytest.raises(ReviewEvidenceError):
        review.consistent_verdict()


def test_unsupported_experience_remains_a_blocking_finding(evidence_payload):
    evidence_payload["candidate_question"] = "How did you recover from your PostgreSQL outage?"
    review = QuestionQualityReview.model_validate(
        {
            "issues": [
                {
                    "code": "UNSUPPORTED_PREMISE",
                    "instruction": "Remove the unestablished outage premise.",
                    "question_quote": "your PostgreSQL outage",
                    "repair_action": "remove_unfounded_premise",
                }
            ]
        }
    )
    assert review.validate_evidence(evidence_payload).issues[0].code == "UNSUPPORTED_PREMISE"


@pytest.mark.asyncio
async def test_review_retains_cross_thread_answers_and_marks_failed_analysis():
    repo, _, question, feedback, answer = await seed()
    context = await repo.get_interview_context("pipeline-interview")
    feedback.analysis_status = "unavailable"
    feedback.analysis.missing_information = ["untrusted failed analysis"]
    context.question_history = [
        InterviewHistoryEntry(question=question, answer=answer, feedback=feedback)
    ]
    gate = QuestionQualityGate(None, load_agent_settings())
    payload = gate.payload(question.model_copy(update={"dialogue_action": "new_topic"}), context)
    record = payload["previous_questions"][0]
    assert record["answer_id"] == answer.answer_id and record["answer"] == answer.text
    assert record["missing_information"] == []
    assert record["thread_complete"] is None
    assert record["analysis_status"] == "unavailable"
    assert payload["current_thread"] == []
