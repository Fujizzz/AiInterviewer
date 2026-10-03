"""Pure, versioned score aggregation and full-source replay, without model calls."""

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from evaluation.aggregation import (
    AggregationInput,
    AggregationRecord,
    CompetencyTrace,
    CriterionTrace,
    MeanTrace,
    WeightTrace,
)
from evaluation.contracts import (
    CompetencyScore,
    CriterionScore,
    EvidenceContribution,
    ScoreSnapshot,
)
from evaluation.eligibility import plan_criterion_contributions
from evaluation.judge import content_digest, validate_judgement
from evaluation.policy import quality_and_factors
from evaluation.resolver import replay_resolution
from shared.contracts import Competency

ZERO, ONE = Decimal(0), Decimal(1)


def _d(value) -> Decimal:
    return Decimal(str(value))


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else ZERO


def _mean_trace(numerator, denominator):
    return MeanTrace(numerator=float(numerator), denominator=float(denominator))


def aggregate_scores(inputs: AggregationInput) -> AggregationRecord:
    """Return a new immutable record. Thresholds have no implicit production defaults."""
    with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
        return _aggregate(inputs)


def _aggregate(inputs: AggregationInput) -> AggregationRecord:
    inputs = AggregationInput.model_validate(inputs.model_dump())
    resolution = replay_resolution(inputs.history)
    judgement = validate_judgement(inputs.judgement, resolution, rubric=inputs.rubric)
    # Normalize unordered configuration collections before identity generation.
    inputs = inputs.model_copy(
        update={
            "judgement": judgement,
            "profile": inputs.profile.model_copy(
                update={
                    "competencies": tuple(
                        sorted(inputs.profile.competencies, key=lambda c: c.competency)
                    ),
                    "required_criterion_ids": tuple(sorted(inputs.profile.required_criterion_ids)),
                }
            ),
            "evaluation_failure_codes": tuple(sorted(set(inputs.evaluation_failure_codes))),
        }
    )
    plan = plan_criterion_contributions(judgement.assessments, resolution, rubric=inputs.rubric)
    items = {item.evidence_id: item for item in resolution.evidence_items}
    states = {state.evidence_id: state for state in resolution.states}
    eligibility = {c.criterion_id: c for c in plan.criteria}
    thresholds = inputs.policy.thresholds
    failed = bool(inputs.evaluation_failure_codes)
    weight_traces, criterion_traces, competency_traces, competencies = [], [], [], []
    criterion_values, competency_values = {}, {}

    for competency in sorted(Competency):
        rubric = inputs.rubric.for_competency(competency)
        scores = []
        for criterion in sorted(rubric.criteria, key=lambda c: c.criterion_id):
            local = [a for a in plan.assessments if a.criterion_id == criterion.criterion_id]
            candidates, rows = {}, []
            for assessment in local:
                for key in assessment.evidence_ids:
                    item, state = items[key], states[key]
                    quality, factors = quality_and_factors(item, state, assessment)
                    weight = ONE
                    for factor in factors.model_dump().values():
                        weight *= _d(factor)
                    row = WeightTrace(
                        assessment_id=assessment.assessment_id,
                        evidence_id=key,
                        independence_group_id=item.independence_group_id,
                        quality=quality,
                        factors=factors,
                        effective_weight=float(weight),
                        selected=False,
                        reason_codes=assessment.reason_codes,
                    )
                    rows.append(row)
                    if weight > 0:
                        candidates.setdefault(item.independence_group_id, []).append(
                            (weight, item, assessment)
                        )
            selected, contributions, weighted_levels = set(), [], []
            for group_id, choices in sorted(candidates.items()):
                # Highest effective quality, never highest level. Stable ID breaks ties.
                weight, item, assessment = sorted(
                    choices,
                    key=lambda value: (-value[0], value[1].evidence_id, value[2].assessment_id),
                )[0]
                selected.add((assessment.assessment_id, item.evidence_id))
                supplemental = sorted(
                    {
                        key
                        for a in local
                        for key in a.evidence_ids
                        if items[key].independence_group_id == group_id
                        and states[key].status in {"eligible", "duplicate"}
                        and key != item.evidence_id
                    }
                )
                contributions.append(
                    EvidenceContribution(
                        assessment_id=assessment.assessment_id,
                        evidence_ids=(item.evidence_id, *supplemental),
                        independence_group_id=group_id,
                        assigned_level=assessment.assigned_level,
                        effective_weight=float(weight),
                        reason_codes=(
                            "anchor_matched",
                            "highest_effective_quality",
                            "one_per_episode",
                        ),
                    )
                )
                weighted_levels.append((_d(assessment.assigned_level), weight))
            for row in rows:
                chosen = (row.assessment_id, row.evidence_id) in selected
                reason = (
                    "selected_episode_contribution"
                    if chosen
                    else "supplemental_only"
                    if row.effective_weight > 0
                    else "zero_weight_gate"
                )
                weight_traces.append(
                    row.model_copy(
                        update={
                            "selected": chosen,
                            "reason_codes": tuple(sorted(set((*row.reason_codes, reason)))),
                        }
                    )
                )
            numerator = sum((level * weight for level, weight in weighted_levels), ZERO)
            denominator = sum((weight for _, weight in weighted_levels), ZERO)
            count = len(weighted_levels)
            mean = _ratio(numerator, denominator)
            quality_mean = _ratio(denominator, _d(count))
            deviation = _ratio(
                sum((abs(level - mean) * weight for level, weight in weighted_levels), ZERO),
                denominator,
            )
            agreement = ONE - deviation / 4 if count else ZERO
            support = min(ONE, _d(count) / thresholds.reliability_target_independent_evidence)
            reliability = quality_mean * agreement * support
            reasons = []
            if failed:
                reasons.append("evaluation_failure")
            if eligibility[criterion.criterion_id].has_unresolved_contradiction:
                reasons.append("unresolved_contradiction")
                reliability = ZERO
            if count < thresholds.min_independent_evidence:
                reasons.append("insufficient_independent_evidence")
            if denominator < _d(thresholds.min_effective_weight):
                reasons.append("insufficient_effective_weight")
            if reliability < _d(thresholds.min_criterion_reliability):
                reasons.append("insufficient_criterion_reliability")
            published = not reasons
            scores.append(
                CriterionScore(
                    criterion_id=criterion.criterion_id,
                    criterion_weight=criterion.weight,
                    score=float(mean) if published else None,
                    status="evaluation_failed"
                    if failed
                    else "insufficient_evidence"
                    if reasons
                    else "published",
                    reason_codes=tuple(reasons),
                    contributions=tuple(contributions),
                )
            )
            criterion_values[criterion.criterion_id] = (mean, reliability, published)
            criterion_traces.append(
                CriterionTrace(
                    criterion_id=criterion.criterion_id,
                    mean=_mean_trace(numerator, denominator),
                    independent_evidence_count=count,
                    quality_mean=float(quality_mean),
                    agreement_factor=float(agreement),
                    support_factor=float(support),
                    reliability=float(reliability),
                )
            )

        numerator = denominator = covered = required = reliable = ZERO
        for criterion in sorted(rubric.criteria, key=lambda c: c.criterion_id):
            mean, reliability, published = criterion_values[criterion.criterion_id]
            weight = _d(criterion.weight)
            if published:
                numerator += mean * weight
                denominator += weight
            if criterion.required:
                required += weight
                if published:
                    covered += weight
                    reliable += reliability * weight
        coverage, reliability = _ratio(covered, required), _ratio(reliable, required)
        reasons = []
        if failed:
            reasons.append("evaluation_failure")
        if not denominator:
            reasons.append("no_scoreable_criterion")
        if coverage < _d(thresholds.min_competency_coverage):
            reasons.append("insufficient_competency_coverage")
        if reliability < _d(thresholds.min_competency_reliability):
            reasons.append("insufficient_competency_reliability")
        mean = _ratio(numerator, denominator)
        published = not reasons
        competencies.append(
            CompetencyScore(
                competency=competency,
                score=float(mean) if published else None,
                coverage=float(coverage),
                reliability=float(reliability),
                criteria=tuple(scores),
                status="evaluation_failed"
                if failed
                else "insufficient_evidence"
                if reasons
                else "published",
                reason_codes=tuple(reasons),
            )
        )
        competency_values[competency] = (mean, coverage, published)
        competency_traces.append(
            CompetencyTrace(
                competency=competency,
                mean=_mean_trace(numerator, denominator),
                coverage=_mean_trace(covered, required),
                reliability=_mean_trace(reliable, required),
            )
        )

    numerator = denominator = covered = total = ZERO
    reasons = []
    for importance in inputs.profile.competencies:
        mean, coverage, published = competency_values[importance.competency]
        weight = _d(importance.weight)
        total += weight
        if published:
            numerator += mean * weight
            denominator += weight
            covered += coverage * weight
        elif importance.mandatory:
            reasons.append(f"mandatory_competency_unscoreable:{importance.competency.value}")
    overall_coverage = _ratio(covered, total)
    if overall_coverage < _d(thresholds.min_overall_coverage):
        reasons.append("insufficient_overall_coverage")
    if not denominator:
        reasons.append("no_scoreable_competency")
    for key in inputs.profile.required_criterion_ids:
        if not criterion_values[key][2]:
            reasons.append(f"required_anchor_assessment_missing:{key}")
    if any(conflict.status == "unresolved" for conflict in resolution.conflicts):
        reasons.append("unresolved_contradiction")
    if failed:
        reasons.extend(("evaluation_failure", *inputs.evaluation_failure_codes))
    snapshot_data = dict(
        interview_id=inputs.history.interview_id,
        rubric_version=inputs.rubric.rubric_version,
        aggregation_policy_version=inputs.policy.policy_version,
        competencies=tuple(competencies),
        overall_coverage=float(overall_coverage),
        overall_score=float(_ratio(numerator, denominator)) if not reasons else None,
        overall_score_publishable=not reasons,
        status="evaluation_failed"
        if failed
        else "insufficient_evidence"
        if reasons
        else "published",
        reason_codes=tuple(sorted(set(reasons))),
        supersedes_snapshot_id=inputs.supersedes_snapshot_id,
        reevaluation_reason=inputs.reevaluation_reason,
    )
    snapshot = ScoreSnapshot(
        snapshot_id="snapshot-v1-" + content_digest(inputs),
        **snapshot_data,
    )
    return AggregationRecord(
        inputs=inputs,
        gated_assessments=plan.assessments,
        weights=tuple(sorted(weight_traces, key=lambda r: (r.assessment_id, r.evidence_id))),
        criteria=tuple(criterion_traces),
        competencies=tuple(competency_traces),
        overall_mean=_mean_trace(numerator, denominator),
        overall_coverage=_mean_trace(covered, total),
        snapshot=snapshot,
    )


def replay_aggregation(record: AggregationRecord) -> ScoreSnapshot:
    """Recompute all math and gates from embedded sources; reject changed traces/scores."""
    record = AggregationRecord.model_validate(record.model_dump())
    rebuilt = aggregate_scores(record.inputs)
    if rebuilt != record:
        raise ValueError("aggregation record does not match deterministic replay")
    return rebuilt.snapshot
