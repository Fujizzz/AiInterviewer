"""Agenda allocations contain objectives, never candidate-facing questions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class TopicProgress(BaseModel):
    questions_asked: int = Field(default=0, ge=0)
    elapsed_seconds: int = Field(default=0, ge=0)
    status: Literal["pending", "active", "completed", "skipped"] = "pending"
    reason: str = ""


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
