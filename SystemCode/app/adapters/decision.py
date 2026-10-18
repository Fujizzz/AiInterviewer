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


class NeedDelta(OutputModel):
    target: str = Field(min_length=1, max_length=500)
    status: Literal["open", "blocked"]
    need_id: str = Field(
        default="",
        description="Copy an existing ID only with its unchanged target; empty for new details",
    )
    segments: list[str] = Field(default_factory=list)


class DetailedAnswerDecision(OutputModel):
    """Internal expanded view; never request this redundant structure from the model."""

    answer_relevance: float = Field(ge=0, le=1)
    analysis: DecisionState
    coverage: list[CoverageDelta] = Field(default_factory=list)
    relations: list[FactRelation] = Field(default_factory=list)
    needs: list[NeedDelta] = Field(default_factory=list, max_length=6)


class CriterionChange(OutputModel):
    id: str
    status: Literal["unassessed", "partial", "sufficient"]
    s: list[str] = Field(default_factory=list)
    gap: str = ""


class NextDetail(OutputModel):
    status: Literal["open", "blocked"]
    target: str = Field(min_length=1, max_length=500)
    s: list[str] = Field(default_factory=list)


class FactChange(OutputModel):
    old: str
    current: str
    kind: Literal["clarifies", "supersedes", "disputes"]
    compatibility: Literal["compatible", "exclusive", "uncertain"]
    context: str | None = None


class CompactAnswerDecision(OutputModel):
    """One continuation decision with request-local aliases and criterion deltas only."""

    status: Literal["substantive", "partial", "non_answer", "explicit_unknown", "refusal"]
    relevance: float = Field(default=0, ge=0, le=1)
    scope: Literal["unknown", "label_only", "concrete", "none"] = "unknown"
    new: bool = False
    complete: bool = False
    limits: list[str] = Field(
        description=(
            "Current answer segment IDs only, e.g. ['s0']. Empty if none. "
            "Never explanations, quotations or annotated IDs."
        ),
    )
    criteria: list[CriterionChange] = Field(default_factory=list)
    needs: list[NextDetail] = Field(default_factory=list, max_length=6)
    relations: list[FactChange]

    @classmethod
    def model_json_schema(cls, **kwargs):
        schema = super().model_json_schema(**kwargs)

        def strip_titles(value):
            if isinstance(value, dict):
                value.pop("title", None)
                for child in value.values():
                    strip_titles(child)
            elif isinstance(value, list):
                for child in value:
                    strip_titles(child)

        strip_titles(schema)
        return schema
