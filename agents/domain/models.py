"""Agent-owned persistence and decision objects.

The repository-facing objects in this module use contract version 1.1.  Shared
RAG, Evaluation, and public Agent contracts intentionally remain at v1.0.
"""

from pydantic import BaseModel, Field, model_validator

from shared.contracts import (
    CandidateProfile,
    Competency,
    EvaluationFeedback,
    InterviewAction,
    InterviewActionType,
    InterviewPlan,
    InterviewState,
    JobProfile,
    PlannedQuestion,
    QuestionType,
    RetrievalResponse,
    RetrievalSource,
)

REPOSITORY_CONTRACT_VERSION = "1.1"


class CompetencySelection(BaseModel):
    competency: Competency
    priority: float
    reason_code: str
    all_priorities: dict[Competency, float]


class ProbeDecision(BaseModel):
    should_probe: bool
    next_probe_depth: int = Field(ge=0, le=7)
    reason_code: str


class ProjectSelection(BaseModel):
    project_id: str
    score: float
    reason_code: str
    all_scores: dict[str, float]


class TopicSelection(BaseModel):
    topic: str
    source_claim_id: str | None = None
    reason_code: str


class RetrievalBatch(BaseModel):
    responses: list[RetrievalResponse] = Field(default_factory=list)
    failed_sources: list[RetrievalSource] = Field(default_factory=list)
    timeout_sources: list[RetrievalSource] = Field(default_factory=list)


class QuestionValidationResult(BaseModel):
    is_valid: bool
    errors: list[str] = Field(default_factory=list)


class AgentDecisionLog(BaseModel):
    contract_version: str = REPOSITORY_CONTRACT_VERSION
    decision_id: str
    interview_id: str
    state_version: int = Field(ge=0)
    selected_competency: Competency | None = None
    competency_priorities: dict[str, float] = Field(default_factory=dict)
    selected_project: str | None = None
    selected_project_id: str | None = None
    selected_topic: str | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5)
    probe_depth: int | None = Field(default=None, ge=0, le=7)
    question_type: QuestionType | None = None
    action_type: InterviewActionType
    reason_code: str
    decision_reasons: dict[str, str] = Field(default_factory=dict)
    retrieval_sources: list[str] = Field(default_factory=list)
    rag_sources_requested: list[str] = Field(default_factory=list)
    failed_retrieval_sources: list[str] = Field(default_factory=list)
    timeout_retrieval_sources: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    planner_latency_ms: int = Field(default=0, ge=0)
    retrieval_latency_ms: int = Field(default=0, ge=0)
    generation_latency_ms: int = Field(default=0, ge=0)
    total_agent_latency_ms: int = Field(default=0, ge=0)
    prompt_version: str | None = None
    planner_prompt_version: str | None = None
    generator_prompt_version: str | None = None
    policy_config_version: str
    model: str | None = None


class AnchorState(BaseModel):
    """Persistent orchestration state for one comparable anchor."""

    asked: bool = False
    completed: bool = False
    question_id: str | None = None


class InterviewContext(BaseModel):
    """Everything a fresh Agent process needs to resume one interview."""

    contract_version: str = REPOSITORY_CONTRACT_VERSION
    interview_id: str
    candidate_profile: CandidateProfile
    job_profile: JobProfile
    plan: InterviewPlan
    state: InterviewState
    recent_feedback: list[EvaluationFeedback] = Field(default_factory=list)
    current_difficulty: dict[Competency, int] = Field(default_factory=dict)
    processed_feedback_ids: list[str] = Field(default_factory=list)
    anchor_state: dict[Competency, AnchorState] = Field(default_factory=dict)
    policy_config_version: str

    @model_validator(mode="after")
    def validate_identity(self) -> "InterviewContext":
        identifiers = {self.interview_id, self.plan.interview_id, self.state.interview_id}
        if len(identifiers) != 1:
            raise ValueError("context, plan, and state interview_id values must match")
        return self


class CommitTurnRequest(BaseModel):
    """Atomic Agent-to-Repository unit of work for one resulting action."""

    contract_version: str = REPOSITORY_CONTRACT_VERSION
    interview_id: str
    expected_state_version: int = Field(ge=0)
    new_state: InterviewState
    question: PlannedQuestion | None
    decision_log: AgentDecisionLog
    feedback_request_id: str | None = None
    resulting_action: InterviewAction
    new_context: InterviewContext | None = None

    @model_validator(mode="after")
    def validate_turn(self) -> "CommitTurnRequest":
        if self.new_state.interview_id != self.interview_id:
            raise ValueError("new_state interview_id must match commit interview_id")
        if self.decision_log.interview_id != self.interview_id:
            raise ValueError("decision_log interview_id must match commit interview_id")
        if self.resulting_action.interview_id != self.interview_id:
            raise ValueError("resulting_action interview_id must match commit interview_id")
        if self.new_context is not None:
            if self.new_context.interview_id != self.interview_id:
                raise ValueError("new_context interview_id must match commit interview_id")
            if self.new_context.state != self.new_state:
                raise ValueError("new_context.state must equal new_state")
        if self.question != self.resulting_action.question:
            raise ValueError("committed question must equal the resulting action question")
        return self


class CommitTurnResult(BaseModel):
    contract_version: str = REPOSITORY_CONTRACT_VERSION
    committed: bool
    state: InterviewState
    action: InterviewAction


class PolicyReplayResult(BaseModel):
    """Deterministic policy fields that can be compared with a decision log."""

    selected_competency: Competency
    selected_project_id: str | None = None
    selected_topic: str | None = None
    difficulty: int = Field(ge=1, le=5)
    probe_depth: int = Field(ge=1, le=7)
    should_probe: bool
    reason_code: str
