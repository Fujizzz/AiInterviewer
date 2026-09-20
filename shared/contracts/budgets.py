"""Canonical question budgets; legacy follow-up limits are input compatibility only."""

from pydantic import BaseModel, Field, model_validator


class QuestionBudgets(BaseModel):
    max_questions_per_project: int = Field(default=4, ge=1)
    max_questions_per_topic: int = Field(default=3, ge=1)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_followup_limit(cls, values):
        if isinstance(values, dict) and "max_consecutive_probes" in values:
            values = dict(values)
            old = values.pop("max_consecutive_probes")
            if isinstance(old, bool) or not isinstance(old, int) or old < 0:
                raise ValueError("max_consecutive_probes must be a nonnegative integer")
            values.setdefault("max_questions_per_topic", old + 1)
        return values

    @property
    def max_consecutive_probes(self):
        """Read-only compatibility for existing integrations; never a second budget."""
        return self.max_questions_per_topic - 1
