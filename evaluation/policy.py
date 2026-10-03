"""Explicit publication configuration and versioned, uncalibrated scoring rules."""

from typing import Literal, Self

from pydantic import Field, model_validator

from evaluation.contracts import EvaluationModel, Text, UnitInterval, require_unique
from shared.contracts import Competency

AGGREGATION_POLICY_VERSION = "aggregation-1.0.0"


class PublicationThresholds(EvaluationModel):
    # No production defaults: callers must supply a reviewed/calibrated configuration.
    min_independent_evidence: int = Field(strict=True, ge=1)
    min_effective_weight: float = Field(strict=True, gt=0)
    min_criterion_reliability: UnitInterval
    min_competency_coverage: float = Field(strict=True, gt=0, le=1)
    min_competency_reliability: UnitInterval
    min_overall_coverage: float = Field(strict=True, gt=0, le=1)
    reliability_target_independent_evidence: int = Field(strict=True, ge=1)


class AggregationPolicy(EvaluationModel):
    policy_version: Literal["aggregation-1.0.0"] = AGGREGATION_POLICY_VERSION
    configuration_id: Text
    thresholds: PublicationThresholds


class CompetencyImportance(EvaluationModel):
    competency: Competency
    weight: float = Field(strict=True, ge=0)
    mandatory: bool = Field(strict=True)


class ScoringProfile(EvaluationModel):
    profile_id: Text
    competencies: tuple[CompetencyImportance, ...]
    required_criterion_ids: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        require_unique([c.competency for c in self.competencies], "competencies")
        require_unique(self.required_criterion_ids, "required_criterion_ids")
        if {c.competency for c in self.competencies} != set(Competency):
            raise ValueError("profile must explicitly include all six competencies")
        if not any(c.weight > 0 for c in self.competencies):
            raise ValueError("profile requires positive total importance")
        return self


class EvidenceQuality(EvaluationModel):
    grounding: Literal["verified"]
    relevance: Literal["anchor_matched", "not_scoreable"]
    directness: Literal["personal", "shared", "team_only", "unclear", "hypothetical", "opinion"]
    specificity: Literal["concrete", "partial", "vague"]
    outcome_support: Literal["reported_outcome", "not_observed", "hypothetical"]
    consistency: Literal["eligible", "duplicate", "disputed", "retracted", "insufficient"]
    independence: Literal["resolved_episode", "unresolved"]
    reason_codes: tuple[Text, ...]


class WeightFactors(EvaluationModel):
    grounding_gate: UnitInterval
    relevance_gate: UnitInterval
    directness_weight: UnitInterval
    specificity_weight: UnitInterval
    consistency_weight: UnitInterval
    independence_weight: UnitInterval


def quality_and_factors(item, state, assessment) -> tuple[EvidenceQuality, WeightFactors]:
    """Mapping v1; reported outcomes are audit labels, not externally verified facts.

    Outcome support deliberately is not a bonus multiplier. Difficulty, position
    importance and assigned level never enter evidence weight or selection.
    """
    directness = item.ownership_scope
    if item.factuality != "reported_experience":
        directness = item.factuality
    relevance = "anchor_matched" if assessment.decision == "included" else "not_scoreable"
    independence = "unresolved" if state.status == "insufficient" else "resolved_episode"
    outcome = (
        "hypothetical"
        if item.factuality == "hypothetical"
        else "reported_outcome"
        if item.evidence_kind == "outcome" and item.factuality == "reported_experience"
        else "not_observed"
    )
    quality = EvidenceQuality(
        grounding="verified",
        relevance=relevance,
        directness=directness,
        specificity=item.specificity,
        outcome_support=outcome,
        consistency=state.status,
        independence=independence,
        reason_codes=(
            "exact_source_verified",
            relevance,
            f"directness_{directness}",
            f"specificity_{item.specificity}",
            outcome,
            f"consistency_{state.status}",
            independence,
        ),
    )
    factors = WeightFactors(
        grounding_gate=1.0,
        relevance_gate=float(relevance == "anchor_matched"),
        directness_weight={
            "personal": 1.0,
            "shared": 0.75,
            "team_only": 0.25,
            "unclear": 0.25,
            "hypothetical": 0.5,
            "opinion": 0.25,
        }[directness],
        specificity_weight={"concrete": 1.0, "partial": 0.6, "vague": 0.2}[item.specificity],
        consistency_weight=float(state.status == "eligible"),
        independence_weight=float(independence == "resolved_episode"),
    )
    return quality, factors
