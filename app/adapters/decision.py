"""Small model DTOs; IDs are resolved against original answers before publication."""

from typing import Literal

from pydantic import Field

from app.providers.llm import OutputModel


class DecisionState(OutputModel):
    status: Literal["substantive", "partial", "non_answer", "explicit_unknown", "refusal"]
    scope: Literal["unknown", "label_only", "concrete", "none"] = "unknown"
    new_information: bool = False
    complete: bool = False
    need: str | None = None
    limitation_segments: list[str] = Field(default_factory=list)


class CriterionDelta(OutputModel):
    criterion_id: str
    status: Literal["partial", "sufficient"]
    missing: list[str] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)


class CoverageDelta(OutputModel):
    objective_id: str
    status: Literal["partial", "sufficient"]
    missing: list[str] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)
    criteria: list[CriterionDelta] = Field(default_factory=list)


class FactRelation(OutputModel):
    earlier_segment: str
    current_segment: str
    kind: Literal["clarifies", "supersedes", "disputes"]
    compatibility: Literal["compatible", "exclusive", "uncertain"]
    different_context_segment: str | None = None


class CompactAnswerDecision(OutputModel):
    answer_relevance: float = Field(ge=0, le=1)
    analysis: DecisionState
    coverage: list[CoverageDelta] = Field(default_factory=list)
    relations: list[FactRelation] = Field(default_factory=list)
