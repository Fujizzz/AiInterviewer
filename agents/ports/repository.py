"""Repository v1.1 boundary with an atomic turn unit of work."""

from typing import Protocol

from agents.domain.models import (
    AgentDecisionLog,
    CommitTurnRequest,
    CommitTurnResult,
    InterviewContext,
)
from shared.contracts import InterviewAction, InterviewPlan, InterviewState, PlannedQuestion


class InterviewRepositoryPort(Protocol):
    async def get_interview_context(self, interview_id: str) -> InterviewContext: ...

    async def initialize_interview(self, context: InterviewContext) -> InterviewContext: ...

    async def commit_turn(self, request: CommitTurnRequest) -> CommitTurnResult: ...

    async def get_processed_feedback_action(
        self,
        interview_id: str,
        feedback_request_id: str,
    ) -> InterviewAction | None: ...

    # v1.0 compatibility methods. InterviewAgentService does not use these paths.
    async def get_state(self, interview_id: str) -> InterviewState: ...

    async def save_state(
        self,
        state: InterviewState,
        expected_version: int | None = None,
    ) -> InterviewState: ...

    async def save_interview_plan(self, plan: InterviewPlan) -> None: ...

    async def get_interview_plan(self, interview_id: str) -> InterviewPlan: ...

    async def save_question(self, question: PlannedQuestion) -> None: ...

    async def get_question(self, question_id: str) -> PlannedQuestion: ...

    async def append_decision_log(self, log: AgentDecisionLog) -> None: ...

    async def save_processed_feedback_action(
        self,
        request_id: str,
        action: InterviewAction,
    ) -> None: ...
