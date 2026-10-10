"""Agenda allocations contain objectives, never candidate-facing questions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TopicAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: str | None
    topic_key: str = Field(min_length=1)
    objective: str = Field(min_length=3, max_length=400)
    completion_criteria: str = Field(min_length=3, max_length=400)
    budget_seconds: int = Field(gt=0)
    expected_questions: int = Field(ge=1)

    @field_validator("objective", "completion_criteria")
    @classmethod
    def objectives_not_questions(cls, value):
        if "?" in value or "？" in value:
            raise ValueError("Use declarative objectives, not question text")
        return value


class PlanDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # During replanning these are REMAINING allocations, not lifetime totals.
    topics: list[TopicAllocation] = Field(max_length=100)
    reserve_seconds: int = Field(ge=0)
    closing_seconds: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)


class TopicPreference(BaseModel):
    """Semantic allocation request. The controller owns seconds and question counts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: str | None
    topic_key: str = Field(min_length=1)
    objective: str = Field(min_length=3, max_length=400)
    completion_criteria: str = Field(min_length=3, max_length=400)
    relative_weight: float = Field(default=1, gt=0, le=100)
    depth: Literal["brief", "standard", "deep"] = "standard"
    objective_change: Literal["preserve", "replace"] = "preserve"

    @field_validator("objective", "completion_criteria")
    @classmethod
    def objectives_not_questions(cls, value):
        return TopicAllocation.objectives_not_questions(value)


class PlanProposal(BaseModel):
    """Priority-ordered objectives; legacy timed drafts remain readable for replay."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Optional only for loading legacy records. New live proposals must echo the version.
    base_plan_version: int | None = Field(default=None, ge=0)
    topics: list[TopicPreference] = Field(max_length=100)
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_draft(cls, value):
        if isinstance(value, PlanDraft):
            value = value.model_dump()
        if isinstance(value, dict) and "reserve_seconds" in value:
            legacy = PlanDraft.model_validate(value)
            scale = max((item.budget_seconds for item in legacy.topics), default=1)
            return {
                "topics": [
                    {
                        "project_id": item.project_id,
                        "topic_key": item.topic_key,
                        "objective": item.objective,
                        "completion_criteria": item.completion_criteria,
                        "relative_weight": item.budget_seconds / scale,
                    }
                    for item in legacy.topics
                ],
                "reason": legacy.reason,
            }
        return value


class InformationNeed(BaseModel):
    """A durable objective gap; wording is data, not the identity of a selection."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    need_id: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    objective_version: int = Field(default=1, ge=1)
    target: str = Field(min_length=1)
    answer_unit: str = ""
    source_answer_ids: list[str] = Field(default_factory=list)
    status: Literal["open", "satisfied", "superseded", "blocked"] = "open"


class CompletionRequirement(BaseModel):
    """One persisted part of the accepted completion contract, with its own evidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    criterion_id: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    coverage_status: Literal["unassessed", "partial", "sufficient"] = "unassessed"
    missing_information: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)


class TopicProgress(BaseModel):
    questions_asked: int = Field(default=0, ge=0)
    elapsed_seconds: int = Field(default=0, ge=0)
    # Execution and evidence coverage are independent. Moving on never proves coverage.
    status: Literal["pending", "active", "deferred", "completed", "skipped"] = "pending"
    reason: str = ""
    coverage_status: Literal["unassessed", "partial", "sufficient"] = "unassessed"
    missing_information: list[str] = Field(default_factory=list)
    evidence_answer_ids: list[str] = Field(default_factory=list)
    coverage_evidence: list[dict] = Field(default_factory=list)
    resume_count: int = Field(default=0, ge=0)
    objective_id: str = ""
    next_need_id: str | None = None
    objective_version: int = Field(default=1, ge=1)
    information_needs: list[InformationNeed] = Field(default_factory=list)
    completion_requirements: list[CompletionRequirement] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_execution_completion(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            if value.get("reason") == "EXECUTOR_MOVED_ON":
                value["status"] = "deferred"
                value.setdefault("coverage_status", "unassessed")
            elif value.get("reason") == "OBJECTIVE_COMPLETED":
                value.setdefault("coverage_status", "sufficient")
        return value


class PlanRevision(BaseModel):
    version: int
    elapsed_seconds: int
    trigger: str
    reason: str
    answer_id: str | None = None
    fallback_used: bool = False
    topics: list[TopicAllocation]
    reserve_seconds: int
    closing_seconds: int
    proposal: PlanProposal | None = None
    adjustments: list[dict] = Field(default_factory=list)
    remaining_at_compile: int | None = None
    base_plan_version: int | None = None
    semantic_changes: list[dict] = Field(default_factory=list)
    source: Literal["model", "fallback", "local_compilation"] = "model"
