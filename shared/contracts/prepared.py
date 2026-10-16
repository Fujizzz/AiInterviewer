"""Reviewed question content, independent of any interview's IDs or dialogue state."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts.agent_contracts import QuestionType, RetrievalSource, RetrievedChunk


class PreparedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cache_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    topic_key: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    kind: Literal["opening", "knowledge"]
    text: str = Field(min_length=1, max_length=4000)
    information_goal: str = Field(min_length=1, max_length=2000)
    difficulty: int = Field(ge=1, le=5)
    question_type: QuestionType
    review_version: str = Field(min_length=1)
    evidence: list[RetrievedChunk] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def grounded_knowledge(self):
        if self.kind == "knowledge" and not any(
            c.source == RetrievalSource.TECHNICAL
            and c.content.strip()
            and not c.metadata.get("low_confidence")
            and c.metadata.get("confidence") != "low"
            for c in self.evidence
        ):
            raise ValueError("Prepared knowledge questions require usable technical evidence")
        return self
