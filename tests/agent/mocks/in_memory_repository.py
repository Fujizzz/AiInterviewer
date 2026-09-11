"""In-memory RepositoryPort v1.1 adapter with atomic turn semantics."""

from agents.domain.errors import InvalidAgentState, StateConflictError
from agents.domain.models import (
    AgentDecisionLog,
    CommitTurnRequest,
    CommitTurnResult,
    InterviewContext,
)
from shared.contracts import InterviewAction, InterviewPlan, InterviewState, PlannedQuestion


class InMemoryRepository:
    """Store deep copies and publish a turn only after every check succeeds."""

    def __init__(self) -> None:
        self.contexts: dict[str, InterviewContext] = {}
        self.states: dict[str, InterviewState] = {}
        self.plans: dict[str, InterviewPlan] = {}
        self.questions: dict[str, PlannedQuestion] = {}
        self.question_interviews: dict[str, str] = {}
        self.interview_question_ids: dict[str, list[str]] = {}
        self.decision_logs: list[AgentDecisionLog] = []
        self.processed_feedback_actions: dict[tuple[str, str], InterviewAction] = {}
        # Compatibility view for v1.0 adapters that keyed request IDs globally.
        self.processed_feedback_ids: dict[str, InterviewAction] = {}

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
        self.states[saved.interview_id] = saved.state.model_copy(deep=True)
        self.plans[saved.interview_id] = saved.plan.model_copy(deep=True)
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
            existing_question = self.questions.get(request.question.question_id)
            if existing_question is not None and existing_question != request.question:
                raise StateConflictError(
                    f"Question {request.question.question_id!r} already has other content"
                )

        # Construct every value before publishing any mutation. Publication below
        # has no await point, so the in-memory adapter has all-or-nothing semantics.
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
                "Decision log state_version must be the version produced by commit_turn"
            )

        self.contexts[request.interview_id] = saved_context
        self.states[request.interview_id] = saved_context.state.model_copy(deep=True)
        self.plans[request.interview_id] = saved_context.plan.model_copy(deep=True)
        if saved_question is not None:
            self.questions[saved_question.question_id] = saved_question
            self.question_interviews[saved_question.question_id] = request.interview_id
            question_ids = self.interview_question_ids.setdefault(request.interview_id, [])
            if saved_question.question_id not in question_ids:
                question_ids.append(saved_question.question_id)
        self.decision_logs.append(saved_log)
        if request.feedback_request_id is not None:
            key = (request.interview_id, request.feedback_request_id)
            self.processed_feedback_actions[key] = saved_action
            self.processed_feedback_ids[request.feedback_request_id] = saved_action.model_copy(
                deep=True
            )
        return CommitTurnResult(
            committed=True,
            state=saved_context.state.model_copy(deep=True),
            action=saved_action,
        )

    async def get_processed_feedback_action(
        self,
        interview_id: str,
        feedback_request_id: str | None = None,
    ) -> InterviewAction | None:
        if feedback_request_id is None:
            action = self.processed_feedback_ids.get(interview_id)
        else:
            action = self.processed_feedback_actions.get((interview_id, feedback_request_id))
        return action.model_copy(deep=True) if action is not None else None

    # v1.0 compatibility layer. InterviewAgentService never calls these methods.
    async def get_state(self, interview_id: str) -> InterviewState:
        if interview_id in self.contexts:
            return self.contexts[interview_id].state.model_copy(deep=True)
        try:
            return self.states[interview_id].model_copy(deep=True)
        except KeyError as error:
            raise InvalidAgentState(f"Interview state {interview_id!r} was not found") from error

    async def save_state(
        self,
        state: InterviewState,
        expected_version: int | None = None,
    ) -> InterviewState:
        stored_state = self.states.get(state.interview_id)
        current_version = (
            stored_state.state_version if stored_state is not None else state.state_version
        )
        if expected_version is not None and expected_version != current_version:
            raise StateConflictError(
                f"Expected state version {expected_version}, current version is {current_version}"
            )
        if stored_state is not None and state.state_version != current_version:
            raise StateConflictError(
                f"State payload version {state.state_version}, current version is {current_version}"
            )
        saved_state = state.model_copy(deep=True)
        saved_state.state_version = current_version + 1
        self.states[state.interview_id] = saved_state
        if state.interview_id in self.contexts:
            self.contexts[state.interview_id].state = saved_state.model_copy(deep=True)
        return saved_state.model_copy(deep=True)

    async def save_interview_plan(self, plan: InterviewPlan) -> None:
        self.plans[plan.interview_id] = plan.model_copy(deep=True)
        if plan.interview_id in self.contexts:
            self.contexts[plan.interview_id].plan = plan.model_copy(deep=True)

    async def get_interview_plan(self, interview_id: str) -> InterviewPlan:
        if interview_id in self.contexts:
            return self.contexts[interview_id].plan.model_copy(deep=True)
        try:
            return self.plans[interview_id].model_copy(deep=True)
        except KeyError as error:
            raise InvalidAgentState(f"Interview plan {interview_id!r} was not found") from error

    async def save_question(self, question: PlannedQuestion) -> None:
        self.questions[question.question_id] = question.model_copy(deep=True)

    async def get_question(self, question_id: str) -> PlannedQuestion:
        try:
            return self.questions[question_id].model_copy(deep=True)
        except KeyError as error:
            raise InvalidAgentState(f"Question {question_id!r} was not found") from error

    async def append_decision_log(self, log: AgentDecisionLog) -> None:
        self.decision_logs.append(log.model_copy(deep=True))

    async def save_processed_feedback_action(
        self,
        request_id: str,
        action: InterviewAction,
    ) -> None:
        existing = self.processed_feedback_ids.get(request_id)
        if existing is not None and existing != action:
            raise StateConflictError(f"Feedback request {request_id!r} already has another action")
        self.processed_feedback_ids[request_id] = action.model_copy(deep=True)
