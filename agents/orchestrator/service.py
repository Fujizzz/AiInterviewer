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
    CommitTurnRequest,
    InterviewContext,
    InterviewHistoryEntry,
    PolicyReplayResult,
    ProbeDecision,
    RetrievalBatch,
    TopicSelection,
)
from agents.evidence import apply_evidence
from agents.orchestrator.replay import replay_decision as replay_policy_decision
from agents.orchestrator.state_machine import InterviewStageMachine
from agents.orchestrator.termination import TerminationPolicy
from agents.policies import DifficultyController
from agents.policies.dialogue_controller import DialogueController
from agents.policies.dialogue_policy import choose_dialogue
from agents.ports import EvaluationPort, InterviewRepositoryPort, LLMPort, RAGPort
from agents.question import (
    FallbackQuestionPolicy,
    QuestionGenerator,
    QuestionPlanner,
    QuestionValidator,
)
from agents.question.dialogue import followup_block
from agents.question.react import QuestionAgentResult, ReactQuestionAgent
from agents.routing import ContextBuilder, RAGRouter
from agents.timeouts import call_with_timeout
from agents.tracing import emit_trace
from shared.contracts import (
    CONTRACT_VERSION,
    CandidateAnswer,
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
        self._difficulty_controller = DifficultyController(self._settings)
        self._question_planner = QuestionPlanner()
        self._question_generator = (
            QuestionGenerator(llm, self._settings) if llm is not None else None
        )
        self._question_agent = (
            ReactQuestionAgent(llm, self._settings)
            if llm is not None and self._settings.question_agent.enabled
            else None
        )
        self._question_validator = QuestionValidator(self._settings)
        self._fallback_policy = FallbackQuestionPolicy()
        self._rag_router = RAGRouter(rag, self._settings) if rag is not None else None
        self._context_builder = ContextBuilder(self._settings)
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
            thread_difficulty=self._settings.initial_question_difficulty,
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
        *,
        elapsed_seconds: int = 0,
        answer: CandidateAnswer | None = None,
    ) -> InterviewAction:
        if elapsed_seconds < 0:
            raise InvalidAgentState("elapsed_seconds must be nonnegative")
        self._validate_contract_version(feedback.contract_version)
        if answer is not None:
            self._validate_contract_version(answer.contract_version)
            if answer.interview_id != interview_id or answer.question_id != feedback.question_id:
                raise InvalidAgentState("Answer must match the interview and feedback question")
            if not answer.text.strip():
                raise InvalidAgentState("Answer must not be empty")
        processed = await self._get_processed_action(interview_id, feedback.request_id)
        if processed is not None:
            return processed
        return await self._run_turn(
            interview_id,
            feedback=feedback,
            elapsed_seconds=elapsed_seconds,
            answer=answer,
        )

    def replay_decision(self, context: InterviewContext) -> PolicyReplayResult:
        """Recompute deterministic policy fields for debugging and comparison."""

        return replay_policy_decision(context, self._settings)

    async def _run_turn(
        self,
        interview_id: str,
        *,
        feedback: EvaluationFeedback | None = None,
        elapsed_seconds: int = 0,
        answer: CandidateAnswer | None = None,
    ) -> InterviewAction:
        maximum_recomputations = self._settings.retries.state_conflict_recomputations
        for attempt in range(maximum_recomputations + 1):
            attempt_started = perf_counter()
            if feedback is not None:
                processed = await self._get_processed_action(interview_id, feedback.request_id)
                if processed is not None:
                    return processed
            context = await self._get_context(interview_id)
            emit_trace("turn.loaded", attempt=attempt + 1, context=context.model_dump(mode="json"))
            if feedback is not None:
                context = await self._context_after_feedback(
                    context,
                    feedback,
                    elapsed_seconds=elapsed_seconds,
                    answer=answer,
                )
            try:
                return await self._decide_and_commit(
                    context,
                    feedback_request_id=(feedback.request_id if feedback is not None else None),
                    started_at=attempt_started,
                )
            except StateConflictError:
                emit_trace("turn.conflict", interview_id=interview_id, attempt=attempt + 1)
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
        *,
        elapsed_seconds: int,
        answer: CandidateAnswer | None = None,
    ) -> InterviewContext:
        state = context.state
        if feedback.question_id not in state.asked_question_ids:
            raise InvalidAgentState(
                f"Feedback question {feedback.question_id!r} was not asked "
                f"in interview {context.interview_id!r}"
            )
        if feedback.question_id != state.current_question_id:
            raise InvalidAgentState("Feedback must target the current question")
        current_question = await self._repository_call(
            self._repository.get_question(feedback.question_id),
            operation="load feedback question",
        )
        updated = context.model_copy(deep=True)
        updated.question_history.append(
            InterviewHistoryEntry(
                question=current_question.model_copy(deep=True),
                answer=answer.model_copy(deep=True) if answer is not None else None,
                feedback=feedback.model_copy(deep=True),
            )
        )
        updated.question_history = updated.question_history[
            -self._settings.question_agent.history_retention :
        ]
        updated.state.elapsed_seconds += elapsed_seconds
        updated.state.remaining_seconds = max(
            0,
            updated.state.remaining_seconds - elapsed_seconds,
        )
        updated.thread_difficulty = self._difficulty_controller.adjust(
            current_question.difficulty, feedback
        )
        apply_evidence(updated, current_question, answer, feedback)
        if updated.active_thread is not None:
            updated.active_thread.no_information_count = (
                0
                if feedback.analysis.new_information
                else updated.active_thread.no_information_count + 1
            )
        updated.state.evidence_ids = list(
            dict.fromkeys([*updated.state.evidence_ids, *feedback.evidence_ids])
        )
        feedback_limit = self._settings.context.recent_feedback
        feedback_history = [
            *updated.recent_feedback,
            feedback.model_copy(deep=True),
        ]
        updated.recent_feedback = feedback_history[-feedback_limit:] if feedback_limit else []
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
        planner_started = perf_counter()
        project, topic, probe = choose_dialogue(context, self._settings)
        if (
            topic is None
            and self._question_agent
            and not followup_block(context, self._settings)
            and not context.question_history[-1].feedback.analysis.thread_complete
        ):
            active = context.active_thread
            project = next(
                (
                    p
                    for p in context.candidate_profile.projects
                    if p.project_id == active.project_id
                ),
                None,
            )
            topic = TopicSelection(
                topic=active.topic,
                topic_key=active.topic_key,
                reason_code="FALLBACK_CURRENT_THREAD",
            )
            probe = ProbeDecision(
                should_probe=True,
                next_probe_depth=active.follow_up_count + 2,
                reason_code="FALLBACK_CONTINUE",
                dialogue_action="probe",
                information_goal="Explain one concrete implementation step",
            )
        if topic is None:
            return await self._finish(
                context,
                feedback_request_id=feedback_request_id,
                started_at=started_at,
                reason="NO_MORE_TOPICS",
            )
        latest = context.question_history[-1] if context.question_history else None
        question_plan = self._question_planner.plan(
            project=project,
            topic=topic,
            probe=probe,
            thread=context.active_thread,
            difficulty=(
                context.thread_difficulty
                if probe.should_probe
                else self._settings.initial_question_difficulty
            ),
            parent_question_id=state.current_question_id,
            answer_excerpt=(latest.answer.text[:1000] if latest and latest.answer else ""),
        )
        if (
            not probe.should_probe
            and context.active_thread
            and (question_plan.project_id != context.active_thread.project_id)
        ):
            question_plan.dialogue_action = "new_project"
        planner_latency_ms = self._elapsed_ms(planner_started)
        emit_trace("question.started", question_id=question_plan.question_id)
        previous_questions = await self._get_previous_questions(state)
        requests = (
            self._rag_router.build_requests(
                question_plan,
                interview_id=state.interview_id,
                candidate_id=context.candidate_profile.candidate_id,
                domain=project.domain if project else None,
            )
            if self._rag_router and self._question_agent is None
            else []
        )
        retrieval_started = perf_counter()
        batch = (
            await self._rag_router.retrieve_with_diagnostics(requests)
            if self._rag_router and requests
            else RetrievalBatch()
        )
        retrieval_latency_ms = self._elapsed_ms(retrieval_started)
        prompt_context = (
            ""
            if self._question_agent
            else self._context_builder.build(
                question_plan=question_plan,
                retrieval_responses=batch.responses,
                candidate_profile=context.candidate_profile,
                selected_project=project,
                previous_questions=previous_questions,
                recent_feedback=context.recent_feedback,
            )
        )
        agent_result = QuestionAgentResult()
        generation_started = perf_counter()
        question, generation_reason = await self._generate_question(
            question_plan,
            prompt_context,
            project,
            force_fallback=False,
            interview=context,
            previous_questions=previous_questions,
            agent_result=agent_result,
        )
        generation_latency_ms = self._elapsed_ms(generation_started)
        if agent_result.question is not None:
            project = next(
                (
                    p
                    for p in context.candidate_profile.projects
                    if p.project_id == question.project_id
                ),
                None,
            )
            topic = TopicSelection(
                topic=question.topic, topic_key=question.topic_key, reason_code="MODEL_SELECTED"
            )
            probe = ProbeDecision(
                should_probe=question.dialogue_action in {"clarify", "probe"},
                next_probe_depth=question.probe_depth,
                reason_code="MODEL_DIALOGUE_DECISION",
                dialogue_action=question.dialogue_action,
                information_goal=question.information_goal,
            )
        emit_trace(
            "question.planned",
            plan=question.model_dump(mode="json"),
            selection={"reason_code": topic.reason_code},
            probe=probe.model_dump(mode="json"),
            decision_source="model" if agent_result.question is not None else "policy_fallback",
            decision_summary=agent_result.decision_summary,
            project_name=project.name if project else "General experience",
            budget=DialogueController(context, self._settings).budget_view(
                question.project_id, question.topic_key
            ),
        )
        emit_trace(
            "question.generated",
            question=question.model_dump(mode="json"),
            generation_reason=generation_reason,
            react_stop_reason=agent_result.stop_reason,
            duration_ms=generation_latency_ms,
        )
        updated = context.model_copy(deep=True)
        DialogueController(updated, self._settings).record_question(
            question, closed_reason=probe.reason_code
        )
        updated.state.question_index += 1
        updated.state.current_question_id = question.question_id
        updated.state.asked_question_ids.append(question.question_id)
        updated.state.last_question_type = question.question_type
        updated.state.last_topic = question.topic
        updated.state.active_project_id = question.project_id
        updated.state.consecutive_probes = updated.active_thread.follow_up_count
        fallback = generation_reason not in {"LLM_GENERATED", "LLM_REPAIRED"}
        reasons = {
            "dialogue": probe.reason_code,
            "topic": topic.reason_code,
            "generation": generation_reason,
            "decision_summary": agent_result.decision_summary,
        }
        action = InterviewAction(
            action_id=str(uuid4()),
            interview_id=state.interview_id,
            type=InterviewActionType.ASK_QUESTION,
            question=question,
            decision_trace=DecisionTrace(
                reason_code=probe.reason_code,
                details={
                    "dialogue_action": question.dialogue_action,
                    "parent_question_id": question.parent_question_id,
                    "thread_id": question.thread_id,
                    "probe_reason": probe.reason_code,
                    "topic_reason": topic.reason_code,
                    "generation_reason": generation_reason,
                    "fallback_used": fallback,
                    "decision_source": "model"
                    if agent_result.question is not None
                    else "policy_fallback",
                    "decision_summary": agent_result.decision_summary,
                    "question_agent_steps": agent_result.steps,
                    "question_agent_stop_reason": agent_result.stop_reason,
                    "rag_sources": [r.source.value for r in requests],
                    "rag_failed_sources": [source.value for source in batch.failed_sources],
                    "rag_timeout_sources": [source.value for source in batch.timeout_sources],
                    "planner_latency_ms": planner_latency_ms,
                    "retrieval_latency_ms": retrieval_latency_ms,
                    "generation_latency_ms": generation_latency_ms,
                },
            ),
        )
        log = self._decision_log(
            action,
            context,
            difficulty=question.difficulty,
            probe_depth=question.probe_depth,
            topic=question.topic,
            question_type=question.question_type,
            rag_sources=[r.source for r in requests],
            failed_sources=batch.failed_sources,
            timeout_sources=batch.timeout_sources,
            fallback_used=fallback,
            planner_latency_ms=planner_latency_ms,
            retrieval_latency_ms=retrieval_latency_ms,
            generation_latency_ms=generation_latency_ms,
            question_agent_result=agent_result,
            total_agent_latency_ms=self._elapsed_ms(started_at),
            decision_reasons=reasons,
        )
        return await self._commit_turn(
            original_context=context,
            updated_context=updated,
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
        if updated_context.active_thread:
            updated_context.active_thread.closed_reason = "STAGE_CHANGED"
            updated_context.closed_threads.append(updated_context.active_thread)
            updated_context.active_thread = None
        updated_context.state.consecutive_probes = 0
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
        reason: str | None = None,
    ) -> InterviewAction:
        state = context.state
        updated_context = context.model_copy(deep=True)
        if updated_context.active_thread:
            updated_context.active_thread.closed_reason = reason or "INTERVIEW_FINISHED"
        updated_context.state.stage = InterviewStage.FINISHED
        updated_context.state.status = "finished"
        action = InterviewAction(
            action_id=f"{state.interview_id}-finish-{state.state_version + 1}",
            interview_id=state.interview_id,
            type=InterviewActionType.FINISH,
            from_stage=state.stage,
            to_stage=InterviewStage.FINISHED,
            decision_trace=DecisionTrace(
                reason_code=reason or self._termination_policy.reason_code(state)
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
        emit_trace(
            "turn.committed",
            interview_id=original_context.interview_id,
            action=result.action.model_dump(mode="json"),
            state=result.state.model_dump(mode="json"),
            decision_log=decision_log.model_dump(mode="json"),
        )
        return result.action

    def _decision_log(
        self,
        action: InterviewAction,
        context: InterviewContext,
        *,
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
        question_agent_result: QuestionAgentResult | None = None,
    ) -> AgentDecisionLog:
        return AgentDecisionLog(
            decision_id=action.action_id,
            interview_id=context.interview_id,
            state_version=context.state.state_version + 1,
            dialogue_action=action.question.dialogue_action if action.question else None,
            parent_question_id=action.question.parent_question_id if action.question else None,
            thread_id=action.question.thread_id if action.question else None,
            selected_project=(action.question.project_id if action.question is not None else None),
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
            question_agent_steps=(question_agent_result.steps if question_agent_result else []),
            question_agent_stop_reason=(
                question_agent_result.stop_reason if question_agent_result else None
            ),
            total_agent_latency_ms=total_agent_latency_ms,
            prompt_version=(self._generation_prompt_name if question_type is not None else None),
            planner_prompt_version=(
                QuestionPlanner.prompt_name if question_type is not None else None
            ),
            generator_prompt_version=(
                self._generation_prompt_name if question_type is not None else None
            ),
            policy_config_version=context.policy_config_version,
            model="mock-or-configured-llm" if self._llm is not None else None,
        )

    async def _generate_question(
        self,
        question_plan: PlannedQuestion,
        context: str,
        project: CandidateProject | None,
        *,
        force_fallback: bool,
        interview: InterviewContext,
        previous_questions: Sequence[PlannedQuestion],
        agent_result: QuestionAgentResult,
    ) -> tuple[PlannedQuestion, str]:
        if not force_fallback and self._question_agent is not None:
            result = await self._question_agent.generate(
                question_plan,
                context,
                interview=interview,
                previous_questions=previous_questions,
                autonomous=True,
            )
            agent_result.question = result.question
            agent_result.decision_summary = result.decision_summary
            agent_result.steps = result.steps
            agent_result.stop_reason = result.stop_reason
            if result.question is not None:
                return result.question, "LLM_REPAIRED" if result.repaired else "LLM_GENERATED"
        elif not force_fallback and self._question_generator is not None:
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
        generic_fallback = self._fallback_policy.apply(question_plan, project=project, generic=True)
        return generic_fallback, "GENERIC_ANCHOR_FALLBACK"

    @property
    def _generation_prompt_name(self) -> str:
        return (
            ReactQuestionAgent.prompt_name
            if self._question_agent is not None
            else QuestionGenerator.prompt_name
        )

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
                f"Repository failed while attempting to {operation}: {type(error).__name__}"
            ) from error

    def _build_plan(
        self,
        request: InitializeInterviewRequest,
        enabled_stages: Sequence[InterviewStage],
    ) -> InterviewPlan:
        return InterviewPlan(
            interview_id=request.interview_id,
            duration_seconds=request.duration_seconds,
            stages=self._allocate_stage_budgets(request.duration_seconds, enabled_stages),
            max_questions_per_project=self._settings.max_questions_per_project,
            max_questions_per_topic=self._settings.max_questions_per_topic,
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
        if contract_version not in {"1.0", CONTRACT_VERSION}:
            raise ContractVersionError(
                f"Unsupported contract version {contract_version!r}; expected {CONTRACT_VERSION!r}"
            )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, round((perf_counter() - started_at) * 1000))
