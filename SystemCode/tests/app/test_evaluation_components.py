"""Real failure boundaries: independent parsing, original references, and corrections."""

from types import SimpleNamespace

import pytest

from app.adapters.evaluation import LLMEvaluationAdapter, answer_segments
from shared.contracts import CandidateAnswer, EvaluationRequest, PlannedQuestion
from tests.app.test_evaluation_recovery import output


def request(text="I implemented the cache. I measured latency."):
    question = PlannedQuestion(
        question_id="q-current",
        project_id="project",
        topic="cache",
        difficulty=2,
        probe_depth=1,
        question_type="implementation",
        intent="mechanism",
        thread_id="thread",
    )
    return EvaluationRequest(
        request_id="evaluation-current",
        interview_id="interview",
        question=question,
        answer=CandidateAnswer(
            interview_id="interview",
            question_id=question.question_id,
            answer_id="current",
            text=text,
        ),
    )


class Repository:
    def __init__(self, history=()):
        self.context = SimpleNamespace(question_history=list(history), active_thread=None)

    async def get_interview_context(self, _):
        return self.context


@pytest.mark.asyncio
async def test_bad_assessment_keeps_conversation_and_next_information_need():
    def model(prompt, data, schema):
        raw = output(quote="not a candidate statement")
        raw["analysis"].update(new_information=True, missing_information=["Eviction strategy"])
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(model, Repository()).evaluate(request())
    assert feedback.analysis_status == "valid"
    assert feedback.analysis.new_information
    assert feedback.analysis.missing_information == ["Eviction strategy"]
    assert feedback.assessment_status == "unavailable"
    assert feedback.dimensions == []
    assert "UNGROUNDED_EVIDENCE_QUOTE" in feedback.evaluation_issues


@pytest.mark.asyncio
async def test_invalid_conversation_does_not_turn_into_partial_candidate_answer():
    def model(prompt, data, schema):
        raw = output()
        raw["analysis"] = {"status": "model_broken", "thread_complete": True}
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(model, Repository()).evaluate(request())
    assert feedback.analysis_status == "unavailable"
    assert not feedback.analysis.thread_complete
    assert feedback.assessment_status == "valid"
    assert len(feedback.dimensions) == 1


@pytest.mark.asyncio
async def test_segment_references_preserve_separate_quotes_and_one_conservative_score():
    def model(prompt, data, schema):
        segments = data["answer_segments"]
        assert segments == answer_segments("current", data["answer"])
        raw = output(True)
        for index, dimension in enumerate(raw["dimensions"]):
            dimension["quote"] = ""
            dimension["source_segment_ids"] = [segments[index]["id"]]
            dimension["rubric_level"] = 5 if index == 0 else 2
            dimension["strength"] = 0.9 if index == 0 else 0.6
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(model, Repository()).evaluate(request())
    assert feedback.assessment_status == "valid"
    assert len(feedback.dimensions) == len(feedback.evidence_ids) == 1
    evidence = feedback.dimensions[0]
    assert evidence.quote == "I implemented the cache."
    assert evidence.source_quotes == ["I implemented the cache.", "I measured latency."]
    assert evidence.rubric_level == 2
    assert evidence.strength == 0.6


@pytest.mark.asyncio
async def test_unknown_segment_id_cannot_ground_invented_evidence():
    def model(prompt, data, schema):
        raw = output()
        raw["dimensions"][0]["source_segment_ids"] = ["another-answer:s0"]
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(model, Repository()).evaluate(request())
    assert feedback.analysis_status == "valid"
    assert feedback.assessment_status == "unavailable"
    assert not feedback.dimensions


@pytest.mark.asyncio
@pytest.mark.parametrize("same_project", [True, False])
async def test_explicit_cross_thread_correction_requires_same_project_sources(same_project):
    evaluation = request("I should correct that: we stored it in memory.")
    old_question = evaluation.question.model_copy(
        update={
            "question_id": "old",
            "thread_id": "old-thread",
            "project_id": "project" if same_project else "other-project",
        }
    )
    earlier_answer = evaluation.answer.model_copy(
        update={"question_id": "old", "answer_id": "earlier", "text": "We stored it in a database."}
    )
    history = [SimpleNamespace(question=old_question, answer=earlier_answer)]

    def model(prompt, data, schema):
        assert data["history"] == []
        assert bool(data["relation_history"]) == same_project
        raw = output(quote="we stored it in memory.")
        raw["analysis"]["answer_relations"] = [
            {
                "kind": "supersedes",
                "earlier_answer_id": "earlier",
                "earlier_quote": earlier_answer.text,
                "current_quote": "we stored it in memory.",
                "explanation": "The candidate explicitly corrected their storage statement.",
            }
        ]
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(model, Repository(history)).evaluate(evaluation)
    assert bool(feedback.analysis.answer_relations) == same_project
    if same_project:
        assert feedback.analysis.answer_relations[0].relation_id == "relation-current-0"
    else:
        assert "INVALID_ANSWER_RELATION" in feedback.evaluation_issues


def test_segments_use_original_offsets_even_with_newlines_and_chinese():
    text = " First line\n第二句话。 Last sentence!"
    segments = answer_segments("answer", text)
    assert [segment["text"] for segment in segments] == [
        "First line",
        "第二句话。",
        "Last sentence!",
    ]
    assert all(text[segment["start"] : segment["end"]] == segment["text"] for segment in segments)
