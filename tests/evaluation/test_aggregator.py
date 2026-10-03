import pytest

from evaluation.aggregator import aggregate_scores
from evaluation.judge import bind_judgement
from evaluation.policy import AggregationPolicy, ScoringProfile
from evaluation.rubric import RubricPack, load_rubric_pack
from tests.evaluation.resolver_helpers import assessment, decision, resolve, source
from tests.evaluation.scoring_helpers import (
    all_assessments,
    configuration,
    draft_for,
    scoring_input,
)

DIAGNOSTIC = "debugging.diagnostic_method"


def criterion(record, key=DIAGNOSTIC):
    return next(
        c for s in record.snapshot.competencies for c in s.criteria if c.criterion_id == key
    )


def competency(record, key="debugging"):
    return next(s for s in record.snapshot.competencies if s.competency == key)


def trace(record, key=DIAGNOSTIC):
    return next(t for t in record.criteria if t.criterion_id == key)


def test_independent_weighted_mean_and_reliability_have_explicit_math():
    weak = source("weak", specificity="partial")
    strong = source("strong", "In another incident I profiled the database pool.")
    resolution = resolve((weak, decision(weak)), (strong, decision(strong)))
    record = aggregate_scores(
        scoring_input(
            resolution,
            assessment("weak", weak, level=3),
            assessment("strong", strong, level=5),
        )
    )
    assert criterion(record).score == 4.25  # (3 * .6 + 5 * 1) / 1.6
    assert trace(record).mean.numerator == 6.8
    assert trace(record).mean.denominator == 1.6
    assert trace(record).independent_evidence_count == 2
    assert trace(record).quality_mean == 0.8
    assert trace(record).agreement_factor == 0.765625
    assert trace(record).support_factor == 1
    assert trace(record).reliability == 0.6125
    assert competency(record).coverage == pytest.approx(1 / 6)
    assert competency(record).reliability == pytest.approx(0.6125 / 6)
    assert record.snapshot.overall_score is None
    assert record.snapshot.overall_coverage == pytest.approx(1 / 36)
    assert "insufficient_overall_coverage" in record.snapshot.reason_codes


def test_duplicate_and_followup_do_not_increase_independent_mass():
    original = source("a", specificity="partial")
    duplicate = source("b", "I used profiling in this incident.")
    refinement = source("c", "The profile showed lock contention in worker threads.")
    before = resolve((original, decision(original)))
    after = resolve(
        (original, decision(original)), (duplicate, decision(duplicate, "duplicate", (original,)))
    )
    a = aggregate_scores(scoring_input(before, assessment("a", original)))
    b = aggregate_scores(scoring_input(after, assessment("a", original, duplicate)))
    assert criterion(a).score == criterion(b).score == 3
    assert trace(a) == trace(b)
    assert len(criterion(b).contributions) == 1
    assert (
        next(
            w for w in b.weights if w.evidence_id == duplicate.evidence.evidence_id
        ).effective_weight
        == 0
    )
    refined = resolve(
        *zip(after.history.sources, after.history.decisions, strict=True),
        (refinement, decision(refinement, "refines", (original,))),
    )
    c = aggregate_scores(scoring_input(refined, assessment("all", original, duplicate, refinement)))
    assert trace(c).independent_evidence_count == 1
    assert trace(c).mean.denominator == 1
    assert criterion(c).contributions[0].evidence_ids[0] == refinement.evidence.evidence_id
    assert len(criterion(c).contributions[0].evidence_ids) == 3


def test_group_selection_uses_effective_weight_not_phase_three_ordinal_ranking():
    specific_team = source("a", ownership="team_only")
    personal_partial = source(
        "b", "I compared thread profiles to find waits.", specificity="partial"
    )
    resolution = resolve(
        (specific_team, decision(specific_team)),
        (personal_partial, decision(personal_partial, same=(specific_team,))),
    )
    record = aggregate_scores(
        scoring_input(
            resolution,
            assessment("combined", specific_team, personal_partial),
        )
    )
    assert (
        criterion(record).contributions[0].evidence_ids[0] == personal_partial.evidence.evidence_id
    )
    assert criterion(record).contributions[0].effective_weight == 0.6
    assert [w for w in record.weights if not w.selected][0].reason_codes[-1] == "supplemental_only"


@pytest.mark.parametrize(
    "threshold,reason",
    [
        ({"min_independent_evidence": 2}, "insufficient_independent_evidence"),
        ({"min_effective_weight": 1.01}, "insufficient_effective_weight"),
        ({"min_criterion_reliability": 0.51}, "insufficient_criterion_reliability"),
    ],
)
def test_criterion_publication_thresholds(threshold, reason):
    item = source("a")
    record = aggregate_scores(
        scoring_input(resolve((item, decision(item))), assessment("a", item), thresholds=threshold)
    )
    assert criterion(record).score is None
    assert reason in criterion(record).reason_codes
    assert trace(record).mean.numerator == 3


def test_exact_thresholds_publish_and_do_not_round_up_near_misses():
    item = source("a", specificity="partial")
    common = (resolve((item, decision(item))), assessment("a", item))
    exact = aggregate_scores(
        scoring_input(
            *common,
            thresholds={
                "min_effective_weight": 0.6,
                "min_criterion_reliability": 0.3,
            },
        )
    )
    missed = aggregate_scores(
        scoring_input(
            *common,
            thresholds={
                "min_effective_weight": 0.6000000000000001,
            },
        )
    )
    assert criterion(exact).score == 3
    assert criterion(missed).score is None


@pytest.mark.parametrize(
    "threshold,reason",
    [
        ({"min_competency_coverage": 0.5}, "insufficient_competency_coverage"),
        ({"min_competency_reliability": 0.5}, "insufficient_competency_reliability"),
    ],
)
def test_competency_gates_keep_criterion_score_separate_from_coverage(threshold, reason):
    item = source("a")
    record = aggregate_scores(
        scoring_input(resolve((item, decision(item))), assessment("a", item), thresholds=threshold)
    )
    assert criterion(record).score == 3
    assert competency(record).score is None
    assert reason in competency(record).reason_codes
    assert record.snapshot.overall_score is None


def test_unresolved_conflict_blocks_only_relevant_criterion_and_overall():
    first = source("a")
    counter = source("b", "I never profiled the incident.")
    valid = source("c", "I validated the fix with sustained load.")
    resolution = resolve(
        (first, decision(first)),
        (counter, decision(counter, "contradicts", (first,))),
        (valid, decision(valid, same=(first,))),
    )
    record = aggregate_scores(
        scoring_input(
            resolution,
            assessment("disputed", first, counter),
            assessment("valid", valid, criterion="debugging.fix_verification"),
        )
    )
    assert criterion(record).score is None
    assert "unresolved_contradiction" in criterion(record).reason_codes
    assert criterion(record, "debugging.fix_verification").score == 3
    assert trace(record).reliability == 0
    assert record.snapshot.overall_score is None
    assert "unresolved_contradiction" in record.snapshot.reason_codes
    assert any(
        a.decision == "disputed" and a.counter_evidence_ids for a in record.gated_assessments
    )


def test_unmapped_conflict_still_blocks_overall_publication():
    item = source("a")
    counter = source("b", "I never used profiling in this incident.")
    good = source("c", "In a different incident I traced the pool, fixed and verified it.")
    resolution = resolve(
        (item, decision(item)),
        (counter, decision(counter, "contradicts", (item,))),
        (good, decision(good)),
    )
    record = aggregate_scores(scoring_input(resolution, *all_assessments(good)))
    assert all(c.score == 4 for c in record.snapshot.competencies)
    assert record.snapshot.overall_score is None
    assert record.snapshot.reason_codes == ("unresolved_contradiction",)


@pytest.mark.parametrize("state", ["retracted", "unresolved", "excluded", "insufficient"])
def test_non_scoreable_evidence_has_zero_weight_and_never_level_zero(state):
    item = source("a")
    value = assessment("a", item)
    if state == "retracted":
        retract = source("b", "I retract my earlier profiling statement.")
        resolution = resolve(
            (item, decision(item)),
            (
                retract,
                decision(
                    retract,
                    "retracts",
                    (item,),
                    proof=retract.evidence.quote_spans[0],
                ),
            ),
        )
    else:
        resolution = resolve(
            (item, decision(item, episode="unresolved" if state == "unresolved" else "new_episode"))
        )
    if state in {"excluded", "insufficient"}:
        value = value.model_copy(
            update={"decision": state, "assigned_level": None, "matched_anchor_ids": ()}
        )
    record = aggregate_scores(scoring_input(resolution, value))
    assert criterion(record).score is None
    assert trace(record).mean.denominator == 0
    assert all(w.effective_weight == 0 for w in record.weights)
    assert all(a.assigned_level is None for a in record.gated_assessments)


def test_all_criteria_scored_publish_weighted_competency_and_overall():
    item = source("a")
    resolution = resolve((item, decision(item)))
    values = tuple(
        a.model_copy(
            update={
                "assigned_level": 2 if a.competency == "debugging" else 4,
                "matched_anchor_ids": (
                    f"{a.criterion_id}.l{2 if a.competency == 'debugging' else 4}",
                ),
            }
        )
        for a in all_assessments(item)
    )
    record = aggregate_scores(
        scoring_input(
            resolution,
            *values,
            weights={"debugging": 5.0},
            mandatory=("debugging", "ownership"),
            required=(DIAGNOSTIC,),
        )
    )
    assert record.snapshot.overall_score == 3  # (debugging: 2*5 + five others: 4*1) / 10
    assert record.snapshot.overall_coverage == 1
    assert record.snapshot.status == "published"
    assert record.snapshot.reason_codes == ()
    assert all(s.coverage == 1 and s.reliability == 0.5 for s in record.snapshot.competencies)
    assert record.overall_mean.numerator == 30 and record.overall_mean.denominator == 10


def test_mandatory_and_required_anchor_gates_cannot_be_renormalized_away():
    item = source("a")
    values = [a for a in all_assessments(item) if a.competency != "ownership"]
    record = aggregate_scores(
        scoring_input(
            resolve((item, decision(item))),
            *values,
            thresholds={"min_overall_coverage": 0.5},
            mandatory=("ownership",),
            required=(load_rubric_pack().for_competency("ownership").criteria[0].criterion_id,),
        )
    )
    assert record.snapshot.overall_coverage == pytest.approx(5 / 6)
    assert record.snapshot.overall_score is None
    assert "mandatory_competency_unscoreable:ownership" in record.snapshot.reason_codes
    assert any(
        r.startswith("required_anchor_assessment_missing:") for r in record.snapshot.reason_codes
    )


def test_failure_blocks_every_numeric_score_even_with_complete_evidence():
    item = source("a")
    inputs = scoring_input(resolve((item, decision(item))), *all_assessments(item))
    record = aggregate_scores(
        inputs.model_copy(update={"evaluation_failure_codes": ("prior_timeout",)})
    )
    assert record.snapshot.status == "evaluation_failed"
    assert record.snapshot.overall_score is None
    assert all(s.score is None for s in record.snapshot.competencies)
    assert all(c.score is None for s in record.snapshot.competencies for c in s.criteria)
    assert "prior_timeout" in record.snapshot.reason_codes


def test_required_coverage_uses_weights_and_optional_criteria_do_not_fill_gaps():
    pack = load_rubric_pack().model_dump(mode="json")
    debug = next(r for r in pack["rubrics"] if r["competency"] == "debugging")
    for c in debug["criteria"]:
        c["required"] = c["criterion_id"] != DIAGNOSTIC
        c["weight"] = 2.0
    pack = RubricPack.model_validate(pack)
    item = source("a")
    record = aggregate_scores(
        scoring_input(resolve((item, decision(item))), assessment("a", item), rubric=pack)
    )
    assert criterion(record).score == 3
    assert competency(record).coverage == 0
    assert competency(record).score is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_independent_evidence", 0),
        ("min_independent_evidence", True),
        ("min_effective_weight", 0),
        ("min_effective_weight", float("inf")),
        ("min_competency_coverage", 0),
        ("min_overall_coverage", 1.1),
        ("min_criterion_reliability", float("nan")),
        ("reliability_target_independent_evidence", 0),
    ],
)
def test_publication_configuration_is_strict(field, value):
    with pytest.raises(ValueError):
        configuration(thresholds={field: value})


def test_thresholds_are_required_and_policy_versions_are_not_silently_upgraded():
    with pytest.raises(ValueError):
        AggregationPolicy(configuration_id="not-calibrated")
    policy, profile = configuration()
    with pytest.raises(ValueError):
        AggregationPolicy.model_validate({**policy.model_dump(), "policy_version": "future"})
    with pytest.raises(ValueError):
        ScoringProfile.model_validate({**profile.model_dump(), "competencies": ()})
    with pytest.raises(ValueError):
        configuration(weights={c.competency.value: 0.0 for c in profile.competencies})
    with pytest.raises(ValueError):
        configuration(weights={"debugging": float("nan")})


def test_unknown_required_criterion_is_rejected():
    with pytest.raises(ValueError, match="unknown required criterion"):
        scoring_input(resolve(), required=("debugging.invented",))


def test_empty_history_is_insufficient_at_every_level():
    record = aggregate_scores(scoring_input(resolve()))
    assert record.snapshot.overall_score is None and record.snapshot.overall_coverage == 0
    assert all(c.score is None and c.coverage == 0 for c in record.snapshot.competencies)
    assert not record.weights


@pytest.mark.parametrize(
    "ownership,expected",
    [
        ("personal", 1.0),
        ("shared", 0.75),
        ("team_only", 0.25),
        ("unclear", 0.25),
    ],
)
@pytest.mark.parametrize(
    "specificity,factor", [("concrete", 1.0), ("partial", 0.6), ("vague", 0.2)]
)
def test_versioned_quality_mapping_is_transparent(ownership, expected, specificity, factor):
    item = source("a", ownership=ownership, specificity=specificity)
    record = aggregate_scores(scoring_input(resolve((item, decision(item))), assessment("a", item)))
    weight = record.weights[0]
    assert weight.effective_weight == pytest.approx(expected * factor)
    assert weight.quality.directness == ownership
    assert weight.quality.specificity == specificity
    assert weight.quality.outcome_support == "not_observed"
    assert weight.quality.grounding == "verified"


def test_competency_uses_criterion_weights_and_preserves_missing_weight_in_coverage():
    pack = load_rubric_pack().model_dump(mode="json")
    debug = next(r for r in pack["rubrics"] if r["competency"] == "debugging")
    for c in debug["criteria"]:
        if c["criterion_id"] == DIAGNOSTIC:
            c["weight"] = 3.0
    pack = RubricPack.model_validate(pack)
    item = source("a")
    record = aggregate_scores(
        scoring_input(
            resolve((item, decision(item))),
            assessment("diagnostic", item, level=5),
            assessment("verification", item, criterion="debugging.fix_verification", level=1),
            rubric=pack,
        )
    )
    assert competency(record).score == 4  # (5*3 + 1*1) / 4
    assert competency(record).coverage == 0.5  # covered weight 4 / required weight 8


def test_role_importance_never_changes_evidence_weight_or_criterion_scores():
    item = source("a")
    resolution = resolve((item, decision(item)))
    a = aggregate_scores(scoring_input(resolution, *all_assessments(item)))
    b = aggregate_scores(
        scoring_input(resolution, *all_assessments(item), weights={"debugging": 50.0})
    )
    assert a.weights == b.weights and a.criteria == b.criteria
    assert a.snapshot.competencies == b.snapshot.competencies


def test_judge_cannot_claim_an_unmatched_level_or_reuse_stale_source_bindings():
    item = source("a")
    first = resolve((item, decision(item)))
    inputs = scoring_input(first, assessment("a", item))
    retract = source("b", "I retract my profiling statement.")
    second = resolve(
        (item, decision(item)),
        (
            retract,
            decision(
                retract,
                "retracts",
                (item,),
                proof=retract.evidence.quote_spans[0],
            ),
        ),
    )
    with pytest.raises(ValueError, match="every source|stale"):
        aggregate_scores(inputs.model_copy(update={"history": second.history}))
    new_judgement = bind_judgement(
        draft_for(second, assessment("a", item)), second, rubric=inputs.rubric
    )
    rescored = aggregate_scores(
        inputs.model_copy(
            update={
                "history": second.history,
                "judgement": new_judgement,
            }
        )
    )
    assert criterion(rescored).score is None
