"""A complete narrow answer is not proof that the whole agenda objective is covered."""

from types import SimpleNamespace

import pytest

from app.adapters.evaluation import LLMEvaluationAdapter
from shared.contracts.planning import TopicAllocation
from tests.app.test_evaluation_components import Repository, request
from tests.app.test_evaluation_recovery import output


def objective_repository():
    repository = Repository()
    repository.context.plan = SimpleNamespace(
        topics=[
            TopicAllocation(
                project_id="project",
                topic_key="state",
                objective="Explain shared state design",
                completion_criteria="Describe fields, storage and concurrency control",
                budget_seconds=100,
                expected_questions=2,
            ),
            TopicAllocation(
                project_id="project",
                topic_key="latency",
                objective="Validate runtime latency",
                completion_criteria="Describe the measurement and baseline",
                budget_seconds=100,
                expected_questions=2,
            ),
            TopicAllocation(
                project_id="other-project",
                topic_key="foreign",
                objective="Describe other project",
                completion_criteria="Explain the other implementation",
                budget_seconds=100,
                expected_questions=2,
            ),
        ]
    )
    repository.context.topic_progress = {
        key: SimpleNamespace(objective_id=key, coverage_status="unassessed", missing_information=[])
        for key in ["state", "latency", "foreign"]
    }
    return repository


def evaluation_request():
    evaluation = request("I owned the shared state module. I measured latency against a baseline.")
    evaluation.question.topic_key = "state"
    evaluation.question.objective_id = "state"
    evaluation.question.information_goal = "What was your personal responsibility"
    return evaluation


@pytest.mark.asyncio
async def test_evaluation_receives_agenda_goal_and_criteria_independently_of_narrow_question():
    def llm(prompt, data, schema):
        objectives = {item["objective_id"]: item for item in data["objectives"]}
        assert set(objectives) == {"state", "latency"}
        assert objectives["state"]["objective"] == "Explain shared state design"
        assert objectives["state"]["completion_criteria"] == (
            "Describe fields, storage and concurrency control"
        )
        assert data["current_objective_id"] == "state"
        raw = output(quote="I owned the shared state module.")
        raw["analysis"]["thread_complete"] = True
        raw["objective_coverage"] = [
            {
                "objective_id": "state",
                "coverage_status": "partial",
                "missing_information": ["Storage mechanism and concurrency control"],
                "supporting_segment_ids": [data["answer_segments"][0]["id"]],
                "rationale": "Ownership is answered, but the broader implementation is not.",
            }
        ]
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(llm, objective_repository()).evaluate(
        evaluation_request()
    )
    assert feedback.analysis.thread_complete
    assert feedback.objective_coverage_status == "valid"
    assert feedback.objective_coverage[0].coverage_status == "partial"
    assert feedback.objective_coverage[0].supporting_quotes == ["I owned the shared state module."]


@pytest.mark.asyncio
async def test_no_explicit_objective_evidence_cannot_turn_thread_complete_into_coverage():
    def llm(prompt, data, schema):
        raw = output(quote="I owned the shared state module.")
        raw["analysis"]["thread_complete"] = True
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(llm, objective_repository()).evaluate(
        evaluation_request()
    )
    assert feedback.analysis.thread_complete
    assert feedback.objective_coverage == []
    assert feedback.objective_coverage_status == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["foreign", "invented_segment", "contradictory_completion"])
async def test_invalid_objective_coverage_cannot_mark_goal_sufficient(invalid):
    def llm(prompt, data, schema):
        raw = output(quote="I owned the shared state module.")
        raw["objective_coverage"] = [
            {
                "objective_id": "foreign" if invalid == "foreign" else "state",
                "coverage_status": "sufficient",
                "missing_information": ["Missing storage"]
                if invalid == "contradictory_completion"
                else [],
                "supporting_segment_ids": [
                    "another-answer:s0"
                    if invalid == "invented_segment"
                    else data["answer_segments"][0]["id"]
                ],
            }
        ]
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(llm, objective_repository()).evaluate(
        evaluation_request()
    )
    assert feedback.analysis_status == "valid"
    assert feedback.objective_coverage == []
    assert feedback.objective_coverage_status == "unavailable"
    assert feedback.evaluation_issues


@pytest.mark.asyncio
async def test_one_answer_can_supply_separate_evidence_for_multiple_known_objectives():
    def llm(prompt, data, schema):
        raw = output(quote="I owned the shared state module.")
        raw["objective_coverage"] = [
            {
                "objective_id": "state",
                "coverage_status": "partial",
                "missing_information": ["State storage"],
                "supporting_segment_ids": [data["answer_segments"][0]["id"]],
            },
            {
                "objective_id": "latency",
                "coverage_status": "partial",
                "missing_information": ["Baseline values"],
                "supporting_segment_ids": [data["answer_segments"][1]["id"]],
            },
        ]
        return schema.model_validate(raw)

    feedback = await LLMEvaluationAdapter(llm, objective_repository()).evaluate(
        evaluation_request()
    )
    assert feedback.objective_coverage_status == "valid"
    assert len(feedback.objective_coverage) == 2
    assert all(item.answer_id == "current" for item in feedback.objective_coverage)
    assert (
        feedback.objective_coverage[0].supporting_quotes
        != feedback.objective_coverage[1].supporting_quotes
    )
