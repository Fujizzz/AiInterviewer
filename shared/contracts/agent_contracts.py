"""Stable Pydantic contracts shared by Agent, RAG, Evaluation, and Backend.

The field names intentionally follow ``docs/modules/AGENT_MODULE_DEVELOPMENT.md``.
Concrete adapter implementations do not belong in this module.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

CONTRACT_VERSION = "1.0"


class Competency(str, Enum):
    TECHNICAL_DEPTH = "technical_depth"
    OWNERSHIP = "ownership"
    DECISION_MAKING = "decision_making"
    DEBUGGING = "debugging"
    EVALUATION = "evaluation"
    ADAPTABILITY = "adaptability"


class InterviewStage(str, Enum):
    INTRO = "intro"
    PROJECT_DEEP_DIVE = "project_deep_dive"
    TECHNICAL = "technical"
    BEHAVIORAL = "behavioral"
    CLOSING = "closing"
    FINISHED = "finished"


class QuestionType(str, Enum):
    DESCRIPTION = "description"
    IMPLEMENTATION = "implementation"
    MECHANISM = "mechanism"
    DESIGN = "design"
    TRADEOFF = "tradeoff"
    FAILURE_ANALYSIS = "failure_analysis"
    COUNTERFACTUAL = "counterfactual"


class RetrievalSource(str, Enum):
    CANDIDATE = "candidate"
    TECHNICAL = "technical"
    QUESTION = "question"
    JOB = "job"


class InterviewActionType(str, Enum):
    ASK_QUESTION = "ask_question"
    CHANGE_STAGE = "change_stage"
    FINISH = "finish"


class CandidateClaim(BaseModel):
    contract_version: str = CONTRACT_VERSION
    claim_id: str
    text: str
    source_ref: str | None = None


class CandidateProject(BaseModel):
    contract_version: str = CONTRACT_VERSION
    project_id: str
    name: str
    domain: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    claims: list[CandidateClaim] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    contract_version: str = CONTRACT_VERSION
    candidate_id: str
    skills: list[str] = Field(default_factory=list)
    projects: list[CandidateProject] = Field(default_factory=list)


class JobProfile(BaseModel):
    contract_version: str = CONTRACT_VERSION
    job_id: str
    title: str
    seniority: str | None = None
    competency_importance: dict[Competency, float]
    domains: list[str] = Field(default_factory=list)


class CompetencyState(BaseModel):
    contract_version: str = CONTRACT_VERSION
    competency: Competency
    score: float | None = None
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    max_verified_difficulty: int = Field(default=0, ge=0, le=5)
    evidence_count: int = Field(default=0, ge=0)
    independent_evidence_count: int = Field(default=0, ge=0)
    last_asked_at_question_index: int | None = Field(default=None, ge=0)


class InterviewState(BaseModel):
    contract_version: str = CONTRACT_VERSION
    interview_id: str
    state_version: int = Field(default=0, ge=0)
    status: str = "created"
    stage: InterviewStage = InterviewStage.INTRO
    active_project_id: str | None = None
    current_question_id: str | None = None
    question_index: int = Field(default=0, ge=0)
    elapsed_seconds: int = Field(default=0, ge=0)
    remaining_seconds: int
    competencies: dict[Competency, CompetencyState]
    asked_question_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    project_visit_count: dict[str, int] = Field(default_factory=dict)
    last_question_type: QuestionType | None = None
    last_topic: str | None = None
    last_competency: Competency | None = None
    consecutive_probes: int = Field(default=0, ge=0)


class StagePlan(BaseModel):
    contract_version: str = CONTRACT_VERSION
    stage: InterviewStage
    budget_seconds: int = Field(gt=0)


class InterviewPlan(BaseModel):
    contract_version: str = CONTRACT_VERSION
    interview_id: str
    duration_seconds: int = Field(gt=0)
    stages: list[StagePlan]
    competency_importance: dict[Competency, float]
    target_coverage: dict[Competency, float]
    target_confidence: dict[Competency, float]
    max_consecutive_probes: int = Field(default=3, ge=0)


class PlannedQuestion(BaseModel):
    contract_version: str = CONTRACT_VERSION
    question_id: str
    target_competency: Competency
    project_id: str | None = None
    topic: str | None = None
    difficulty: int = Field(ge=1, le=5)
    probe_depth: int = Field(ge=1, le=7)
    question_type: QuestionType
    intent: str
    required_context_sources: list[RetrievalSource] = Field(default_factory=list)
    text: str | None = None


class CandidateAnswer(BaseModel):
    contract_version: str = CONTRACT_VERSION
    interview_id: str
    question_id: str
    answer_id: str
    text: str


class RetrievalRequest(BaseModel):
    contract_version: str = CONTRACT_VERSION
    request_id: str
    interview_id: str
    source: RetrievalSource
    intent: str
    competency: Competency
    difficulty: int = Field(ge=1, le=5)
    candidate_id: str | None = None
    project_id: str | None = None
    topic: str | None = None
    domain: str | None = None
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class RetrievedChunk(BaseModel):
    contract_version: str = CONTRACT_VERSION
    chunk_id: str
    source: RetrievalSource
    title: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_score: float | None = None


class RetrievalResponse(BaseModel):
    contract_version: str = CONTRACT_VERSION
    request_id: str
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    latency_ms: int | None = Field(default=None, ge=0)
    partial: bool = False


class EvaluationRequest(BaseModel):
    contract_version: str = CONTRACT_VERSION
    request_id: str
    interview_id: str
    question: PlannedQuestion
    answer: CandidateAnswer


class EvaluationFeedback(BaseModel):
    contract_version: str = CONTRACT_VERSION
    request_id: str
    question_id: str
    target_competency: Competency
    answer_relevance: float = Field(ge=0.0, le=1.0)
    evidence_strength: float = Field(ge=0.0, le=1.0)
    evaluation_confidence: float = Field(ge=0.0, le=1.0)
    rubric_level: int | None = Field(default=None, ge=1, le=5)
    contradiction_detected: bool = False
    needs_clarification: bool = False
    updated_competency_state: CompetencyState
    evidence_ids: list[str] = Field(default_factory=list)


class DecisionTrace(BaseModel):
    contract_version: str = CONTRACT_VERSION
    selected_competency_priority: float | None = None
    reason_code: str
    details: dict[str, Any] = Field(default_factory=dict)


class InterviewAction(BaseModel):
    contract_version: str = CONTRACT_VERSION
    action_id: str
    interview_id: str
    type: InterviewActionType
    question: PlannedQuestion | None = None
    from_stage: InterviewStage | None = None
    to_stage: InterviewStage | None = None
    decision_trace: DecisionTrace


class InitializeInterviewRequest(BaseModel):
    contract_version: str = CONTRACT_VERSION
    interview_id: str
    candidate_profile: CandidateProfile
    job_profile: JobProfile
    duration_seconds: int = Field(gt=0)
    enabled_stages: list[InterviewStage]


class InitializeInterviewResponse(BaseModel):
    contract_version: str = CONTRACT_VERSION
    interview_id: str
    plan: InterviewPlan
    state: InterviewState
    first_action: InterviewAction
