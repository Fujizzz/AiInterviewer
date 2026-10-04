"""Resolver-owned audit contracts; existing evaluation schema 1.0 stays unchanged."""

from typing import Literal, Self

from pydantic import Field, model_validator

from evaluation.contracts import (
    EvaluationModel,
    EvaluationResult,
    EvidenceItem,
    EvidenceRelation,
    QuoteSpan,
    Text,
    require_unique,
)
from evaluation.inputs import ThreadTurn

RESOLVER_VERSION = "resolver-1.0.0"


class EvidenceSource(EvaluationModel):
    evidence: EvidenceItem
    turn: ThreadTurn


class RelationDecision(EvaluationModel):
    evidence_id: Text
    relation: EvidenceRelation
    related_evidence_ids: tuple[Text, ...] = ()
    independence: Literal["new_episode", "same_episode", "unresolved"]
    same_episode_as: tuple[Text, ...] = ()
    retraction_span: QuoteSpan | None = None
    concise_rationale: Text

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        for name in ("related_evidence_ids", "same_episode_as"):
            values = getattr(self, name)
            require_unique(values, name)
            if self.evidence_id in values:
                raise ValueError("evidence cannot reference itself")
        if (self.relation == EvidenceRelation.NEW) != (not self.related_evidence_ids):
            raise ValueError("only new evidence may omit relation targets")
        if (self.independence == "same_episode") != bool(self.same_episode_as):
            raise ValueError("same_episode requires existing episode references")
        if self.relation in {
            EvidenceRelation.DUPLICATE,
            EvidenceRelation.REFINES,
            EvidenceRelation.CONTRADICTS,
            EvidenceRelation.RETRACTS,
        } and (
            self.independence != "same_episode"
            or not set(self.related_evidence_ids) <= set(self.same_episode_as)
        ):
            raise ValueError("dependent relations must share their targets' episode")
        if (self.relation == EvidenceRelation.RETRACTS) != (self.retraction_span is not None):
            raise ValueError("only retracts requires an explicit current-answer retraction span")
        return self


class ResolutionDraft(EvaluationModel):
    decisions: tuple[RelationDecision, ...]


class ResolutionHistory(EvaluationModel):
    """Chronological raw sources + decisions, sufficient for deterministic replay.

    No silent truncation: all sources must belong to this interview. Persistence
    and historical snapshot storage remain the caller's responsibility.
    """

    resolver_version: Literal["resolver-1.0.0"] = RESOLVER_VERSION
    interview_id: Text
    sources: tuple[EvidenceSource, ...] = ()
    decisions: tuple[RelationDecision, ...] = ()


class EvidenceState(EvaluationModel):
    evidence_id: Text
    canonical_claim: Text
    status: Literal["eligible", "duplicate", "disputed", "retracted", "insufficient"]
    reason_codes: tuple[Text, ...] = Field(min_length=1)
    counter_evidence_ids: tuple[Text, ...] = ()


class EvidenceConflict(EvaluationModel):
    evidence_id: Text
    counter_evidence_id: Text
    status: Literal["unresolved", "resolved_by_retraction"]


class EvidenceResolution(EvaluationModel):
    history: ResolutionHistory
    normalization_version: Literal["claim-nfc-whitespace-1"] = "claim-nfc-whitespace-1"
    # A derived view of the entire ledger, not mutations of historical records.
    evidence_items: tuple[EvidenceItem, ...]
    states: tuple[EvidenceState, ...]
    conflicts: tuple[EvidenceConflict, ...]


class ResolvedEvaluation(EvaluationModel):
    evaluation: EvaluationResult
    resolution: EvidenceResolution | None = None

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.evaluation.status == "failed" and self.resolution is not None:
            raise ValueError("failed evaluation cannot publish a resolution")
        if self.evaluation.status == "completed" and self.resolution is None:
            raise ValueError("completed resolved evaluation requires a resolution")
        if self.resolution and (
            self.resolution.history.interview_id != self.evaluation.interview_id
        ):
            raise ValueError("resolution must belong to the evaluation interview")
        return self
