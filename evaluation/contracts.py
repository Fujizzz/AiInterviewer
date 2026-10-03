"""Evaluation-owned v1 contracts, independent of the live v2 Agent port.

These models describe data, not extraction, judging or scoring algorithms. Offsets
are zero-based Python Unicode character offsets with an exclusive end. Quotes are
never stripped or normalized. Collections are tuples so ledger/snapshot values can
be serialized without callers mutating nested collections in place.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts import AnswerAnalysis, CandidateAnswer, Competency

EVALUATION_SCHEMA_VERSION = "1.0"
Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Level = Annotated[int, Field(strict=True, ge=1, le=5)]
Score = Annotated[float, Field(ge=1, le=5, strict=True)]
UnitInterval = Annotated[float, Field(ge=0, le=1, strict=True)]


def require_unique(values: tuple | list, name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class VersionedModel(EvaluationModel):
    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION


class QuoteSpan(EvaluationModel):
    quote: Text
    char_start: int = Field(strict=True, ge=0)
    char_end: int = Field(strict=True, gt=0)

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if self.char_end - self.char_start != len(self.quote):
            raise ValueError("quote length must equal char_end - char_start")
        return self

    def validate_answer_text(self, answer_text: str) -> None:
        """Check exact grounding against the supplied current answer, without repair."""
        if answer_text[self.char_start : self.char_end] != self.quote:
            raise ValueError("quote/span must match the current answer exactly")


class EvidenceRelation(str, Enum):
    NEW = "new"
    DUPLICATE = "duplicate"
    REFINES = "refines"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    RETRACTS = "retracts"


class EvidenceItem(VersionedModel):
    evidence_id: Text
    answer_id: Text
    question_id: Text
    thread_id: Text
    project_id: Text | None = None
    quote_spans: tuple[QuoteSpan, ...] = Field(min_length=1)
    normalized_claim: Text
    evidence_kind: Literal[
        "personal_action", "technical_explanation", "decision", "outcome", "reflection"
    ]
    ownership_scope: Literal["personal", "shared", "team_only", "unclear"]
    # A reported experience is not an externally verified fact.
    factuality: Literal["reported_experience", "hypothetical", "opinion", "unclear"]
    specificity: Literal["concrete", "partial", "vague"]
    related_evidence_ids: tuple[Text, ...] = ()
    relation: EvidenceRelation = EvidenceRelation.NEW
    independence_group_id: Text | None = None
    extraction_version: Text

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        previous_end = -1
        for span in self.quote_spans:
            if span.char_start < previous_end:
                raise ValueError("quote_spans must be ordered and non-overlapping")
            previous_end = span.char_end
        require_unique(self.related_evidence_ids, "related_evidence_ids")
        if self.evidence_id in self.related_evidence_ids:
            raise ValueError("evidence cannot reference itself")
        if (self.relation == EvidenceRelation.NEW) != (not self.related_evidence_ids):
            raise ValueError("new evidence has no related IDs; other relations require related IDs")
        return self

    def validate_answer(self, answer: CandidateAnswer) -> None:
        """Bind spans to their answer identity; semantic validation comes later."""
        if (answer.answer_id, answer.question_id) != (self.answer_id, self.question_id):
            raise ValueError("evidence must reference the current answer and question")
        for span in self.quote_spans:
            span.validate_answer_text(answer.text)


class CriterionAssessment(VersionedModel):
    assessment_id: Text
    competency: Competency
    criterion_id: Text
    rubric_version: Text
    evidence_ids: tuple[Text, ...] = ()
    assigned_level: Level | None = None
    matched_anchor_ids: tuple[Text, ...] = ()
    decision: Literal["included", "insufficient", "excluded", "disputed"]
    reason_codes: tuple[Text, ...] = Field(min_length=1)
    concise_rationale: Text
    counter_evidence_ids: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        for name in ("evidence_ids", "matched_anchor_ids", "counter_evidence_ids", "reason_codes"):
            require_unique(getattr(self, name), name)
        if set(self.evidence_ids) & set(self.counter_evidence_ids):
            raise ValueError("supporting and counter evidence must be distinct")
        if self.decision == "included":
            if self.assigned_level is None or not self.evidence_ids or not self.matched_anchor_ids:
                raise ValueError("included assessments require evidence, a level and anchors")
        elif self.assigned_level is not None or self.matched_anchor_ids:
            raise ValueError("unscored assessments must not assign a level or anchors")
        if self.decision == "disputed" and (not self.evidence_ids or not self.counter_evidence_ids):
            raise ValueError("disputed assessments require supporting and counter evidence")
        return self


class EvidenceContribution(EvaluationModel):
    """Recorded aggregation inputs; the phase-four policy will produce these."""

    assessment_id: Text
    evidence_ids: tuple[Text, ...] = Field(min_length=1)
    independence_group_id: Text
    assigned_level: Level
    effective_weight: UnitInterval
    reason_codes: tuple[Text, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        require_unique(self.evidence_ids, "evidence_ids")
        require_unique(self.reason_codes, "reason_codes")
        return self


class ScoreValue(EvaluationModel):
    score: Score | None = None
    status: Literal["published", "insufficient_evidence", "evaluation_failed"]
    reason_codes: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def validate_publication(self) -> Self:
        require_unique(self.reason_codes, "reason_codes")
        if self.status == "published":
            if self.score is None:
                raise ValueError("published scores require a score")
        elif self.score is not None or not self.reason_codes:
            raise ValueError("unpublished scores must be null with reason_codes")
        return self


class CriterionScore(ScoreValue):
    criterion_id: Text
    criterion_weight: float = Field(strict=True, gt=0)
    contributions: tuple[EvidenceContribution, ...] = ()

    @model_validator(mode="after")
    def validate_contributions(self) -> Self:
        require_unique([item.assessment_id for item in self.contributions], "assessment_ids")
        if self.status == "published" and not any(
            item.effective_weight > 0 for item in self.contributions
        ):
            raise ValueError("published criterion scores require effective evidence")
        return self


class CompetencyScore(ScoreValue, VersionedModel):
    competency: Competency
    coverage: UnitInterval
    reliability: UnitInterval | None = None
    criteria: tuple[CriterionScore, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_criteria(self) -> Self:
        require_unique([item.criterion_id for item in self.criteria], "criterion_ids")
        if self.status == "published" and (
            self.coverage == 0 or not any(item.status == "published" for item in self.criteria)
        ):
            raise ValueError("published competency scores require scored criteria and coverage")
        return self


class ScoreSnapshot(VersionedModel):
    snapshot_id: Text
    interview_id: Text
    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION
    rubric_version: Text
    aggregation_policy_version: Text
    competencies: tuple[CompetencyScore, ...] = Field(min_length=1)
    overall_coverage: UnitInterval
    overall_score: Score | None = None
    overall_score_publishable: bool = Field(strict=True)
    status: Literal["published", "insufficient_evidence", "evaluation_failed"]
    reason_codes: tuple[Text, ...] = ()
    supersedes_snapshot_id: Text | None = None
    reevaluation_reason: Text | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        names = [item.competency for item in self.competencies]
        require_unique(names, "competencies")
        if set(names) != set(Competency):
            raise ValueError("snapshots must include all six competencies, including unscored ones")
        ScoreValue(score=self.overall_score, status=self.status, reason_codes=self.reason_codes)
        if self.overall_score_publishable != (self.status == "published"):
            raise ValueError("overall_score_publishable must agree with status")
        if self.status == "published" and (
            self.overall_coverage == 0
            or not any(item.status == "published" for item in self.competencies)
            or any(item.status == "evaluation_failed" for item in self.competencies)
        ):
            raise ValueError(
                "published snapshots require scored coverage and no evaluation failure"
            )
        if (self.supersedes_snapshot_id is None) != (self.reevaluation_reason is None):
            raise ValueError("superseding snapshots require a reevaluation reason and previous ID")
        if self.supersedes_snapshot_id == self.snapshot_id:
            raise ValueError("a snapshot cannot supersede itself")
        return self


class EvaluationResult(VersionedModel):
    """Internal result, never a Question Agent payload or a live EvaluationFeedback.

    Evidence and assessments are ledger additions. Snapshot references may also
    point to historical ledger entries, whose resolution belongs to persistence.
    """

    request_id: Text
    interview_id: Text
    question_id: Text
    answer_id: Text
    status: Literal["completed", "failed"]
    analysis: AnswerAnalysis
    evidence_items: tuple[EvidenceItem, ...] = ()
    assessments: tuple[CriterionAssessment, ...] = ()
    score_snapshot: ScoreSnapshot | None = None
    reason_codes: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        require_unique([item.evidence_id for item in self.evidence_items], "evidence_ids")
        require_unique([item.assessment_id for item in self.assessments], "assessment_ids")
        require_unique(self.reason_codes, "reason_codes")
        if any(
            (item.answer_id, item.question_id) != (self.answer_id, self.question_id)
            for item in self.evidence_items
        ):
            raise ValueError("result evidence must belong to the current answer and question")
        if self.score_snapshot and self.score_snapshot.interview_id != self.interview_id:
            raise ValueError("snapshot must belong to the result interview")
        if self.status == "failed" and (
            not self.reason_codes
            or self.analysis.thread_complete
            or self.evidence_items
            or self.assessments
            or self.score_snapshot is not None
        ):
            raise ValueError(
                "failed results require reasons and cannot publish evidence or completion"
            )
        return self
