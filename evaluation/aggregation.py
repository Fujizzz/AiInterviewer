"""Self-contained scoring input and audit records; no persistence side effects."""

from typing import Literal, Self

from pydantic import Field, model_validator

from evaluation.contracts import (
    CriterionAssessment,
    EvaluationModel,
    EvaluationResult,
    ScoreSnapshot,
    Text,
    UnitInterval,
)
from evaluation.judge import RubricJudgement
from evaluation.policy import AggregationPolicy, EvidenceQuality, ScoringProfile, WeightFactors
from evaluation.resolution import EvidenceResolution, ResolutionHistory
from evaluation.rubric import RubricPack
from shared.contracts import Competency


class AggregationInput(EvaluationModel):
    history: ResolutionHistory
    rubric: RubricPack
    judgement: RubricJudgement
    policy: AggregationPolicy
    profile: ScoringProfile
    evaluation_failure_codes: tuple[Text, ...] = ()
    supersedes_snapshot_id: Text | None = None
    reevaluation_reason: Text | None = None

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        if (self.supersedes_snapshot_id is None) != (self.reevaluation_reason is None):
            raise ValueError("reevaluation requires both previous snapshot ID and reason")
        criteria = {c.criterion_id for r in self.rubric.rubrics for c in r.criteria}
        if not set(self.profile.required_criterion_ids) <= criteria:
            raise ValueError("profile references unknown required criterion")
        return self


class WeightTrace(EvaluationModel):
    assessment_id: Text
    evidence_id: Text
    independence_group_id: Text
    quality: EvidenceQuality
    factors: WeightFactors
    effective_weight: UnitInterval
    selected: bool
    reason_codes: tuple[Text, ...]


class MeanTrace(EvaluationModel):
    numerator: float
    denominator: float = Field(ge=0)


class CriterionTrace(EvaluationModel):
    criterion_id: Text
    mean: MeanTrace
    independent_evidence_count: int = Field(ge=0)
    quality_mean: UnitInterval
    agreement_factor: UnitInterval
    support_factor: UnitInterval
    reliability: UnitInterval


class CompetencyTrace(EvaluationModel):
    competency: Competency
    mean: MeanTrace
    coverage: MeanTrace
    reliability: MeanTrace


class AggregationRecord(EvaluationModel):
    trace_version: Literal["aggregation-trace-1.0.0"] = "aggregation-trace-1.0.0"
    eligibility_policy_version: Literal["episode-selection-1.0.0"] = "episode-selection-1.0.0"
    inputs: AggregationInput
    gated_assessments: tuple[CriterionAssessment, ...]
    weights: tuple[WeightTrace, ...]
    criteria: tuple[CriterionTrace, ...]
    competencies: tuple[CompetencyTrace, ...]
    overall_mean: MeanTrace
    overall_coverage: MeanTrace
    snapshot: ScoreSnapshot


class ScoredEvaluation(EvaluationModel):
    evaluation: EvaluationResult
    resolution: EvidenceResolution | None = None
    aggregation: AggregationRecord | None = None

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.evaluation.status == "failed":
            if self.resolution is not None or self.aggregation is not None:
                raise ValueError("failed evaluation cannot publish scoring artifacts")
        elif self.resolution is None or self.aggregation is None:
            raise ValueError("completed scoring requires resolution and aggregation")
        if self.aggregation is not None:
            if self.evaluation.score_snapshot != self.aggregation.snapshot:
                raise ValueError("evaluation snapshot must match aggregation")
            if self.resolution.history != self.aggregation.inputs.history:
                raise ValueError("aggregation must use the returned resolution history")
            if self.evaluation.assessments != self.aggregation.gated_assessments:
                raise ValueError("evaluation assessments must match aggregation")
        return self
