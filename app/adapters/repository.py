"""In-memory RepositoryPort adapter for the terminal MVP."""

from __future__ import annotations

from agents.domain.errors import InvalidAgentState, StateConflictError
from agents.domain.models import (
    AgentDecisionLog,
    CommitTurnRequest,
    CommitTurnResult,
    InterviewContext,
)
from shared.contracts import InterviewAction, PlannedQuestion


class InMemoryInterviewRepository:
    """Publish complete Agent turns atomically and isolate interviews by ID."""

    def __init__(self) -> None:
        self.contexts: dict[str, InterviewContext] = {}
        self.questions: dict[str, PlannedQuestion] = {}
        self.question_interviews: dict[str, str] = {}
        self.interview_question_ids: dict[str, list[str]] = {}
        self.decision_logs: list[AgentDecisionLog] = []
        self.processed_feedback_actions: dict[tuple[str, str], InterviewAction] = {}

    async def get_interview_context(self, interview_id: str) -> InterviewContext:
        try:
            return self.contexts[interview_id].model_copy(deep=True)
        except KeyError as error:
            raise InvalidAgentState(f"Interview context {interview_id!r} was not found") from error

    async def initialize_interview(self, context: InterviewContext) -> InterviewContext:
        if context.interview_id in self.contexts:
            raise StateConflictError(f"Interview {context.interview_id!r} is already initialized")
        saved = context.model_copy(deep=True)
        saved.state.state_version += 1
        self.contexts[saved.interview_id] = saved
        self.interview_question_ids[saved.interview_id] = []
        return saved.model_copy(deep=True)

    async def commit_turn(self, request: CommitTurnRequest) -> CommitTurnResult:
        try:
            stored = self.contexts[request.interview_id]
        except KeyError as error:
            raise InvalidAgentState(
                f"Interview context {request.interview_id!r} was not found"
            ) from error

        current_version = stored.state.state_version
        if request.expected_state_version != current_version:
            raise StateConflictError(
                f"Expected state version {request.expected_state_version}, "
                f"current version is {current_version}"
            )
        if request.new_state.state_version != current_version:
            raise StateConflictError(
                f"State payload version {request.new_state.state_version}, "
                f"current version is {current_version}"
            )
        if request.feedback_request_id is not None:
            key = (request.interview_id, request.feedback_request_id)
            if key in self.processed_feedback_actions:
                raise StateConflictError(
                    f"Feedback request {request.feedback_request_id!r} is already processed"
                )
        if request.question is not None:
            existing = self.questions.get(request.question.question_id)
            if existing is not None and existing != request.question:
                raise StateConflictError(
                    f"Question {request.question.question_id!r} already has other content"
                )

        saved_context = (
            request.new_context.model_copy(deep=True)
            if request.new_context is not None
            else stored.model_copy(deep=True)
        )
        saved_context.state = request.new_state.model_copy(deep=True)
        saved_context.state.state_version = current_version + 1
        if request.feedback_request_id is not None:
            saved_context.processed_feedback_ids = list(
                dict.fromkeys([*stored.processed_feedback_ids, request.feedback_request_id])
            )
        else:
            saved_context.processed_feedback_ids = list(stored.processed_feedback_ids)

        saved_question = (
            request.question.model_copy(deep=True) if request.question is not None else None
        )
        saved_log = request.decision_log.model_copy(deep=True)
        saved_action = request.resulting_action.model_copy(deep=True)
        if saved_log.state_version != saved_context.state.state_version:
            raise InvalidAgentState(
                "Decision log state_version must match the committed state version"
            )

        # No await occurs while publishing these values, so readers see all or none.
        self.contexts[request.interview_id] = saved_context
        if saved_question is not None:
            self.questions[saved_question.question_id] = saved_question
            self.question_interviews[saved_question.question_id] = request.interview_id
            question_ids = self.interview_question_ids.setdefault(request.interview_id, [])
            if saved_question.question_id not in question_ids:
                question_ids.append(saved_question.question_id)
        self.decision_logs.append(saved_log)
        if request.feedback_request_id is not None:
            self.processed_feedback_actions[(request.interview_id, request.feedback_request_id)] = (
                saved_action
            )
        return CommitTurnResult(
            committed=True,
            state=saved_context.state.model_copy(deep=True),
            action=saved_action,
        )

    async def get_processed_feedback_action(
        self,
        interview_id: str,
        feedback_request_id: str,
    ) -> InterviewAction | None:
        action = self.processed_feedback_actions.get((interview_id, feedback_request_id))
        return action.model_copy(deep=True) if action is not None else None

    async def get_question(self, question_id: str) -> PlannedQuestion:
        try:
            return self.questions[question_id].model_copy(deep=True)
        except KeyError as error:
            raise InvalidAgentState(f"Question {question_id!r} was not found") from error

    def decision_logs_for(self, interview_id: str) -> list[AgentDecisionLog]:
        return [
            log.model_copy(deep=True)
            for log in self.decision_logs
            if log.interview_id == interview_id
        ]
