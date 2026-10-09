from types import SimpleNamespace

import pytest

from app.adapters.assessment import RealtimeDecisionAdapter
from app.adapters.decision import CompactAnswerDecision
from app.adapters.evaluation import LLMEvaluationAdapter
from tests.app.test_evaluation_components import Repository, request
from tests.app.test_evaluation_recovery import output
from tests.app.test_objective_coverage import evaluation_request, objective_repository


@pytest.mark.asyncio
async def test_general_limitations_do_not_block_grounded_goal_completion():
    def model(prompt, data, schema):
        result = output(quote="I owned the shared state module.")
        result["analysis"]["uncertainties"] = ["No online production statistics to cite"]
        result["objective_coverage"] = [
            dict(
                objective_id="state",
                coverage_status="sufficient",
                supporting_segment_ids=[data["answer_segments"][0]["id"]],
            )
        ]
        return schema.model_validate(result)

    feedback = await LLMEvaluationAdapter(model, objective_repository()).evaluate(
        evaluation_request()
    )
    assert feedback.objective_coverage_status == "valid"
    assert feedback.objective_coverage[0].coverage_status == "sufficient"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "compatible,kind,expected",
    [
        ("exclusive", "clarifies", "disputes"),
        ("uncertain", "clarifies", "disputes"),
        ("compatible", "clarifies", "clarifies"),
        ("exclusive", "supersedes", "supersedes"),
    ],
)
async def test_cross_thread_relations_resolve_original_segments_and_enforce_compatibility(
    compatible, kind, expected
):
    old_text = "All frames in the window share a single ROI."
    current = request("Each frame in the same window uses its own ROI.")
    old = SimpleNamespace(
        question=current.question.model_copy(update={"thread_id": "old-thread"}),
        answer=current.answer.model_copy(update={"answer_id": "old", "text": old_text}),
    )
    repo = Repository([old])

    def model(prompt, data, schema):
        assert schema is CompactAnswerDecision
        assert "answer" not in data and "history" not in data and "relation_history" not in data
        assert len(data["previous_answers"]) == 1
        return schema(
            answer_relevance=1,
            analysis=dict(status="substantive", scope="concrete", complete=True),
            relations=[
                dict(
                    earlier_segment="old:s0",
                    current_segment="current:s0",
                    kind=kind,
                    compatibility=compatible,
                )
            ],
        )

    feedback = await RealtimeDecisionAdapter(model, repo).evaluate(current)
    relation = feedback.analysis.answer_relations[0]
    assert relation.kind == expected
    assert relation.earlier_quote == old_text and relation.current_quote == current.answer.text
    assert bool(feedback.analysis.contradictions) == (expected == "disputes")
    assert feedback.analysis.thread_complete == (expected != "disputes")


@pytest.mark.asyncio
async def test_unknown_relation_segments_cannot_publish_candidate_conflict():
    def model(prompt, data, schema):
        return schema(
            answer_relevance=1,
            analysis=dict(status="substantive", scope="concrete"),
            relations=[
                dict(
                    earlier_segment="foreign:s0",
                    current_segment="current:s0",
                    kind="disputes",
                    compatibility="exclusive",
                )
            ],
        )

    feedback = await RealtimeDecisionAdapter(model, Repository()).evaluate(request())
    assert feedback.analysis_status == "unavailable"
    assert not feedback.analysis.answer_relations and not feedback.dimensions


@pytest.mark.asyncio
async def test_unrelated_conflict_does_not_block_a_separately_grounded_objective():
    from app.adapters.evaluation import answer_segments, grounded_objective_coverage
    from shared.contracts import AnswerAnalysis, ContradictionEvidence, ObjectiveCoverage

    text = "I implemented bounded queues. Each frame uses an independent ROI."
    segments = answer_segments("new", text)
    analysis = AnswerAnalysis(
        status="substantive",
        answer_scope="concrete",
        contradictions=["Incompatible ROI coordinates"],
        contradiction_evidence=[
            ContradictionEvidence(
                earlier_answer_id="old",
                earlier_quote="The window shares one ROI.",
                current_quote=segments[1]["text"],
                explanation="Incompatible ROI coordinates",
            )
        ],
    )
    updates = [
        ObjectiveCoverage(
            objective_id="queues", coverage_status="sufficient", supporting_segment_ids=["new:s0"]
        )
    ]
    accepted, issues = grounded_objective_coverage(
        updates,
        [dict(objective_id="queues", accepted_evidence=[])],
        segments,
        "new",
        analysis,
        "valid",
    )
    assert not issues and accepted[0].coverage_status == "sufficient"
    updates[0].supporting_segment_ids = ["new:s1"]
    rejected, issues = grounded_objective_coverage(
        updates,
        [dict(objective_id="queues", accepted_evidence=[])],
        segments,
        "new",
        analysis,
        "valid",
    )
    assert not rejected and issues == ["CONFLICTING_OBJECTIVE_COMPLETION"]


@pytest.mark.asyncio
async def test_general_limitation_is_grounded_and_does_not_become_a_followup_gap():
    def model(prompt, data, schema):
        return schema(
            answer_relevance=1,
            analysis=dict(
                status="substantive",
                scope="concrete",
                complete=True,
                limitation_segments=["current:s1"],
            ),
            coverage=[dict(objective_id="state", status="sufficient", segments=["current:s0"])],
        )

    current = evaluation_request()
    current.answer.text = "I implemented version checks. I have no online statistics."
    result = await RealtimeDecisionAdapter(model, objective_repository()).evaluate(current)
    assert result.analysis.limitations == ["I have no online statistics."]
    assert not result.analysis.missing_information and not result.analysis.uncertainties
    assert result.objective_coverage[0].coverage_status == "sufficient"


@pytest.mark.asyncio
async def test_accepted_old_evidence_remains_available_for_conflict_checks_after_history_grows():
    current = evaluation_request()
    current.answer.text = "Each frame in the same window uses its own ROI."
    old_text = "All frames in the window share a single ROI."
    repo = objective_repository()
    old = SimpleNamespace(
        question=current.question.model_copy(update={"thread_id": "old-thread"}),
        answer=current.answer.model_copy(update={"answer_id": "old", "text": old_text}),
    )
    repo.context.question_history = [old] + [
        SimpleNamespace(
            question=current.question.model_copy(update={"thread_id": f"other-{i}"}),
            answer=current.answer.model_copy(
                update={"answer_id": f"later-{i}", "text": "I implemented bounded queues."}
            ),
        )
        for i in range(13)
    ]
    repo.context.topic_progress["state"].coverage_evidence = [
        dict(answer_id="old", supporting_quotes=[old_text], supporting_segment_ids=["old:s0"])
    ]

    def model(prompt, data, schema):
        assert any(a["answer_id"] == "old" for a in data["previous_answers"])
        return schema(
            answer_relevance=1,
            analysis=dict(status="substantive", scope="concrete", complete=True),
            relations=[
                dict(
                    earlier_segment="old:s0",
                    current_segment="current:s0",
                    kind="disputes",
                    compatibility="exclusive",
                )
            ],
        )

    feedback = await RealtimeDecisionAdapter(model, repo).evaluate(current)
    assert feedback.analysis_status == "valid"
    assert feedback.analysis.contradiction_evidence[0].earlier_answer_id == "old"
    assert feedback.analysis.answer_relations[0].kind == "disputes"
    assert not feedback.analysis.thread_complete
