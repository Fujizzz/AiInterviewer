"""Publish a bounded public report from validated formal receipts; never invent evidence."""

from evaluation.aggregator import aggregate_scores
from evaluation.persistence import failure_codes
from shared.contracts import Competency


def scoreable_aggregation(aggregation):
    """Project valid receipts without letting an unassessed turn erase their scores.

    The original strict snapshot remains unchanged. All evidence, contradiction,
    rubric and quality gates still run; only known missing-turn flags are removed
    from this partial projection. Callers must mark the result provisional.
    """
    if aggregation and aggregation.inputs.evaluation_failure_codes:
        codes = aggregation.inputs.evaluation_failure_codes
        if all(code.startswith("unassessed_feedback:") for code in codes):
            return aggregate_scores(
                aggregation.inputs.model_copy(update={"evaluation_failure_codes": ()})
            )
    return aggregation


def latest_scoring_record(records):
    return next(
        (
            r.scored.aggregation
            for r in reversed(records)
            if r.scored.evaluation.status == "completed" and r.scored.aggregation is not None
        ),
        None,
    )


def formal_publication(context, records):
    """Strict scores stay immutable. Provisional scores use only published criteria."""
    gaps = failure_codes(
        context.processed_feedback_ids,
        records,
        unobserved_feedback_ids=context.unobserved_feedback_ids,
    )
    original = latest_scoring_record(records)
    latest = scoreable_aggregation(original)
    snapshot = latest.snapshot if latest else None
    incomplete = bool(gaps or (original and original.inputs.evaluation_failure_codes))
    blocked = bool(
        not snapshot
        or snapshot.status == "evaluation_failed"
        or (snapshot and "unresolved_contradiction" in snapshot.reason_codes)
    )
    reasons = ["evaluation_incomplete", *gaps] if incomplete else []
    if original:
        reasons += list(original.inputs.evaluation_failure_codes)
    if snapshot:
        reasons += list(snapshot.reason_codes)
    else:
        reasons.append("no_scoring_evidence")
    competencies = {}
    for name in Competency:
        c = (
            next((c for c in snapshot.competencies if c.competency == name), None)
            if snapshot
            else None
        )
        usable = (
            [x for x in c.criteria if x.status == "published" and x.score is not None]
            if c and not blocked
            else []
        )
        denominator = sum(x.criterion_weight for x in usable)
        provisional = (
            sum(x.score * x.criterion_weight for x in usable) / denominator if denominator else None
        )
        score = (c.score if c.score is not None else provisional) if c and not blocked else None
        identifiers = {e.independence_group_id for x in usable for e in x.contributions}
        competencies[name.value] = dict(
            score=score,
            coverage=c.coverage if c else 0.0,
            evidence_count=len(identifiers),
            max_verified_difficulty=0,
            status="unavailable"
            if score is None
            else "published"
            if c.score is not None and not incomplete
            else "provisional",
            reliability=c.reliability if c else 0.0,
            reason_codes=(list(c.reason_codes) if c else ["no_scoring_evidence"])
            + (["evaluation_incomplete"] if incomplete else []),
        )
    weighted = (
        [
            (x.weight, competencies[x.competency.value]["score"])
            for x in latest.inputs.profile.competencies
            if competencies[x.competency.value]["score"] is not None
        ]
        if latest
        else []
    )
    denominator = sum(w for w, _ in weighted)
    overall = sum(w * v for w, v in weighted) / denominator if denominator else None
    status = "unavailable" if overall is None else "provisional"
    if not blocked and not incomplete and snapshot.overall_score_publishable:
        overall, status = snapshot.overall_score, "published"
    if overall is None and not reasons:
        reasons.append("no_scoring_evidence")
    return dict(
        overall_score=round(overall, 4) if overall is not None else None,
        competencies=competencies,
        scoring_source="formal_evaluation",
        score_status=status,
        overall_coverage=snapshot.overall_coverage if snapshot else 0.0,
        score_reasons=sorted(set(reasons)),
        missing_competencies=[
            name.value
            for name in Competency
            if not snapshot
            or next(c for c in snapshot.competencies if c.competency == name).score is None
        ],
        score_snapshot_id=original.snapshot.snapshot_id if original else None,
        score_basis_snapshot_id=snapshot.snapshot_id if snapshot else None,
        unscored_answer_count=len(
            set(gaps) | set(original.inputs.evaluation_failure_codes if original else ())
        ),
    )
