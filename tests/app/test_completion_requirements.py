"""Completion needs independent evidence for every accepted criterion, not only a quote."""

from types import SimpleNamespace

import pytest

from agents.planning.completion import project_completion, requirements_for_objective
from app.adapters.assessment import RealtimeDecisionAdapter
from app.adapters.evaluation import LLMEvaluationAdapter
from shared.contracts.planning import TopicAllocation, TopicProgress
from tests.app.decision_fixtures import compact_output, legacy_data
from tests.app.test_evaluation_components import Repository, request
from tests.app.test_evaluation_recovery import output


@pytest.fixture(params=[False, True], ids=["legacy", "realtime"])
def adapter_factory(request):
    def build(model, repository):
        if not request.param:
            return LLMEvaluationAdapter(model, repository)

        from app.adapters.evaluation import AnswerEvidence

        def compact_model(prompt, data, schema):
            return compact_output(model(prompt, legacy_data(data), AnswerEvidence).model_dump())

        return RealtimeDecisionAdapter(compact_model, repository)

    return build


def completion_case(criteria, text):
    evaluation = request(text)
    evaluation.question.topic_key = "objective"
    evaluation.question.objective_id = "objective"
    item = TopicAllocation(
        project_id="project",
        topic_key="objective",
        objective="Explain the implementation and validation",
        completion_criteria=criteria,
        budget_seconds=200,
        expected_questions=3,
    )
    progress = TopicProgress(objective_id="objective")
    progress.completion_requirements = requirements_for_objective(item, progress)
    repo = Repository()
    repo.context.plan = SimpleNamespace(topics=[item])
    repo.context.topic_progress = {"objective": progress}
    return repo, evaluation, progress


def model_result(data, coverage):
    raw = output(quote=data["answer_segments"][0]["text"])
    raw["analysis"]["thread_complete"] = True
    raw["objective_coverage"] = [
        {
            "objective_id": "objective",
            "coverage_status": "sufficient",
            "supporting_segment_ids": [data["answer_segments"][0]["id"]],
            "criterion_coverage": coverage,
        }
    ]
    return raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        "BM25 and dense retrieval run in parallel, followed by RRF and limited reranking.",
        "FFmpeg uses bounded queues between decode, inference and encode, with backpressure.",
    ],
)
async def test_implementation_only_cannot_complete_validation_requirement(answer, adapter_factory):
    repo, evaluation, progress = completion_case(
        "Concrete implementation; Concrete validation procedure", answer
    )

    def model(prompt, data, schema):
        requirements = data["objectives"][0]["completion_requirements"]
        assert len(requirements) == 2
        return schema.model_validate(
            model_result(
                data,
                [
                    {
                        "criterion_id": requirements[0]["criterion_id"],
                        "coverage_status": "sufficient",
                        "supporting_segment_ids": [data["answer_segments"][0]["id"]],
                    }
                ],
            )
        )

    feedback = await adapter_factory(model, repo).evaluate(evaluation)
    assert feedback.analysis.thread_complete
    assert feedback.objective_coverage[0].coverage_status == "partial"
    assert feedback.objective_coverage[0].missing_information == ["Concrete validation procedure"]
    assert feedback.objective_coverage_status == "valid"
    assert all(r.coverage_status == "unassessed" for r in progress.completion_requirements)


@pytest.mark.asyncio
async def test_old_broad_sufficient_verdict_has_no_authority_to_close_criteria(adapter_factory):
    repo, evaluation, _ = completion_case("Implementation; Validation", "I used a bounded queue.")
    feedback = await adapter_factory(
        lambda prompt, data, schema: schema.model_validate(model_result(data, [])), repo
    ).evaluate(evaluation)
    assert feedback.objective_coverage[0].coverage_status == "partial"
    assert feedback.objective_coverage[0].missing_information == ["Implementation", "Validation"]


@pytest.mark.asyncio
async def test_roi_method_is_satisfied_and_only_actual_results_remain(adapter_factory):
    repo, evaluation, _ = completion_case(
        "Controlled ROI comparison method; Actual measured ROI savings",
        "We held video, model and seeds constant and recorded tokens, VAE time and peak memory. "
        "I have no confirmed savings numbers.",
    )

    def model(prompt, data, schema):
        method, results = data["objectives"][0]["completion_requirements"]
        raw = model_result(
            data,
            [
                {
                    "criterion_id": method["criterion_id"],
                    "coverage_status": "sufficient",
                    "supporting_segment_ids": [data["answer_segments"][0]["id"]],
                },
                {
                    "criterion_id": results["criterion_id"],
                    "coverage_status": "partial",
                    "missing_information": ["Actual measured token, VAE time and memory savings"],
                    "supporting_segment_ids": [data["answer_segments"][1]["id"]],
                },
            ],
        )
        raw["objective_coverage"][0]["coverage_status"] = "partial"
        raw["objective_coverage"][0]["missing_information"] = ["Quantify or validate ROI savings"]
        return schema.model_validate(raw)

    feedback = await adapter_factory(model, repo).evaluate(evaluation)
    update = feedback.objective_coverage[0]
    assert update.coverage_status == "partial"
    assert update.missing_information == ["Actual measured token, VAE time and memory savings"]
    assert update.criterion_coverage[0].coverage_status == "sufficient"
    assert update.criterion_coverage[0].supporting_quotes == [
        evaluation.answer.text.split(". ")[0] + "."
    ]


@pytest.mark.asyncio
async def test_concrete_method_can_complete_without_unrequired_online_statistics(adapter_factory):
    repo, evaluation, _ = completion_case(
        "Controlled comparison method including limitations",
        "We compared identical clips and measured peak memory while checking visual seams. "
        "We have no online A/B statistics.",
    )

    def model(prompt, data, schema):
        criterion = data["objectives"][0]["completion_requirements"][0]
        return schema.model_validate(
            model_result(
                data,
                [
                    {
                        "criterion_id": criterion["criterion_id"],
                        "coverage_status": "sufficient",
                        "supporting_segment_ids": [s["id"] for s in data["answer_segments"]],
                    }
                ],
            )
        )

    feedback = await adapter_factory(model, repo).evaluate(evaluation)
    assert feedback.objective_coverage[0].coverage_status == "sufficient"
    assert feedback.objective_coverage[0].missing_information == []


@pytest.mark.asyncio
async def test_all_evidenced_criteria_override_stale_overall_gap(adapter_factory):
    repo, evaluation, _ = completion_case(
        "Validation procedure", "I compared identical clips and checked peak memory and seams."
    )

    def model(prompt, data, schema):
        criterion = data["objectives"][0]["completion_requirements"][0]
        raw = model_result(
            data,
            [
                {
                    "criterion_id": criterion["criterion_id"],
                    "coverage_status": "sufficient",
                    "supporting_segment_ids": [data["answer_segments"][0]["id"]],
                }
            ],
        )
        raw["objective_coverage"][0].update(
            coverage_status="partial", missing_information=["Quantify or validate savings"]
        )
        return schema.model_validate(raw)

    feedback = await adapter_factory(model, repo).evaluate(evaluation)
    assert feedback.objective_coverage[0].coverage_status == "sufficient"
    assert feedback.objective_coverage[0].missing_information == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["invented_id", "invented_segment", "missing_proof", "duplicate"]
)
async def test_invalid_criterion_evidence_never_completes_objective(invalid, adapter_factory):
    repo, evaluation, _ = completion_case("Validation procedure", "I tested the memory boundary.")

    def model(prompt, data, schema):
        criterion = data["objectives"][0]["completion_requirements"][0]
        observation = {
            "criterion_id": "invented" if invalid == "invented_id" else criterion["criterion_id"],
            "coverage_status": "sufficient",
            "supporting_segment_ids": []
            if invalid == "missing_proof"
            else [
                "invented:s0" if invalid == "invented_segment" else data["answer_segments"][0]["id"]
            ],
        }
        return schema.model_validate(
            model_result(data, [observation] * (2 if invalid == "duplicate" else 1))
        )

    feedback = await adapter_factory(model, repo).evaluate(evaluation)
    assert feedback.analysis_status == "valid"
    assert feedback.objective_coverage[0].coverage_status == "partial"
    assert feedback.evaluation_issues


@pytest.mark.asyncio
async def test_prior_criterion_evidence_survives_a_later_validation_answer(adapter_factory):
    repo, first, progress = completion_case(
        "Implementation; Validation procedure", "I used a bounded queue."
    )

    def model(prompt, data, schema):
        requirements = data["objectives"][0]["completion_requirements"]
        criterion = requirements[0 if data["answer"].startswith("I used") else 1]
        return schema.model_validate(
            model_result(
                data,
                [
                    {
                        "criterion_id": criterion["criterion_id"],
                        "coverage_status": "sufficient",
                        "supporting_segment_ids": [data["answer_segments"][0]["id"]],
                    }
                ],
            )
        )

    adapter = adapter_factory(model, repo)
    update = (await adapter.evaluate(first)).objective_coverage[0]
    _, progress.completion_requirements = project_completion(
        update, progress.completion_requirements
    )
    second = first.model_copy(deep=True)
    second.answer.answer_id = "validation-answer"
    second.answer.text = (
        "I tested increasingly long clips and observed a stable peak memory boundary."
    )
    update = (await adapter.evaluate(second)).objective_coverage[0]
    assert update.coverage_status == "sufficient"
    _, requirements = project_completion(update, progress.completion_requirements)
    assert requirements[0].evidence[0]["answer_id"] == first.answer.answer_id
    assert requirements[1].evidence[0]["answer_id"] == second.answer.answer_id
