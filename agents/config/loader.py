"""Validated configuration for dialogue policies and bounded model execution."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from shared.contracts.budgets import QuestionBudgets


class DifficultySettings(BaseModel):
    strong_evidence_threshold: float = Field(default=0.8, ge=0, le=1)
    strong_relevance_threshold: float = Field(default=0.8, ge=0, le=1)


class ProbeSettings(BaseModel):
    minimum_remaining_seconds: int = Field(default=60, ge=0)
    max_no_information_answers: int = Field(default=2, ge=1)


class ProjectSelectorSettings(BaseModel):
    job_relevance_weight: float = Field(default=0.6, ge=0)
    detail_weight: float = Field(default=0.4, ge=0)
    visit_penalty_weight: float = Field(default=0.2, ge=0)


class RetrievalSettings(BaseModel):
    candidate_top_k: int = Field(default=4, ge=1, le=20)
    technical_top_k: int = Field(default=5, ge=1, le=20)
    question_top_k: int = Field(default=3, ge=1, le=20)
    job_top_k: int = Field(default=3, ge=1, le=20)


class ContextSettings(BaseModel):
    candidate_chunks: int = Field(default=4, ge=0)
    technical_chunks: int = Field(default=5, ge=0)
    question_chunks: int = Field(default=3, ge=0)
    job_chunks: int = Field(default=3, ge=0)
    recent_questions: int = Field(default=3, ge=0)
    recent_feedback: int = Field(default=3, ge=0)


class QuestionValidationSettings(BaseModel):
    minimum_words: int = Field(default=4, ge=1)
    maximum_words: int = Field(default=110, ge=1)
    maximum_question_marks: int = Field(default=1, ge=1)


class TimeoutSettings(BaseModel):
    rag_seconds: float = Field(default=2.0, gt=0)
    evaluation_seconds: float = Field(default=30.0, gt=0)
    llm_generation_seconds: float = Field(default=30.0, gt=0)
    repository_seconds: float = Field(default=1.0, gt=0)


class RetrySettings(BaseModel):
    llm_generation_retries: int = Field(default=3, ge=0, le=3)
    state_conflict_recomputations: int = Field(default=1, ge=0, le=1)


class QuestionAgentSettings(BaseModel):
    enabled: bool = True
    quality_timeout_seconds: float = Field(default=20.0, gt=0)
    max_tool_calls: int = Field(default=2, ge=0, le=8)
    total_timeout_seconds: float = Field(default=90.0, gt=0)
    history_retention: int = Field(default=50, ge=1, le=200)
    history_tool_limit: int = Field(default=10, ge=1, le=50)
    text_char_limit: int = Field(default=4000, ge=100, le=20000)


class AgentSettings(QuestionBudgets):
    policy_config_version: str = "dialogue-policy-v2"
    min_question_difficulty: int = Field(default=1, ge=1, le=5)
    max_question_difficulty: int = Field(default=5, ge=1, le=5)
    initial_question_difficulty: int = Field(default=2, ge=1, le=5)
    difficulty: DifficultySettings = Field(default_factory=DifficultySettings)
    probe: ProbeSettings = Field(default_factory=ProbeSettings)
    project_selector: ProjectSelectorSettings = Field(default_factory=ProjectSelectorSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    question_validation: QuestionValidationSettings = Field(
        default_factory=QuestionValidationSettings
    )
    timeouts: TimeoutSettings = Field(default_factory=TimeoutSettings)
    retries: RetrySettings = Field(default_factory=RetrySettings)
    question_agent: QuestionAgentSettings = Field(default_factory=QuestionAgentSettings)


class ConfigDocument(BaseModel):
    agent: AgentSettings


def load_agent_settings(path: Path | None = None) -> AgentSettings:
    with (path or Path(__file__).with_name("defaults.yaml")).open(encoding="utf-8") as source:
        return ConfigDocument.model_validate(yaml.safe_load(source)).agent
