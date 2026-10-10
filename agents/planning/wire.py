"""Small provider contract; durable plans keep the full accepted objectives."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.planning.allocation import PlanConstraintError
from shared.contracts.planning import PlanProposal, TopicAllocation, TopicPreference


class PlannerChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic_key: str = Field(min_length=1)
    relative_weight: float = Field(default=1, gt=0, le=100)
    depth: Literal["brief", "standard", "deep"] = "standard"
    objective_change: Literal["preserve", "replace"] = "preserve"
    objective: str | None = Field(default=None, min_length=3, max_length=400)
    completion_criteria: str | None = Field(default=None, min_length=3, max_length=400)

    @field_validator("objective", "completion_criteria")
    @classmethod
    def objectives_not_questions(cls, value):
        return TopicAllocation.objectives_not_questions(value) if value is not None else None


class CompactPlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_plan_version: int = Field(ge=0)
    topics: list[PlannerChoice] = Field(max_length=100)
    reason: str = Field(min_length=3, max_length=300)

    def expand(self, context, eligible):
        """Resolve ownership and preserved text locally, before budget compilation."""
        if self.base_plan_version != context.plan.version:
            raise PlanConstraintError("STALE_OR_MISSING_PLAN_VERSION")
        existing = {item.topic_key: item for item in context.plan.topics}
        topics = []
        for item in self.topics:
            if item.topic_key not in eligible:
                raise PlanConstraintError("CLOSED_OR_UNKNOWN_TOPIC", topic_key=item.topic_key)
            previous = existing.get(item.topic_key)
            preserving = previous is not None and item.objective_change == "preserve"
            if not preserving and (not item.objective or not item.completion_criteria):
                raise PlanConstraintError("MISSING_NEW_OBJECTIVE", topic_key=item.topic_key)
            topics.append(
                TopicPreference(
                    project_id=eligible[item.topic_key]["project_id"],
                    topic_key=item.topic_key,
                    relative_weight=item.relative_weight,
                    depth=item.depth,
                    objective_change=item.objective_change,
                    objective=previous.objective if preserving else item.objective,
                    completion_criteria=previous.completion_criteria
                    if preserving
                    else item.completion_criteria,
                )
            )
        return PlanProposal(
            base_plan_version=self.base_plan_version, topics=topics, reason=self.reason
        )
