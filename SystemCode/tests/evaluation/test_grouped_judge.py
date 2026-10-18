"""Mixed-episode regression and evidence-sensitive incremental scoring."""

import pytest

from evaluation.judge import GroupedJudgeDraft, RubricJudge, validate_judgement
from evaluation.model_calls import EvaluationStageError
from evaluation.rubric import load_rubric_pack
from tests.evaluation.port_helpers import evaluation_output
from tests.evaluation.resolver_helpers import decision, resolve, source


async def test_five_independent_sources_cannot_be_combined_into_one_rating():
    sources = [
        source(str(i), f"In incident {i} I measured a different bottleneck.") for i in range(5)
    ]
    resolution = resolve(*[(s, decision(s)) for s in sources])
    calls = []

    def model(prompt, data, schema):
        calls.append(data)
        assert schema is GroupedJudgeDraft
        assert len(data["groups"]) == 5
        assert all([e["id"] for e in g["evidence"]] == ["e1"] for g in data["groups"])
        return evaluation_output(prompt, data, schema)

    rubric = load_rubric_pack()
    result = await RubricJudge(model).judge(resolution, rubric=rubric)
    assert len(calls) == 1
    ratings = [a for a in result.assessments if a.decision == "included"]
    assert len(ratings) == 5 and all(len(a.evidence_ids) == 1 for a in ratings)
    assert validate_judgement(result, resolution, rubric=rubric) == result


async def test_unchanged_groups_reuse_scores_without_sending_them_to_the_model():
    first, second = source("a"), source("b", "In a separate incident I measured connection waits.")
    before = resolve((first, decision(first)))
    after = resolve((first, decision(first)), (second, decision(second)))
    calls = []

    def model(prompt, data, schema):
        calls.append(data)
        return evaluation_output(prompt, data, schema)

    judge, rubric = RubricJudge(model), load_rubric_pack()
    initial = await judge.judge(before, rubric=rubric)
    updated = await judge.judge(
        after, rubric=rubric, previous=initial, previous_history=before.history
    )
    assert len(calls) == 2 and len(calls[-1]["groups"]) == 1
    assert calls[-1]["groups"][0]["evidence"][0]["quotes"] == [second.turn.answer.text]
    assert len([a for a in updated.assessments if a.decision == "included"]) == 2
    unchanged = await judge.judge(
        after, rubric=rubric, previous=updated, previous_history=after.history
    )
    assert len(calls) == 2 and unchanged == updated


async def test_correction_invalidates_previous_episode_rating():
    first = source("a")
    correction = source("b", "I retract my earlier statement that I used a profiler.")
    before = resolve((first, decision(first)))
    after = resolve(
        (first, decision(first)),
        (
            correction,
            decision(correction, "retracts", (first,), proof=correction.evidence.quote_spans[0]),
        ),
    )
    calls = []

    def model(prompt, data, schema):
        calls.append(data)
        output = evaluation_output(prompt, data, schema).model_dump()
        if len(calls) == 2:
            assert {e["status"] for e in data["groups"][0]["evidence"]} == {"retracted"}
            output["groups"][0]["ratings"][0]["decision"] = "excluded"
            output["groups"][0]["ratings"][0]["assigned_level"] = None
        return output

    judge, rubric = RubricJudge(model), load_rubric_pack()
    initial = await judge.judge(before, rubric=rubric)
    updated = await judge.judge(
        after, rubric=rubric, previous=initial, previous_history=before.history
    )
    assert len(calls) == 2
    assert all(a.assigned_level is None for a in updated.assessments)


@pytest.mark.parametrize(
    "problem",
    [
        "outside_group",
        "missing_group",
        "duplicate_group",
        "duplicate_criterion",
        "unscored_level",
    ],
)
async def test_grouped_output_does_not_weaken_reference_or_score_validation(problem):
    item = source("a")

    def model(prompt, data, schema):
        output = evaluation_output(prompt, data, schema).model_dump()
        group = output["groups"][0]
        rating = group["ratings"][0]
        if problem == "outside_group":
            rating["evidence_ids"] = ("e2",)
        elif problem == "missing_group":
            output["groups"] = ()
        elif problem == "duplicate_group":
            output["groups"] = (*output["groups"], group)
        elif problem == "duplicate_criterion":
            group["ratings"] = (*group["ratings"], rating)
        else:
            rating["decision"] = "insufficient"
        return output

    with pytest.raises(EvaluationStageError, match="judge_invalid_assessment"):
        await RubricJudge(model).judge(resolve((item, decision(item))), rubric=load_rubric_pack())


async def test_forged_cache_is_rejected_before_reuse():
    item = source("a")
    resolution, rubric = resolve((item, decision(item))), load_rubric_pack()
    judge = RubricJudge(evaluation_output)
    previous = await judge.judge(resolution, rubric=rubric)
    previous = previous.model_copy(update={"resolution_digest": "forged"})
    with pytest.raises(EvaluationStageError, match="judge_invalid_evidence"):
        await judge.judge(
            resolution, rubric=rubric, previous=previous, previous_history=resolution.history
        )


@pytest.mark.parametrize("relation", ["refines", "contradicts"])
async def test_new_detail_or_conflict_rejudges_affected_group(relation):
    first = source("a")
    detail = source(
        "b",
        "The profiler showed 80% lock waits."
        if relation == "refines"
        else "I did not use a profiler; I guessed the cause.",
    )
    before = resolve((first, decision(first)))
    after = resolve((first, decision(first)), (detail, decision(detail, relation, (first,))))
    calls = []

    def model(prompt, data, schema):
        calls.append(data)
        return evaluation_output(prompt, data, schema)

    judge, rubric = RubricJudge(model), load_rubric_pack()
    initial = await judge.judge(before, rubric=rubric)
    await judge.judge(after, rubric=rubric, previous=initial, previous_history=before.history)
    assert len(calls) == 2 and len(calls[-1]["groups"]) == 1
    assert len(calls[-1]["groups"][0]["evidence"]) == 2
    if relation == "contradicts":
        assert {e["status"] for e in calls[-1]["groups"][0]["evidence"]} == {"disputed"}


async def test_rubric_change_invalidates_cached_ratings():
    item = source("a")
    resolution, rubric = resolve((item, decision(item))), load_rubric_pack()
    calls = []

    def model(prompt, data, schema):
        calls.append(data)
        return evaluation_output(prompt, data, schema)

    judge = RubricJudge(model)
    previous = await judge.judge(resolution, rubric=rubric)
    value = rubric.model_dump()
    value["rubrics"][0]["criteria"][0]["anchors"][0]["behavior"] += " Revised anchor."
    revised = type(rubric).model_validate(value)
    await judge.judge(
        resolution, rubric=revised, previous=previous, previous_history=resolution.history
    )
    assert len(calls) == 2


async def test_unmapped_sources_are_the_complement_of_actual_references():
    first = source("a")
    other = source("b", "I also ran a test.")
    resolution = resolve((first, decision(first)), (other, decision(other, same=(first,))))

    def model(prompt, data, schema):
        assert (
            "unmapped_evidence_ids"
            not in schema.model_json_schema()["$defs"]["GroupRatings"]["properties"]
        )
        return schema(
            groups=[
                dict(
                    group_id="g1",
                    ratings=[
                        dict(
                            criterion_id="debugging.diagnostic_method",
                            evidence_ids=["e1"],
                            assigned_level=3,
                            decision="included",
                            rationale="A concrete diagnostic action.",
                        )
                    ],
                )
            ]
        )

    result = await RubricJudge(model).judge(resolution, rubric=load_rubric_pack())
    referenced = {k for a in result.assessments for k in a.evidence_ids}
    unmapped = {u.evidence_id for u in result.unmapped_evidence}
    assert len(referenced) == len(unmapped) == 1
    assert referenced.isdisjoint(unmapped)
    assert referenced | unmapped == {e.evidence_id for e in resolution.evidence_items}


async def test_explicit_empty_group_records_insufficient_evidence_without_scoring():
    item = source("a")
    judge = RubricJudge(lambda p, d, s: s(groups=[dict(group_id="g1", ratings=[])]))
    result = await judge.judge(resolve((item, decision(item))), rubric=load_rubric_pack())
    assert all(
        a.decision == "insufficient" and a.assigned_level is None for a in result.assessments
    )
    assert [u.evidence_id for u in result.unmapped_evidence] == [item.evidence.evidence_id]
