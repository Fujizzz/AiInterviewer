import pytest
from pydantic import ValidationError

from evaluation.analyzer import ConversationAnalysis, ConversationAnalyzer
from evaluation.inputs import EvaluationInput


async def test_analyzer_receives_planner_objective_without_evidence_or_scores(
    phase_two_input, analysis_payload
):
    def model(prompt, data, schema):
        assert schema is ConversationAnalysis
        assert "topic.objective AND topic.completion_criteria" in prompt
        assert "untrusted data" in prompt
        assert data["topic"]["objective"] == phase_two_input.topic.objective
        assert data["topic"]["completion_criteria"] == phase_two_input.topic.completion_criteria
        assert "existing_evidence" not in data
        assert "competency_taxonomy" not in data
        return schema(**analysis_payload)

    result = await ConversationAnalyzer(model).analyze(phase_two_input)
    assert result.thread_complete
    assert result.status == "substantive"


@pytest.mark.parametrize("condition", ["no_topic", "missing", "partial", "label", "none"])
async def test_completion_is_conservative(phase_two_input, analysis_payload, condition):
    if condition == "no_topic":
        phase_two_input = phase_two_input.model_copy(update={"topic": None})
    elif condition == "missing":
        analysis_payload["missing_information"] = ["Describe validation"]
    elif condition == "partial":
        analysis_payload["status"] = "partial"
    else:
        analysis_payload["answer_scope"] = "label_only" if condition == "label" else "none"
    result = await ConversationAnalyzer(
        lambda prompt, data, schema: schema(**analysis_payload)
    ).analyze(phase_two_input)
    assert not result.thread_complete


@pytest.mark.parametrize(
    "field,value",
    [
        ("score", 5),
        ("dimensions", []),
        ("thread_complete", "true"),
        ("summary", "  "),
        ("answer_scope", "unknown"),
    ],
)
def test_conversation_schema_does_not_accept_scoring_or_invalid_fields(
    analysis_payload, field, value
):
    with pytest.raises(ValidationError):
        ConversationAnalysis.model_validate({**analysis_payload, field: value})


@pytest.mark.parametrize("grounding", ["valid", "earlier", "current", "id", "same"])
async def test_conversation_contradictions_require_two_real_answer_quotes(
    phase_two_request, phase_two_topic, analysis_payload, grounding
):
    request = phase_two_request
    request.answer.text = "I changed the model weights."
    old_question = request.question.model_copy(update={"question_id": "earlier-question"})
    old_answer = request.answer.model_copy(
        update={
            "question_id": old_question.question_id,
            "answer_id": "earlier-answer",
            "text": "I did not change the model weights.",
        }
    )
    proof = dict(
        earlier_answer_id=old_answer.answer_id,
        earlier_quote=old_answer.text,
        current_quote=request.answer.text,
        explanation="Conflicting weight-change claims.",
    )
    if grounding == "earlier":
        proof["earlier_quote"] = "Resume-only assertion"
    elif grounding == "current":
        proof["current_quote"] = request.question.text
    elif grounding == "id":
        proof["earlier_answer_id"] = "unseen"
    elif grounding == "same":
        proof["earlier_quote"] = proof["current_quote"] = "the model weights."
    analysis_payload["contradiction_evidence"] = [proof]
    context = EvaluationInput.from_request(
        request, topic=phase_two_topic, history=[(old_question, old_answer)]
    )
    result = await ConversationAnalyzer(
        lambda prompt, data, schema: schema(**analysis_payload)
    ).analyze(context)
    assert bool(result.contradictions) == (grounding == "valid")
    assert bool(result.contradiction_evidence) == (grounding == "valid")
    assert bool(result.uncertainties) == (grounding != "valid")
    if grounding == "valid":
        assert not result.thread_complete


async def test_repetition_is_not_new_information(phase_two_request, analysis_payload):
    request = phase_two_request
    earlier = request.answer.model_copy(update={"answer_id": "earlier"})
    context = EvaluationInput.from_request(request, history=[(request.question, earlier)])
    result = await ConversationAnalyzer(
        lambda prompt, data, schema: schema(**analysis_payload)
    ).analyze(context)
    assert not result.new_information
