"""Explicit test-only publication thresholds; these are not production defaults."""

from evaluation.aggregation import AggregationInput
from evaluation.judge import AssessmentDraft, JudgeDraft, UnmappedEvidence, bind_judgement
from evaluation.policy import AggregationPolicy, ScoringProfile
from evaluation.rubric import load_rubric_pack
from shared.contracts import Competency
from tests.evaluation.resolver_helpers import assessment


def draft_for(resolution, *assessments, rubric=None):
    rubric = rubric or load_rubric_pack()
    values = [
        AssessmentDraft.model_validate(
            a.model_dump(
                exclude={
                    "schema_version",
                    "assessment_id",
                    "rubric_version",
                }
            )
        )
        for a in assessments
    ]
    covered = {a.criterion_id for a in values}
    values += [
        AssessmentDraft(
            competency=r.competency,
            criterion_id=c.criterion_id,
            decision="insufficient",
            reason_codes=("no_evidence",),
            concise_rationale="No relevant candidate behavior was supplied.",
        )
        for r in rubric.rubrics
        for c in r.criteria
        if c.criterion_id not in covered
    ]
    refs = {key for a in values for key in (*a.evidence_ids, *a.counter_evidence_ids)}
    return JudgeDraft(
        assessments=tuple(values),
        unmapped_evidence=tuple(
            UnmappedEvidence(
                evidence_id=item.evidence_id,
                reason_code="no_relevant_criterion",
                concise_rationale="No relevant behavior for this rubric.",
            )
            for item in resolution.evidence_items
            if item.evidence_id not in refs
        ),
    )


def configuration(*, thresholds=None, weights=None, mandatory=(), required=()):
    policy = AggregationPolicy(
        configuration_id="test-only-uncalibrated",
        thresholds={
            "min_independent_evidence": 1,
            "min_effective_weight": 0.1,
            "min_criterion_reliability": 0.0,
            "min_competency_coverage": 0.1,
            "min_competency_reliability": 0.0,
            "min_overall_coverage": 1.0,
            "reliability_target_independent_evidence": 2,
            **(thresholds or {}),
        },
    )
    profile = ScoringProfile(
        profile_id="test-role",
        competencies=tuple(
            dict(
                competency=c,
                weight=(weights or {}).get(c.value, 1.0),
                mandatory=c.value in mandatory,
            )
            for c in Competency
        ),
        required_criterion_ids=required,
    )
    return policy, profile


def scoring_input(resolution, *assessments, rubric=None, **config):
    rubric = rubric or load_rubric_pack()
    policy, profile = configuration(**config)
    return AggregationInput(
        history=resolution.history,
        rubric=rubric,
        policy=policy,
        profile=profile,
        judgement=bind_judgement(
            draft_for(resolution, *assessments, rubric=rubric), resolution, rubric=rubric
        ),
    )


def all_assessments(item, *, level=4, rubric=None):
    rubric = rubric or load_rubric_pack()
    return tuple(
        assessment(c.criterion_id, item, criterion=c.criterion_id, level=level)
        for r in rubric.rubrics
        for c in r.criteria
    )
