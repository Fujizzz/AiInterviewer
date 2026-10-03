import pytest
from pydantic import ValidationError

from evaluation.contracts import EvidenceItem
from evaluation.inputs import EvaluationInput


def test_inputs_are_frozen_copies_and_payloads_are_allowlisted(
    phase_two_input, phase_two_request, phase_two_topic
):
    phase_two_request.question.text = "Changed question"
    phase_two_request.answer.text = "Changed answer"
    phase_two_topic.objective = "Changed objective"
    assert phase_two_input.question.text != phase_two_request.question.text
    assert phase_two_input.answer.text != phase_two_request.answer.text
    assert phase_two_input.topic.objective != phase_two_topic.objective
    with pytest.raises(ValidationError, match="frozen"):
        phase_two_input.answer.text = "Mutation"
    conversation = phase_two_input.conversation_payload()
    extraction = phase_two_input.extraction_payload()
    assert set(conversation) == {"question", "answer", "topic", "history"}
    assert set(extraction) == {"question", "answer", "history", "existing_evidence"}
    assert set(conversation["topic"]) == {
        "project_id",
        "topic_key",
        "objective",
        "completion_criteria",
    }
    assert "difficulty" not in extraction["question"]
    assert "intent" not in extraction["question"]


def test_history_is_bounded_and_filters_other_threads_projects_interviews_and_current_answer(
    phase_two_request,
):
    request = phase_two_request
    turns = []
    for index in range(12):
        question = request.question.model_copy(update={"question_id": f"q{index}"})
        answer = request.answer.model_copy(
            update={"question_id": question.question_id, "answer_id": f"a{index}"}
        )
        turns.append((question, answer))
    other_thread = request.question.model_copy(update={"thread_id": "old"})
    other_project = request.question.model_copy(update={"project_id": "other"})
    other_interview = request.answer.model_copy(update={"interview_id": "other"})
    history = [
        *turns,
        (request.question, request.answer),
        (other_thread, turns[0][1]),
        (other_project, turns[0][1]),
        (request.question, other_interview),
    ]
    context = EvaluationInput.from_request(request, history=history)
    assert [turn.answer.answer_id for turn in context.history] == [f"a{i}" for i in range(2, 12)]
    turns[-1][1].text = "Changed"
    assert context.history[-1].answer.text != "Changed"


@pytest.mark.parametrize("field", ["question_id", "interview_id"])
def test_wrong_current_answer_identity_is_rejected(phase_two_request, field):
    setattr(phase_two_request.answer, field, "other")
    with pytest.raises(ValidationError, match="current answer must belong"):
        EvaluationInput.from_request(phase_two_request)


@pytest.mark.parametrize("field", ["topic_key", "project_id"])
def test_stale_planner_objective_is_rejected(phase_two_request, phase_two_topic, field):
    setattr(phase_two_topic, field, "other")
    with pytest.raises(ValidationError, match="Planner objective"):
        EvaluationInput.from_request(phase_two_request, topic=phase_two_topic)


def test_no_thread_id_uses_question_identity(phase_two_request):
    phase_two_request.question.thread_id = ""
    assert EvaluationInput.from_request(phase_two_request).question.thread_id == "question-current"


def test_index_is_bounded_compact_and_does_not_reintroduce_current_answer(
    phase_two_request, example
):
    base = EvidenceItem.model_validate(example["result"]["evidence_items"][0])
    items = [
        base.model_copy(
            update={
                "evidence_id": f"e{i}",
                "answer_id": f"a{i}",
                "thread_id": phase_two_request.question.thread_id,
                "project_id": phase_two_request.question.project_id,
            }
        )
        for i in range(55)
    ]
    current = items[0].model_copy(update={"answer_id": phase_two_request.answer.answer_id})
    context = EvaluationInput.from_request(
        phase_two_request, existing_evidence=[*items, current, base]
    )
    assert [item.evidence_id for item in context.existing_evidence] == [
        f"e{i}" for i in range(5, 55)
    ]
    assert set(context.extraction_payload()["existing_evidence"][0]) == {
        "evidence_id",
        "answer_id",
        "thread_id",
        "project_id",
        "normalized_claim",
    }


@pytest.mark.parametrize("problem", ["current", "foreign_thread", "duplicate", "wrong_question"])
def test_direct_input_cannot_bypass_history_identity_checks(phase_two_input, problem):
    data = phase_two_input.model_dump()
    turn = dict(question=dict(data["question"]), answer=dict(data["answer"]))
    if problem != "current":
        turn["answer"]["answer_id"] = "previous"
    if problem == "foreign_thread":
        turn["question"]["thread_id"] = "other"
    if problem == "wrong_question":
        turn["answer"]["question_id"] = "other"
    data["history"] = [turn, turn] if problem == "duplicate" else [turn]
    with pytest.raises(ValidationError):
        EvaluationInput.model_validate(data)
