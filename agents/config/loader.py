"""Validated YAML configuration for Agent policy constants."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class CompetencySelectorSettings(BaseModel):
    importance_weight: float = Field(ge=0.0)
    coverage_weight: float = Field(ge=0.0)
    confidence_weight: float = Field(ge=0.0)
    stage_fit_weight: float = Field(ge=0.0)
    recency_penalty_weight: float = Field(ge=0.0)
    anchor_bonus: float = Field(ge=0.0)


class TargetSettings(BaseModel):
    default_coverage: float = Field(ge=0.0, le=1.0)
    default_confidence: float = Field(ge=0.0, le=1.0)


class DifficultySettings(BaseModel):
    strong_evidence_threshold: float = Field(ge=0.0, le=1.0)
    strong_confidence_threshold: float = Field(ge=0.0, le=1.0)
    strong_relevance_threshold: float = Field(ge=0.0, le=1.0)
    weak_evidence_threshold: float = Field(ge=0.0, le=1.0)
    minimum_relevance_to_decrease: float = Field(ge=0.0, le=1.0)


class ProbeSettings(BaseModel):
    minimum_remaining_seconds: int = Field(ge=0)


class RedundancySettings(BaseModel):
    maximum_key_occurrences: int = Field(ge=1)


class ProjectSelectorSettings(BaseModel):
    competency_relevance_weight: float = Field(default=0.50, ge=0.0)
    job_relevance_weight: float = Field(default=0.30, ge=0.0)
    unverified_claim_weight: float = Field(default=0.20, ge=0.0)
    visit_penalty_weight: float = Field(default=0.25, ge=0.0)
    visits_until_full_penalty: int = Field(default=3, ge=1)


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
    maximum_words: int = Field(default=70, ge=1)
    maximum_question_marks: int = Field(default=1, ge=1)


class TimeoutSettings(BaseModel):
    rag_seconds: float = Field(default=2.0, gt=0.0)
    evaluation_seconds: float = Field(default=5.0, gt=0.0)
    llm_planning_seconds: float = Field(default=5.0, gt=0.0)
    llm_generation_seconds: float = Field(default=5.0, gt=0.0)
    repository_seconds: float = Field(default=1.0, gt=0.0)


class RetrySettings(BaseModel):
    llm_generation_retries: int = Field(default=1, ge=0, le=1)
    state_conflict_recomputations: int = Field(default=1, ge=0, le=1)


class AgentSettings(BaseModel):
    policy_config_version: str
    max_consecutive_probes: int = Field(ge=0)
    min_question_difficulty: int = Field(ge=1, le=5)
    max_question_difficulty: int = Field(ge=1, le=5)
    initial_question_difficulty: int = Field(ge=1, le=5)
    minimum_interview_seconds: int = Field(ge=0)
    default_stage_fit: float = Field(ge=0.0, le=1.0)
    competency_selector: CompetencySelectorSettings
    target: TargetSettings
    difficulty: DifficultySettings
    probe: ProbeSettings
    redundancy: RedundancySettings
    project_selector: ProjectSelectorSettings = Field(default_factory=ProjectSelectorSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    question_validation: QuestionValidationSettings = Field(
        default_factory=QuestionValidationSettings
    )
    timeouts: TimeoutSettings = Field(default_factory=TimeoutSettings)
    retries: RetrySettings = Field(default_factory=RetrySettings)


class ConfigDocument(BaseModel):
    agent: AgentSettings


def load_agent_settings(path: Path | None = None) -> AgentSettings:
    """Load and validate Agent settings from YAML."""

    config_path = path or Path(__file__).with_name("defaults.yaml")
    with config_path.open(encoding="utf-8") as config_file:
        document = yaml.safe_load(config_file)
    return ConfigDocument.model_validate(document).agent
