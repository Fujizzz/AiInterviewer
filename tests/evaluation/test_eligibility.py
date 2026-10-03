import pytest

from evaluation.eligibility import plan_criterion_contributions
from evaluation.rubric import load_rubric_pack
from tests.evaluation.resolver_helpers import assessment, decision, resolve, source


def plan(resolution, *assessments):
    return plan_criterion_contributions(assessments, resolution, rubric=load_rubric_pack())


def test_duplicates_do_not_raise_count_or_selected_level():
    original = source("a")
    duplicate = source("b", "I found the contention using a profiler.")
    before = resolve((original, decision(original)))
    after = resolve(
        (original, decision(original)),
        (duplicate, decision(duplicate, "duplicate", (original,))),
    )
    original_assessment = assessment("original", original, level=3)
    first = plan(before, original_assessment)
    second = plan(after, original_assessment, assessment("duplicate", duplicate, level=5))
    assert (
        first.criteria[0].independent_evidence_count
        == second.criteria[0].independent_evidence_count
        == 1
    )
    assert first.criteria[0].groups[0].evidence_id == second.criteria[0].groups[0].evidence_id
    assert second.criteria[0].groups[0].assessment_id == "original"
    excluded = next(a for a in second.assessments if a.decision == "excluded")
    assert excluded.assigned_level is None and not excluded.matched_anchor_ids
    assert original_assessment.assigned_level == 3


def test_best_quality_followup_contributes_once_and_other_experiences_count_separately():
    original = source("a", specificity="partial", ownership="team_only")
    refinement = source("b", "I compared the profiler's blocked-thread stacks.")
    independent = source("c", "In a second outage I isolated connection pool contention.")
    resolution = resolve(
        (original, decision(original)),
        (refinement, decision(refinement, "refines", (original,))),
        (independent, decision(independent)),
    )
    assessments = (
        assessment("a", original, level=5),
        assessment("b", refinement, level=3),
        assessment("c", independent, level=4),
    )
    result = plan(resolution, *assessments)
    assert result.criteria[0].independent_evidence_count == 2
    selected = {group.evidence_id: group for group in result.criteria[0].groups}
    assert set(selected) == {refinement.evidence.evidence_id, independent.evidence.evidence_id}
    assert selected[refinement.evidence.evidence_id].supplemental_evidence_ids == (
        original.evidence.evidence_id,
    )
    assert plan(resolution, *reversed(assessments)) == result


def test_episode_cap_is_per_criterion_and_does_not_hide_unrelated_facts():
    diagnosis = source("a")
    validation = source("b", "I validated latency under sustained load.")
    contrary = source("c", "I did not run a profiler in this incident.")
    unrelated = source("d", "In another incident I measured cache miss rates.")
    resolution = resolve(
        (diagnosis, decision(diagnosis)),
        (validation, decision(validation, same=(diagnosis,))),
        (contrary, decision(contrary, "contradicts", (diagnosis,))),
        (unrelated, decision(unrelated)),
    )
    result = plan(
        resolution,
        assessment("diagnosis", diagnosis),
        assessment("contrary", contrary),
        assessment("validation", validation, criterion="debugging.fix_verification"),
        assessment("independent", unrelated),
    )
    diagnostic, verified = result.criteria
    assert diagnostic.has_unresolved_contradiction and diagnostic.independent_evidence_count == 1
    assert not verified.has_unresolved_contradiction and verified.independent_evidence_count == 1
    disputed = [a for a in result.assessments if a.decision == "disputed"]
    assert len(disputed) == 2
    for item in disputed:
        assert item.assigned_level is None
        assert item.evidence_ids and item.counter_evidence_ids
        assert not set(item.evidence_ids) & set(item.counter_evidence_ids)
        assert not item.matched_anchor_ids
    assert (
        next(a for a in result.assessments if a.assessment_id == "validation").assigned_level == 3
    )


def test_both_conflict_sides_in_one_assessment_remain_disjoint_after_gating():
    first, second = source("a"), source("b", "I never used profiling.")
    resolution = resolve(
        (first, decision(first)), (second, decision(second, "contradicts", (first,)))
    )
    result = plan(resolution, assessment("both", first, second))
    assert result.assessments[0].decision == "disputed"
    assert result.criteria[0].groups == ()
    assert result.assessments[0].assigned_level is None
    assert plan(resolution, *result.assessments) == result


@pytest.mark.parametrize("blocked", ["retracted", "uncertain"])
def test_blocked_assessments_need_rejudging_instead_of_zero_levels(blocked):
    original = source("a")
    if blocked == "retracted":
        withdrawn = source("b", "I retract my original statement about profiling.")
        resolution = resolve(
            (original, decision(original)),
            (
                withdrawn,
                decision(
                    withdrawn,
                    "retracts",
                    (original,),
                    proof=withdrawn.evidence.quote_spans[0],
                ),
            ),
        )
    else:
        resolution = resolve((original, decision(original, episode="unresolved")))
    result = plan(resolution, assessment("a", original))
    assert result.assessments[0].decision == "insufficient"
    assert result.assessments[0].assigned_level is None
    assert not result.criteria[0].groups


def test_resolving_conflict_does_not_restore_a_stale_level():
    old, counter = source("old"), source("counter", "I never ran a profiler.")
    withdrawn = source("withdrawn", "I retract my original profiling claim.")
    before = resolve((old, decision(old)), (counter, decision(counter, "contradicts", (old,))))
    stale = plan(before, assessment("counter", counter)).assessments[0]
    after = resolve(
        (old, decision(old)),
        (counter, decision(counter, "contradicts", (old,))),
        (
            withdrawn,
            decision(withdrawn, "retracts", (old,), proof=withdrawn.evidence.quote_spans[0]),
        ),
    )
    result = plan(after, stale)
    assert result.assessments[0].decision == "insufficient"
    assert result.assessments[0].reason_codes == ("reassessment_required",)
    assert result.assessments[0].assigned_level is None


def test_forged_eligibility_view_is_replayed_from_original_sources():
    original = source("a")
    resolution = resolve((original, decision(original, episode="unresolved")))
    tampered = resolution.model_copy(
        update={"states": (resolution.states[0].model_copy(update={"status": "eligible"}),)}
    )
    assert not plan(tampered, assessment("a", original)).criteria[0].groups


@pytest.mark.parametrize("problem", ["evidence", "criterion", "rubric", "level", "duplicate_id"])
def test_unknown_refs_and_conflicting_level_assignments_fail_closed(problem):
    original = source("a")
    resolution = resolve((original, decision(original)))
    original_assessment = assessment("a", original)
    values = [original_assessment]
    if problem == "evidence":
        values = [original_assessment.model_copy(update={"evidence_ids": ("invented",)})]
    elif problem == "criterion":
        values = [original_assessment.model_copy(update={"criterion_id": "debugging.invented"})]
    elif problem == "rubric":
        values = [original_assessment.model_copy(update={"rubric_version": "future"})]
    elif problem == "level":
        values.append(assessment("b", original, level=4))
    else:
        values *= 2
    with pytest.raises(ValueError):
        plan(resolution, *values)


def test_empty_assessments_are_valid_and_produce_no_numeric_result():
    result = plan(resolve())
    assert result.assessments == result.criteria == ()
    assert "score" not in result.model_dump()


def test_withdrawn_counter_evidence_also_requires_reassessment():
    supporting = source("supporting")
    counter = source("counter", "The team ran the final validation without me.")
    withdrawal = source("withdrawal", "I retract my statement about the team's validation.")
    resolution = resolve(
        (supporting, decision(supporting)),
        (counter, decision(counter, same=(supporting,))),
        (
            withdrawal,
            decision(
                withdrawal,
                "retracts",
                (counter,),
                proof=withdrawal.evidence.quote_spans[0],
            ),
        ),
    )
    original = assessment("assessment", supporting).model_copy(
        update={
            "counter_evidence_ids": (counter.evidence.evidence_id,),
        }
    )
    result = plan(resolution, original)
    assert result.assessments[0].assigned_level is None
    assert result.assessments[0].reason_codes == ("claim_retracted",)
    assert not result.criteria[0].groups
