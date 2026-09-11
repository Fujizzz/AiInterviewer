"""Public Agent service with restart-safe, atomic interview orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from time import perf_counter
from typing import TypeVar
from uuid import uuid4

from agents.config import AgentSettings, load_agent_settings
from agents.domain.errors import (
    AgentError,
    ContractVersionError,
    InvalidAgentState,
    RepositoryUnavailable,
    StateConflictError,
)
from agents.domain.models import (
    AgentDecisionLog,
    AnchorState,
    CommitTurnRequest,
    InterviewContext,
    PolicyReplayResult,
    ProjectSelection,
    RetrievalBatch,
    TopicSelection,
)
from agents.orchestrator.replay import replay_decision as replay_policy_decision
from agents.orchestrator.state_machine import InterviewStageMachine
from agents.orchestrator.termination import TerminationPolicy
from agents.policies import (
    AnchorPolicy,
    CompetencySelector,
    DifficultyController,
    ProbeController,
    ProjectSelector,
    RedundancyPolicy,
    TopicSelector,
)
from agents.ports import EvaluationPort, InterviewRepositoryPort, LLMPort, RAGPort
from agents.question import (
    FallbackQuestionPolicy,
    QuestionGenerator,
    QuestionPlanner,
    QuestionValidator,
)
from agents.routing import ContextBuilder, RAGRouter
from agents.timeouts import call_with_timeout
from shared.contracts import (
    CONTRACT_VERSION,
    CandidateProfile,
    CandidateProject,
    Competency,
    CompetencyState,
    DecisionTrace,
    EvaluationFeedback,
    InitializeInterviewRequest,
    InitializeInterviewResponse,
    InterviewAction,
    InterviewActionType,
    InterviewPlan,
    InterviewStage,
    InterviewState,
    JobProfile,
    PlannedQuestion,
    QuestionType,
    RetrievalSource,
    StagePlan,
)

ResultT = TypeVar("ResultT")


class InterviewAgentService:
    """Only public entry point for Agent orchestration.

    The object owns immutable policies, adapters, and configuration only. Every
    mutable interview value is loaded from and committed through RepositoryPort.
    """

    def __init__(
        self,
        *,
        repository: InterviewRepositoryPort,
        rag: RAGPort | None = None,
        evaluation: EvaluationPort | None = None,
        llm: LLMPort | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        self._repository = repository
        self._rag = rag
        self._evaluation = evaluation
        self._llm = llm
        self._settings = settings or load_agent_settings()
        self._anchor_policy = AnchorPolicy(self._settings)
        self._competency_selector = CompetencySelector(
            self._settings,
            anchor_policy=self._anchor_policy,
        )
        self._difficulty_controller = DifficultyController(self._settings)
        self._probe_controller = ProbeController(self._settings)
        self._project_selector = ProjectSelector(self._settings)
        self._topic_selector = TopicSelector()
        self._question_planner = QuestionPlanner()
        self._question_generator = (
            QuestionGenerator(llm, self._settings) if llm is not None else None
        )
        self._question_validator = QuestionValidator(self._settings)
        self._fallback_policy = FallbackQuestionPolicy()
        self._rag_router = RAGRouter(rag, self._settings) if rag is not None else None
        self._context_builder = ContextBuilder(self._settings)
        self._redundancy_policy = RedundancyPolicy(self._settings)
        self._stage_machine = InterviewStageMachine()
        self._termination_policy = TerminationPolicy(self._settings)

    async def initialize_interview(
        self,
        request: InitializeInterviewRequest,
    ) -> InitializeInterviewResponse:
        self._validate_contract_version(request.contract_version)
        self._validate_contract_version(request.candidate_profile.contract_version)
        self._validate_contract_version(request.job_profile.contract_version)

        enabled_stages = self._normalize_stages(request.enabled_stages)
        if request.duration_seconds < len(enabled_stages):
            raise InvalidAgentState(
                "duration_seconds must allow at least one second for each enabled stage"
            )
        plan = self._build_plan(request, enabled_stages)
        state = InterviewState(
            interview_id=request.interview_id,
            status="active",
            stage=enabled_stages[0],
            active_project_id=(
                request.candidate_profile.projects[0].project_id
                if request.candidate_profile.projects
                else None
            ),
            remaining_seconds=request.duration_seconds,
            competencies={
                competency: CompetencyState(competency=competency) for competency in Competency
            },
        )
        context = InterviewContext(
            interview_id=request.interview_id,
            candidate_profile=request.candidate_profile.model_copy(deep=True),
            job_profile=request.job_profile.model_copy(deep=True),
            plan=plan,
            state=state,
            current_difficulty={
                competency: self._settings.initial_question_difficulty
                for competency in Competency
            },
            anchor_state={
                competency: AnchorState()
                for competency in self._anchor_policy.required_competencies
            },
            policy_config_version=self._settings.policy_config_version,
        )
        await self._repository_call(
            self._repository.initialize_interview(context),
            operation="initialize interview",
        )
        first_action = await self.next_action(request.interview_id)
        current_context = await self._get_context(request.interview_id)
        return InitializeInterviewResponse(
            interview_id=request.interview_id,
            plan=current_context.plan,
            state=current_context.state,
            first_action=first_action,
        )

    async def next_action(self, interview_id: str) -> InterviewAction:
        return await self._run_turn(interview_id)

    async def apply_evaluation_feedback(
        self,
        interview_id: str,
        feedback: EvaluationFeedback,
    ) -> InterviewAction:
        self._validate_contract_version(feedback.contract_version)
        self._validate_contract_version(feedback.updated_competency_state.contract_version)
        processed = await self._get_processed_action(interview_id, feedback.request_id)
        if processed is not None:
            return processed
        return await self._run_turn(interview_id, feedback=feedback)

    def replay_decision(self, context: InterviewContext) -> PolicyReplayResult:
        """Recompute deterministic policy fields for debugging and comparison."""

        return replay_policy_decision(context, self._settings)

    async def _run_turn(
        self,
        interview_id: str,
        *,
        feedback: EvaluationFeedback | None = None,
    ) -> InterviewAction:
        maximum_recomputations = self._settings.retries.state_conflict_recomputations
        for attempt in range(maximum_recomputations + 1):
            attempt_started = perf_counter()
            if feedback is not None:
                processed = await self._get_processed_action(interview_id, feedback.request_id)
                if processed is not None:
                    return processed
            context = await self._get_context(interview_id)
            if feedback is not None:
                context = await self._context_after_feedback(context, feedback)
            try:
                return await self._decide_and_commit(
                    context,
                    feedback_request_id=(feedback.request_id if feedback is not None else None),
                    started_at=attempt_started,
                )
            except StateConflictError:
                if feedback is not None:
                    processed = await self._get_processed_action(
                        interview_id,
                        feedback.request_id,
                    )
                    if processed is not None:
                        return processed
                if attempt >= maximum_recomputations:
                    raise
        raise AssertionError("bounded conflict loop exited unexpectedly")

    async def _context_after_feedback(
        self,
        context: InterviewContext,
        feedback: EvaluationFeedback,
    ) -> InterviewContext:
        state = context.state
        if feedback.question_id not in state.asked_question_ids:
            raise InvalidAgentState(
                f"Feedback question {feedback.question_id!r} was not asked "
                f"in interview {context.interview_id!r}"
            )
        if feedback.updated_competency_state.competency != feedback.target_competency:
            raise InvalidAgentState("Feedback target competency and updated state do not match")

        current_question = await self._repository_call(
            self._repository.get_question(feedback.question_id),
            operation="load feedback question",
        )
        updated = context.model_copy(deep=True)
        updated.current_difficulty[feedback.target_competency] = (
            self._difficulty_controller.adjust(current_question.difficulty, feedback)
        )
        competency_state = feedback.updated_competency_state.model_copy(deep=True)
        if competency_state.last_asked_at_question_index is None:
            competency_state.last_asked_at_question_index = state.competencies[
                feedback.target_competency
            ].last_asked_at_question_index
        updated.state.competencies[feedback.target_competency] = competency_state
        updated.state.evidence_ids = list(
            dict.fromkeys([*updated.state.evidence_ids, *feedback.evidence_ids])
        )
        feedback_limit = self._settings.context.recent_feedback
        feedback_history = [
            *updated.recent_feedback,
            feedback.model_copy(deep=True),
        ]
        updated.recent_feedback = (
            feedback_history[-feedback_limit:] if feedback_limit else []
        )
        anchor = updated.anchor_state.get(feedback.target_competency)
        if anchor is not None and anchor.asked and anchor.question_id == feedback.question_id:
            anchor.completed = True
        return updated

    async def _decide_and_commit(
        self,
        context: InterviewContext,
        *,
        feedback_request_id: str | None,
        started_at: float,
    ) -> InterviewAction:
        if self._termination_policy.should_finish(context.state, context.plan):
            return await self._finish(
                context,
                feedback_request_id=feedback_request_id,
                started_at=started_at,
            )
        if self._stage_machine.should_transition(context.state, context.plan):
            return await self._change_stage(
                context,
                feedback_request_id=feedback_request_id,
                started_at=started_at,
            )
        return await self._ask_question(
            context,
            feedback_request_id=feedback_request_id,
            started_at=started_at,
        )

    async def _ask_question(
        self,
        context: InterviewContext,
        *,
        feedback_request_id: str | None,
        started_at: float,
    ) -> InterviewAction:
        state = context.state
        plan = context.plan
        selection = self._competency_selector.select(
            state=state,
            plan=plan,
            anchor_state=context.anchor_state,
        )
        project_selection, selected_project = self._select_project(
            profile=context.candidate_profile,
            job=context.job_profile,
            state=state,
            competency=selection.competency,
        )
        probe = self._probe_controller.decide(
            competency=selection.competency,
            state=state,
            plan=plan,
        )
        difficulty = context.current_difficulty.get(
            selection.competency,
            max(
                self._settings.initial_question_difficulty,
                state.competencies[selection.competency].max_verified_difficulty,
            ),
        )
        difficulty = min(difficulty, self._settings.max_question_difficulty)
        continue_topic = bool(
            probe.should_probe
            and state.last_competency == selection.competency
            and state.last_topic
            and selected_project is not None
            and selected_project.project_id == state.active_project_id
        )
        topic_selection = self._select_topic(
            selected_project,
            selection.competency,
            state,
            continue_topic=continue_topic,
        )
        is_same_topic_probe = bool(continue_topic and state.last_topic == topic_selection.topic)
        probe_depth = probe.next_probe_depth if is_same_topic_probe else 1
        previous_questions = await self._get_previous_questions(state)

        planner_started = perf_counter()
        question_plan = self._question_planner.plan(
            target_competency=selection.competency,
            selected_project=selected_project,
            selected_topic=topic_selection,
            difficulty=difficulty,
            probe_depth=probe_depth,
            recent_question_summaries=[
                question.text or question.intent for question in previous_questions
            ],
            recent_feedback_summary=self._feedback_summaries(context.recent_feedback),
            candidate_claims=selected_project.claims if selected_project is not None else (),
        )
        redundancy_reason = "NOT_REDUNDANT"
        force_fallback = False
        if self._redundancy_policy.is_redundant(question_plan, previous_questions):
            alternate = self._alternate_topic(
                project=selected_project,
                competency=selection.competency,
                previous_questions=previous_questions,
            )
            if alternate is not None:
                topic_selection = alternate
                question_plan = self._question_planner.plan(
                    target_competency=selection.competency,
                    selected_project=selected_project,
                    selected_topic=topic_selection,
                    difficulty=difficulty,
                    probe_depth=1,
                    recent_question_summaries=[
                        question.text or question.intent for question in previous_questions
                    ],
                    recent_feedback_summary=self._feedback_summaries(context.recent_feedback),
                    candidate_claims=(
                        selected_project.claims if selected_project is not None else ()
                    ),
                )
                probe_depth = 1
                is_same_topic_probe = False
                redundancy_reason = "ALTERNATE_TOPIC_SELECTED"
            else:
                force_fallback = True
                redundancy_reason = "DUPLICATE_FALLBACK_ANCHOR"
        planner_latency_ms = self._elapsed_ms(planner_started)

        requests = (
            self._rag_router.build_requests(
                question_plan,
                interview_id=state.interview_id,
                candidate_id=context.candidate_profile.candidate_id,
                domain=selected_project.domain if selected_project is not None else None,
            )
            if self._rag_router is not None
            else []
        )
        retrieval_started = perf_counter()
        retrieval_batch = (
            await self._rag_router.retrieve_with_diagnostics(requests)
            if self._rag_router is not None
            else RetrievalBatch()
        )
        retrieval_latency_ms = self._elapsed_ms(retrieval_started)
        prompt_context = self._context_builder.build(
            question_plan=question_plan,
            retrieval_responses=retrieval_batch.responses,
            candidate_profile=context.candidate_profile,
            selected_project=selected_project,
            previous_questions=previous_questions,
            recent_feedback=context.recent_feedback,
        )
        generation_started = perf_counter()
        question, generation_reason = await self._generate_question(
            question_plan,
            prompt_context,
            selected_project,
            force_fallback=force_fallback,
        )
        generation_latency_ms = self._elapsed_ms(generation_started)
        all_retrieval_failed = bool(requests) and not retrieval_batch.responses
        fallback_used = generation_reason not in {"LLM_GENERATED", "LLM_REPAIRED"} or bool(
            all_retrieval_failed
        )

        updated_context = context.model_copy(deep=True)
        updated_state = updated_context.state
        updated_state.question_index += 1
        updated_state.current_question_id = question.question_id
        updated_state.asked_question_ids.append(question.question_id)
        updated_state.last_competency = question.target_competency
        updated_state.last_question_type = question.question_type
        updated_state.last_topic = question.topic
        updated_state.active_project_id = question.project_id
        updated_state.consecutive_probes = (
            state.consecutive_probes + 1 if is_same_topic_probe else 0
        )
        updated_state.competencies[
            question.target_competency
        ].last_asked_at_question_index = updated_state.question_index
        if question.project_id is not None:
            updated_state.project_visit_count[question.project_id] = (
                updated_state.project_visit_count.get(question.project_id, 0) + 1
            )
        anchor = updated_context.anchor_state.get(question.target_competency)
        if (
            selection.reason_code == "ANCHOR_NOT_COMPLETED"
            and anchor is not None
            and not anchor.asked
        ):
            anchor.asked = True
            anchor.question_id = question.question_id

        action = InterviewAction(
            action_id=str(uuid4()),
            interview_id=state.interview_id,
            type=InterviewActionType.ASK_QUESTION,
            question=question,
            decision_trace=DecisionTrace(
                selected_competency_priority=selection.priority,
                reason_code=selection.reason_code,
                details={
                    "all_priorities": {
                        competency.value: priority
                        for competency, priority in selection.all_priorities.items()
                    },
                    "probe_reason": probe.reason_code,
                    "project_reason": (
                        project_selection.reason_code
                        if project_selection is not None
                        else "NO_PROJECT"
                    ),
                    "project_scores": (
                        project_selection.all_scores if project_selection is not None else {}
                    ),
                    "topic_reason": topic_selection.reason_code,
                    "question_type": question.question_type.value,
                    "rag_sources": [request.source.value for request in requests],
                    "rag_failed_sources": [
                        source.value for source in retrieval_batch.failed_sources
                    ],
                    "rag_timeout_sources": [
                        source.value for source in retrieval_batch.timeout_sources
                    ],
                    "fallback_used": fallback_used,
                    "generation_reason": generation_reason,
                    "redundancy_reason": redundancy_reason,
                    "planner_latency_ms": planner_latency_ms,
                    "retrieval_latency_ms": retrieval_latency_ms,
                    "generation_latency_ms": generation_latency_ms,
                },
            ),
        )
        decision_reasons = {
            "competency": selection.reason_code,
            "project": (
                project_selection.reason_code if project_selection is not None else "NO_PROJECT"
            ),
            "topic": topic_selection.reason_code,
            "probe": probe.reason_code,
            "generation": generation_reason,
            "redundancy": redundancy_reason,
        }
        log = self._decision_log(
            action,
            context,
            selected_competency=selection.competency,
            priorities=selection.all_priorities,
            difficulty=difficulty,
            probe_depth=probe_depth,
            topic=topic_selection.topic,
            question_type=question.question_type,
            rag_sources=[request.source for request in requests],
            failed_sources=retrieval_batch.failed_sources,
            timeout_sources=retrieval_batch.timeout_sources,
            fallback_used=fallback_used,
            planner_latency_ms=planner_latency_ms,
            retrieval_latency_ms=retrieval_latency_ms,
            generation_latency_ms=generation_latency_ms,
            total_agent_latency_ms=self._elapsed_ms(started_at),
            decision_reasons=decision_reasons,
        )
        return await self._commit_turn(
            original_context=context,
            updated_context=updated_context,
            action=action,
            question=question,
            decision_log=log,
            feedback_request_id=feedback_request_id,
        )

    async def _change_stage(
        self,
        context: InterviewContext,
        *,
        feedback_request_id: str | None,
        started_at: float,
    ) -> InterviewAction:
        state = context.state
        next_stage = self._stage_machine.next_stage(state, context.plan)
        updated_context = context.model_copy(deep=True)
        updated_context.state.stage = next_stage
        if next_stage == InterviewStage.FINISHED:
            updated_context.state.status = "finished"
        action = InterviewAction(
            action_id=f"{state.interview_id}-stage-{state.state_version + 1}",
            interview_id=state.interview_id,
            type=InterviewActionType.CHANGE_STAGE,
            from_stage=state.stage,
            to_stage=next_stage,
            decision_trace=DecisionTrace(reason_code="STAGE_BUDGET_EXHAUSTED"),
        )
        log = self._decision_log(
            action,
            context,
            total_agent_latency_ms=self._elapsed_ms(started_at),
        )
        return await self._commit_turn(
            original_context=context,
            updated_context=updated_context,
            action=action,
            question=None,
            decision_log=log,
            feedback_request_id=feedback_request_id,
        )

    async def _finish(
        self,
        context: InterviewContext,
        *,
        feedback_request_id: str | None,
        started_at: float,
    ) -> InterviewAction:
        state = context.state
        updated_context = context.model_copy(deep=True)
        updated_context.state.stage = InterviewStage.FINISHED
        updated_context.state.status = "finished"
        action = InterviewAction(
            action_id=f"{state.interview_id}-finish-{state.state_version + 1}",
            interview_id=state.interview_id,
            type=InterviewActionType.FINISH,
            from_stage=state.stage,
            to_stage=InterviewStage.FINISHED,
            decision_trace=DecisionTrace(
                reason_code=self._termination_policy.reason_code(state)
            ),
        )
        log = self._decision_log(
            action,
            context,
            total_agent_latency_ms=self._elapsed_ms(started_at),
        )
        return await self._commit_turn(
            original_context=context,
            updated_context=updated_context,
            action=action,
            question=None,
            decision_log=log,
            feedback_request_id=feedback_request_id,
        )

    async def _commit_turn(
        self,
        *,
        original_context: InterviewContext,
        updated_context: InterviewContext,
        action: InterviewAction,
        question: PlannedQuestion | None,
        decision_log: AgentDecisionLog,
        feedback_request_id: str | None,
    ) -> InterviewAction:
        request = CommitTurnRequest(
            interview_id=original_context.interview_id,
            expected_state_version=original_context.state.state_version,
            new_state=updated_context.state,
            new_context=updated_context,
            question=question,
            decision_log=decision_log,
            feedback_request_id=feedback_request_id,
            resulting_action=action,
        )
        result = await self._repository_call(
            self._repository.commit_turn(request),
            operation="commit interview turn",
        )
        if not result.committed:
            raise RepositoryUnavailable("Repository did not commit the interview turn")
        return result.action

    def _decision_log(
        self,
        action: InterviewAction,
        context: InterviewContext,
        *,
        selected_competency: Competency | None = None,
        priorities: dict[Competency, float] | None = None,
        difficulty: int | None = None,
        probe_depth: int | None = None,
        topic: str | None = None,
        question_type: QuestionType | None = None,
        rag_sources: Sequence[RetrievalSource] = (),
        failed_sources: Sequence[RetrievalSource] = (),
        timeout_sources: Sequence[RetrievalSource] = (),
        fallback_used: bool = False,
        planner_latency_ms: int = 0,
        retrieval_latency_ms: int = 0,
        generation_latency_ms: int = 0,
        total_agent_latency_ms: int = 0,
        decision_reasons: dict[str, str] | None = None,
    ) -> AgentDecisionLog:
        return AgentDecisionLog(
            decision_id=action.action_id,
            interview_id=context.interview_id,
            state_version=context.state.state_version + 1,
            selected_competency=selected_competency,
            competency_priorities={
                competency.value: priority for competency, priority in (priorities or {}).items()
            },
            selected_project=(
                action.question.project_id if action.question is not None else None
            ),
            selected_project_id=(
                action.question.project_id if action.question is not None else None
            ),
            selected_topic=topic,
            difficulty=difficulty,
            probe_depth=probe_depth,
            question_type=question_type,
            action_type=action.type,
            reason_code=action.decision_trace.reason_code,
            decision_reasons=decision_reasons or {},
            retrieval_sources=[source.value for source in rag_sources],
            rag_sources_requested=[source.value for source in rag_sources],
            failed_retrieval_sources=[source.value for source in failed_sources],
            timeout_retrieval_sources=[source.value for source in timeout_sources],
            fallback_used=fallback_used,
            planner_latency_ms=planner_latency_ms,
            retrieval_latency_ms=retrieval_latency_ms,
            generation_latency_ms=generation_latency_ms,
            total_agent_latency_ms=total_agent_latency_ms,
            prompt_version=(QuestionGenerator.prompt_name if question_type is not None else None),
            planner_prompt_version=(
                QuestionPlanner.prompt_name if question_type is not None else None
            ),
            generator_prompt_version=(
                QuestionGenerator.prompt_name if question_type is not None else None
            ),
            policy_config_version=context.policy_config_version,
            model="mock-or-configured-llm" if self._llm is not None else None,
        )

    def _select_project(
        self,
        *,
        profile: CandidateProfile,
        job: JobProfile,
        state: InterviewState,
        competency: Competency,
    ) -> tuple[ProjectSelection | None, CandidateProject | None]:
        if not profile.projects:
            return None, None
        selection = self._project_selector.select(
            profile=profile,
            job=job,
            state=state,
            competency=competency,
        )
        project = next(
            project for project in profile.projects if project.project_id == selection.project_id
        )
        return selection, project

    def _select_topic(
        self,
        project: CandidateProject | None,
        competency: Competency,
        state: InterviewState,
        *,
        continue_topic: bool,
    ) -> TopicSelection:
        if continue_topic and state.last_topic is not None:
            return TopicSelection(topic=state.last_topic, reason_code="CONTINUE_PROBE_TOPIC")
        if project is None:
            return TopicSelection(topic="project experience", reason_code="NO_PROJECT_FALLBACK")
        recent_topics = (
            [state.last_topic] if state.last_topic and state.last_competency == competency else []
        )
        return self._topic_selector.select(
            project=project,
            competency=competency,
            recent_topics=recent_topics,
        )

    def _alternate_topic(
        self,
        *,
        project: CandidateProject | None,
        competency: Competency,
        previous_questions: Sequence[PlannedQuestion],
    ) -> TopicSelection | None:
        if project is None:
            return None
        excluded = {
            question.topic
            for question in previous_questions
            if question.target_competency == competency
            and question.project_id == project.project_id
            and question.topic is not None
        }
        return self._topic_selector.select_alternate(
            project=project,
            competency=competency,
            excluded_topics=excluded,
        )

    async def _generate_question(
        self,
        question_plan: PlannedQuestion,
        context: str,
        project: CandidateProject | None,
        *,
        force_fallback: bool,
    ) -> tuple[PlannedQuestion, str]:
        if not force_fallback and self._question_generator is not None:
            repair_errors: list[str] | None = None
            attempts = self._settings.retries.llm_generation_retries + 1
            for attempt in range(attempts):
                try:
                    candidate = await self._question_generator.generate(
                        question_plan,
                        context,
                        repair_errors=repair_errors,
                    )
                    validation = self._question_validator.validate(candidate, question_plan)
                    if validation.is_valid:
                        return candidate, "LLM_GENERATED" if attempt == 0 else "LLM_REPAIRED"
                    repair_errors = validation.errors
                except Exception as error:  # external LLM failures degrade to a safe question
                    repair_errors = [f"GENERATION_ERROR:{type(error).__name__}"]

        candidate_fallback = self._fallback_policy.apply(question_plan, project=project)
        if self._question_validator.is_valid(candidate_fallback, question_plan):
            return candidate_fallback, "CANDIDATE_SPECIFIC_FALLBACK"
        generic_fallback = self._fallback_policy.apply(question_plan, generic=True)
        return generic_fallback, "GENERIC_ANCHOR_FALLBACK"

    async def _get_previous_questions(
        self,
        state: InterviewState,
    ) -> list[PlannedQuestion]:
        limit = self._settings.context.recent_questions
        question_ids = state.asked_question_ids[-limit:] if limit else []
        if not question_ids:
            return []
        return list(
            await asyncio.gather(
                *(
                    self._repository_call(
                        self._repository.get_question(question_id),
                        operation="load previous question",
                    )
                    for question_id in question_ids
                )
            )
        )

    @staticmethod
    def _feedback_summaries(feedback_history: Sequence[EvaluationFeedback]) -> list[str]:
        return [
            (
                f"{feedback.target_competency.value}: relevance={feedback.answer_relevance:.2f}, "
                f"evidence={feedback.evidence_strength:.2f}, "
                f"confidence={feedback.evaluation_confidence:.2f}"
            )
            for feedback in feedback_history
        ]

    async def _get_context(self, interview_id: str) -> InterviewContext:
        return await self._repository_call(
            self._repository.get_interview_context(interview_id),
            operation="load interview context",
        )

    async def _get_processed_action(
        self,
        interview_id: str,
        feedback_request_id: str,
    ) -> InterviewAction | None:
        return await self._repository_call(
            self._repository.get_processed_feedback_action(
                interview_id,
                feedback_request_id,
            ),
            operation="check feedback idempotency",
        )

    async def _repository_call(
        self,
        awaitable: Awaitable[ResultT],
        *,
        operation: str,
    ) -> ResultT:
        try:
            return await call_with_timeout(
                awaitable,
                timeout_seconds=self._settings.timeouts.repository_seconds,
                error_factory=lambda: RepositoryUnavailable(
                    f"Repository timed out while attempting to {operation}"
                ),
            )
        except AgentError:
            raise
        except Exception as error:
            raise RepositoryUnavailable(
                f"Repository failed while attempting to {operation}: "
                f"{type(error).__name__}"
            ) from error

    def _build_plan(
        self,
        request: InitializeInterviewRequest,
        enabled_stages: Sequence[InterviewStage],
    ) -> InterviewPlan:
        importance = {
            competency: request.job_profile.competency_importance.get(competency, 0.0)
            for competency in Competency
        }
        return InterviewPlan(
            interview_id=request.interview_id,
            duration_seconds=request.duration_seconds,
            stages=self._allocate_stage_budgets(request.duration_seconds, enabled_stages),
            competency_importance=importance,
            target_coverage={
                competency: self._settings.target.default_coverage
                for competency in Competency
            },
            target_confidence={
                competency: self._settings.target.default_confidence
                for competency in Competency
            },
            max_consecutive_probes=self._settings.max_consecutive_probes,
        )

    @staticmethod
    def _normalize_stages(stages: Sequence[InterviewStage]) -> list[InterviewStage]:
        enabled = list(dict.fromkeys(stage for stage in stages if stage != InterviewStage.FINISHED))
        if not enabled:
            raise InvalidAgentState("At least one non-finished interview stage must be enabled")
        return enabled

    @staticmethod
    def _allocate_stage_budgets(
        duration_seconds: int,
        stages: Sequence[InterviewStage],
    ) -> list[StagePlan]:
        base_budget, remainder = divmod(duration_seconds, len(stages))
        return [
            StagePlan(
                stage=stage,
                budget_seconds=base_budget + (1 if index < remainder else 0),
            )
            for index, stage in enumerate(stages)
        ]

    @staticmethod
    def _validate_contract_version(contract_version: str) -> None:
        if contract_version != CONTRACT_VERSION:
            raise ContractVersionError(
                f"Unsupported contract version {contract_version!r}; expected {CONTRACT_VERSION!r}"
            )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, round((perf_counter() - started_at) * 1000))
