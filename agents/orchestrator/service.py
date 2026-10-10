"""Responsibilities: Own versioned interview planning, question decisions, feedback and termination.
Implementation: Load context for each turn, validate supplied contracts, apply bounded policies and
commit context/action/evidence atomically through RepositoryPort. Explicit user finish skips
planning and new question generation, preserving the same evidence and persistence boundaries.
Related Modules: agents.planning/question/policies implement decisions; ports abstract persistence,
RAG and model calls; app and backend AgentSession adapt evaluation and report presentation.
Declaration Index:
- InterviewAgentService: Versioned Agent orchestration service shared by CLI and web adapters.
- InterviewAgentService.__init__: Compose reusable policies and injected ports.
- InterviewAgentService.initialize_interview: Initialize a new interview and obtain its first
  action.
- InterviewAgentService.next_action: Advance the interview using its committed context.
- InterviewAgentService.apply_evaluation_feedback: Consume evaluated current-question feedback once.
- InterviewAgentService.replay_decision: Recompute deterministic policy fields for comparison.
- InterviewAgentService.finish_interview: Commit an explicit user finish with optional current
  feedback.
- InterviewAgentService._run_turn: Load, decide and atomically commit one interview turn.
- InterviewAgentService._context_after_feedback: Construct the updated conversation/evidence
  snapshot.
- InterviewAgentService._decide_and_commit: Choose termination, stage transition or another
  question.
- InterviewAgentService._ask_question: Admit and generate the next constrained question.
- InterviewAgentService._change_stage: Commit an explicit stage transition.
- InterviewAgentService._finish: Commit terminal state and close unfinished agenda items.
- InterviewAgentService._commit_turn: Publish one versioned context/action/question transaction.
- InterviewAgentService._decision_log: Build diagnostic metadata for the selected action.
- InterviewAgentService._generate_question: Generate and validate question text using the configured
  pipeline.
- InterviewAgentService._generation_prompt_name: Expose the configured question prompt name.
- InterviewAgentService._get_previous_questions: Load the bounded recent question snapshots.
- InterviewAgentService._get_context: Load this interview context through its repository port.
- InterviewAgentService._get_processed_action: Resolve feedback idempotency from persisted state.
- InterviewAgentService._repository_call: Bound and annotate repository failures.
- InterviewAgentService._build_plan: Construct the initial configured interview plan.
- InterviewAgentService._sync_clock: Update actual elapsed/remaining time and active-topic duration.
- InterviewAgentService._normalize_stages: Normalize explicitly enabled stages.
- InterviewAgentService._allocate_stage_budgets: Allocate integer seconds evenly across enabled
  stages.
- InterviewAgentService._validate_contract_version: Validate the shared contract version.
- InterviewAgentService._elapsed_ms: Measure nonnegative operation latency.
Variable Index:
- ResultT: Generic result type used by the bounded repository-call wrapper.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from time import perf_counter, time
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
from agents.orchestrator.replay import replay_decision as replay_policy_decision
from agents.orchestrator.state_machine import InterviewStageMachine
from agents.orchestrator.termination import TerminationPolicy
from agents.planning import InterviewPlannerAgent
from agents.planning.pace import round_cost
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
from evaluation.integration import apply_evaluation
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

    The object owns policies, adapters and optional speculative planning tasks.
    Canonical state is loaded from and committed through RepositoryPort;
    background snapshots propose preferences but never publish state.
    """

    def __init__(
        self,
        *,
        repository: InterviewRepositoryPort,
        rag: RAGPort | None = None,
        evaluation: EvaluationPort | None = None,
        llm: LLMPort | None = None,
        settings: AgentSettings | None = None,
        clock=time,
        background_replanning: bool = False,
    ) -> None:
        """Functionality: Compose reusable policies and injected ports.
        Inputs: repository, rag, evaluation, llm, settings, clock.
        Outputs: None; instance policy/port references are initialized.
        Logic: Store repository, optional RAG/evaluation/LLM, settings (loaded if omitted) and clock
        (wall time by default); create enabled planners and generators without initializing an
        interview.
        Constraints: No candidate state is stored on the service; each turn loads repository
        context.
        """
        self._repository = repository
        self._rag = rag
        self._evaluation = evaluation
        self._llm = llm
        self._settings = settings or load_agent_settings()
        self._clock = clock
        self._interview_planner = InterviewPlannerAgent(llm, self._settings, self._sync_clock)
        from agents.planning.background import BackgroundReplanner

        self._background_replanner = (
            BackgroundReplanner(self._interview_planner) if background_replanning else None
        )
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
        """Functionality: Initialize a new interview and obtain its first action.
        Inputs: request.
        Outputs: InitializeInterviewResponse with committed first action and latest state/plan.
        Logic: Validate contract/stage/time constraints, copy profiles, create plan and empty
        competency state, optionally revise initial plan, persist then run the first turn.
        Constraints: Time planning accepts only project deep dive and at least 60 seconds;
        repository/model failures propagate.
        """
        self._validate_contract_version(request.contract_version)
        self._validate_contract_version(request.candidate_profile.contract_version)
        self._validate_contract_version(request.job_profile.contract_version)

        enabled_stages = self._normalize_stages(request.enabled_stages)
        if request.planning_enabled and (
            enabled_stages != [InterviewStage.PROJECT_DEEP_DIVE] or request.duration_seconds < 60
        ):
            raise InvalidAgentState(
                "Time planning requires project_deep_dive and at least 60 seconds"
            )
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
        if request.planning_enabled:
            await self._interview_planner.revise(context, "INITIAL_PLAN")
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
        """Functionality: Advance the interview using its committed context.
        Inputs: interview_id.
        Outputs: Committed InterviewAction.
        Logic: Delegate to the versioned turn loop without new feedback.
        Constraints: Uses normal plan/stage/termination policies and existing conflict bounds.
        """
        return await self._run_turn(interview_id)

    async def apply_evaluation_feedback(
        self,
        interview_id: str,
        feedback: EvaluationFeedback,
        *,
        elapsed_seconds: int = 0,
        answer: CandidateAnswer | None = None,
    ) -> InterviewAction:
        """Functionality: Consume evaluated current-question feedback once.
        Inputs: interview_id, feedback, elapsed_seconds, answer.
        Outputs: Committed InterviewAction, including an existing action for duplicate feedback.
        Logic: Reject negative elapsed time and mismatched/empty supplied answers, validate
        versions, return an already processed action or enter the turn loop.
        Constraints: Answer may be None for explicit unobserved/non-answer feedback; capability
        evidence must remain grounded.
        """
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

    async def finish_interview(
        self,
        interview_id: str,
        *,
        feedback: EvaluationFeedback | None = None,
        answer: CandidateAnswer | None = None,
    ) -> InterviewAction:
        """Functionality: End on the user's explicit request without planning another question.
        Inputs: Interview ID and optional evaluated current answer/feedback.
        Outputs: Atomically committed FINISH action, with USER_FINISHED decision reason.
        Logic: Validate supplied contracts and question identity, then use the normal versioned
        turn transaction; feedback and final state share the same commit.
        Constraints: No model-generated question or automatic change to scoring criteria.
        Repository conflicts retain the existing bounded recomputation policy.
        """
        if feedback is not None:
            self._validate_contract_version(feedback.contract_version)
        if answer is not None:
            self._validate_contract_version(answer.contract_version)
            if (
                feedback is None
                or answer.interview_id != interview_id
                or answer.question_id != feedback.question_id
                or not answer.text.strip()
            ):
                raise InvalidAgentState("Final answer must match current feedback and be nonempty")
        return await self._run_turn(
            interview_id, feedback=feedback, answer=answer, finish_reason="USER_FINISHED"
        )

    async def _run_turn(
        self,
        interview_id: str,
        *,
        feedback: EvaluationFeedback | None = None,
        elapsed_seconds: int = 0,
        answer: CandidateAnswer | None = None,
        finish_reason: str | None = None,
    ) -> InterviewAction:
        """Functionality: Load, decide and atomically commit one interview turn.
        Inputs: interview_id, feedback, elapsed_seconds, answer, finish_reason.
        Outputs: Committed InterviewAction.
        Logic: Check feedback idempotency, reload context, apply optional feedback; finish_reason
        bypasses planning and question generation; otherwise use normal policies. Recompute only on
        StateConflictError.
        Constraints: Only configured conflict recomputations are permitted; other exceptions
        propagate and no vendor call is silently retried here.
        """
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
            context = await self._context_after_assessments(context)
            try:
                if finish_reason is not None:
                    self._sync_clock(context)
                    return await self._finish(
                        context,
                        feedback_request_id=feedback.request_id if feedback is not None else None,
                        started_at=attempt_started,
                        reason=finish_reason,
                    )
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

    async def _context_after_assessments(self, context):
        if not context.assessment_feedback_ids:
            return context
        from evaluation.assessment import project_assessments

        try:
            jobs = await self._repository_call(
                self._repository.get_shadow_jobs(context.interview_id),
                operation="load assessment sources",
            )
            records = await self._repository_call(
                self._repository.get_assessment_records(context.interview_id),
                operation="load completed assessments",
            )
            updated, applied = project_assessments(context, jobs, records)
            for question, feedback in applied:
                if feedback.dimensions and not any(
                    evidence.status == "active" and evidence.question_id == question.question_id
                    for evidence in updated.evidence_records
                ):
                    continue
                if (
                    updated.active_thread is not None
                    and (question.thread_id or question.question_id)
                    == updated.active_thread.thread_id
                    and feedback.analysis_status == "valid"
                    and feedback.assessment_status == "valid"
                ):
                    updated.thread_difficulty = self._difficulty_controller.adjust(
                        question.difficulty, feedback
                    )
            return updated
        except Exception as error:
            # Scores are optional for progression; storage/model failure is not low ability.
            emit_trace(
                "evaluation.assessment_refresh_unavailable",
                interview_id=context.interview_id,
                error_type=type(error).__name__,
            )
            return context

    async def _context_after_feedback(
        self,
        context: InterviewContext,
        feedback: EvaluationFeedback,
        *,
        elapsed_seconds: int,
        answer: CandidateAnswer | None = None,
    ) -> InterviewContext:
        """Functionality: Construct the updated conversation/evidence snapshot.
        Inputs: context, feedback, elapsed_seconds, answer.
        Outputs: New InterviewContext; original context is preserved.
        Logic: Require the current asked question, copy context, append bounded history, update
        clock, difficulty, grounded evidence, thread information and planner feedback statistics.
        Constraints: Dimensions require actual answer quotes; no capability evidence is invented for
        absent answers.
        """
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
                feedback=EvaluationFeedback.model_validate(feedback.model_dump()),
            )
        )
        updated.question_history = updated.question_history[
            -self._settings.question_agent.history_retention :
        ]
        if updated.plan.planning_enabled:
            self._sync_clock(updated)
        else:
            updated.state.elapsed_seconds += elapsed_seconds
            updated.state.remaining_seconds = max(
                0,
                updated.state.remaining_seconds - elapsed_seconds,
            )
        if feedback.analysis_status == "valid" and feedback.assessment_status == "valid":
            updated.thread_difficulty = self._difficulty_controller.adjust(
                current_question.difficulty, feedback
            )
        apply_evaluation(updated, current_question, answer, feedback)
        if answer is not None and feedback.assessment_status != "valid":
            updated.unassessed_answer_ids = list(
                dict.fromkeys([*updated.unassessed_answer_ids, answer.answer_id])
            )
        if updated.active_thread is not None and feedback.analysis_status == "valid":
            updated.active_thread.no_information_count = (
                0
                if feedback.analysis.new_information
                else updated.active_thread.no_information_count + 1
            )
        if updated.plan.planning_enabled:
            self._interview_planner.feedback(
                updated,
                current_question,
                feedback,
                updated.state.elapsed_seconds - context.state.elapsed_seconds,
            )
        if feedback.assessment_status == "valid":
            updated.state.evidence_ids = list(
                dict.fromkeys([*updated.state.evidence_ids, *feedback.evidence_ids])
            )
        feedback_limit = self._settings.context.recent_feedback
        feedback_history = [
            *updated.recent_feedback,
            EvaluationFeedback.model_validate(feedback.model_dump()),
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
        """Functionality: Choose termination, stage transition or another question.
        Inputs: context, feedback_request_id, started_at.
        Outputs: Committed InterviewAction.
        Logic: Synchronize time, review enabled plans if not already terminal, check finish/stage
        policies, delegate to the appropriate committing branch.
        Constraints: Policy order is preserved; callbacks may consume real elapsed time and
        repository failures propagate.
        """
        self._sync_clock(context)
        if context.coverage_question_criteria:
            return await self._finish(
                context,
                feedback_request_id=feedback_request_id,
                started_at=started_at,
                reason="COVERAGE_REVIEW",
            )
        if context.plan.planning_enabled and not self._termination_policy.should_finish(
            context.state, context.plan
        ):
            if self._background_replanner:
                self._background_replanner.consume(context)
            await self._interview_planner.review(
                context,
                defer=self._background_replanner.request if self._background_replanner else None,
            )
            self._sync_clock(context)
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
        """Functionality: Admit and generate the next constrained question.
        Inputs: context, feedback_request_id, started_at.
        Outputs: ASK_QUESTION action or FINISH when no topic/time remains.
        Logic: Select dialogue/project/topic, enforce available time and topic limits,
        plan/retrieve/generate/validate, recheck elapsed time, update counters/thread/progress and
        commit question plus audit.
        Constraints: Preserves existing retrieval/generation fallbacks and configured budgets;
        counters advance only with the committed question.
        """
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
            insufficient_time = (
                context.plan.planning_enabled
                and state.clock_started_at is not None
                and state.remaining_seconds - context.plan.closing_seconds
                < round_cost(context, self._settings)
            )
            return await self._finish(
                context,
                feedback_request_id=feedback_request_id,
                started_at=started_at,
                reason="INSUFFICIENT_TIME_FOR_NEW_TOPIC" if insufficient_time else "NO_MORE_TOPICS",
            )
        if (
            context.plan.planning_enabled
            and not probe.should_probe
            and state.clock_started_at is not None
            and state.remaining_seconds - context.plan.closing_seconds
            < round_cost(context, self._settings)
        ):
            return await self._finish(
                context,
                feedback_request_id=feedback_request_id,
                started_at=started_at,
                reason="INSUFFICIENT_TIME_FOR_NEW_TOPIC",
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
        agenda_item = next(
            (item for item in context.plan.topics if item.topic_key == topic.topic_key), None
        )
        if agenda_item and not probe.should_probe:
            question_plan.intent = question_plan.information_goal = agenda_item.objective
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
        timed_context = context.model_copy(deep=True)
        self._sync_clock(timed_context)
        if question is None:
            emit_trace(
                "question.failed",
                question_id=question_plan.question_id,
                reason=generation_reason,
                stop_reason=agent_result.stop_reason,
                blocking_issues=agent_result.blocking_issues,
            )
            return await self._finish(
                timed_context,
                feedback_request_id=feedback_request_id,
                started_at=started_at,
                reason="NO_VALID_QUESTION",
            )
        if self._termination_policy.should_finish(timed_context.state, timed_context.plan):
            return await self._finish(
                timed_context,
                feedback_request_id=feedback_request_id,
                started_at=started_at,
            )
        if agent_result.question is not None or agent_result.locked_intent is not None:
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
        updated.last_question_generation_seconds = generation_latency_ms / 1000
        if updated.plan.planning_enabled and updated.state.clock_started_at is None:
            # Initial resume parsing, planning and first question generation are preparation.
            updated.state.clock_started_at = self._clock()
        DialogueController(updated, self._settings).record_question(
            question, closed_reason=probe.reason_code
        )
        # Topic allocations are checked at admission. Charge generation to the
        # selected topic after recording it; crossing a soft allocation during
        # generation must not invalidate an already admitted question.
        self._sync_clock(updated)
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
        """Functionality: Commit an explicit stage transition.
        Inputs: context, feedback_request_id, started_at.
        Outputs: CHANGE_STAGE action.
        Logic: Copy state/context, select next enabled stage and close the active thread/reset
        consecutive probes, create transition action/log and commit without a question.
        Constraints: No candidate answer is evaluated in this branch; expected state version
        protects persistence.
        """
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
        """Functionality: Commit terminal state and close unfinished agenda items.
        Inputs: context, feedback_request_id, started_at, reason.
        Outputs: FINISH action.
        Logic: Copy context, mark pending/active progress skipped, close active thread, set finished
        stage/status, build FINISH action/log and atomically commit optional feedback.
        Constraints: Explicit reason overrides normal termination reason; no final-report model is
        invoked in this core method.
        """
        if reason in {"NO_MORE_TOPICS", "NO_VALID_QUESTION", "COVERAGE_REVIEW"}:
            supplement = await self._coverage_question(context, feedback_request_id, started_at)
            if supplement is not None:
                return supplement
        state = context.state
        updated_context = context.model_copy(deep=True)
        if context.plan.planning_enabled:
            for progress in updated_context.topic_progress.values():
                if progress.status in {"active", "pending", "deferred"}:
                    progress.status = "skipped"
                    progress.reason = reason or self._termination_policy.reason_code(
                        state, context.plan
                    )
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
                reason_code=reason or self._termination_policy.reason_code(state, context.plan)
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

    async def _coverage_question(self, context, feedback_request_id, started_at):
        """Use saved formal gaps before a soft finish; respect user finish and every hard budget."""
        if getattr(self._evaluation, "formal", None) is None:
            return None
        self._sync_clock(context)
        if self._termination_policy.should_finish(context.state, context.plan):
            return None
        if context.state.remaining_seconds - context.plan.closing_seconds < round_cost(
            context, self._settings
        ):
            return None
        from agents.policies.score_coverage import coverage_question
        from evaluation.publication import latest_scoring_record, scoreable_aggregation

        records = await self._repository_call(
            self._repository.get_evaluation_records(context.interview_id),
            operation="read scoring coverage",
        )
        aggregation = scoreable_aggregation(latest_scoring_record(records))
        target = coverage_question(context, aggregation, self._settings)
        if target is None:
            return None
        self._sync_clock(context)
        if self._termination_policy.should_finish(context.state, context.plan):
            return None
        key, question = target
        updated = context.model_copy(deep=True)
        DialogueController(updated, self._settings).record_question(
            question, closed_reason="SCORING_COVERAGE"
        )
        updated.coverage_question_criteria.append(key)
        updated.state.question_index += 1
        updated.state.current_question_id = question.question_id
        updated.state.asked_question_ids.append(question.question_id)
        updated.state.last_question_type = question.question_type
        updated.state.last_topic = question.topic
        updated.state.active_project_id = question.project_id
        updated.state.consecutive_probes = 0
        action = InterviewAction(
            action_id=str(uuid4()),
            interview_id=context.interview_id,
            type=InterviewActionType.ASK_QUESTION,
            question=question,
            decision_trace=DecisionTrace(
                reason_code="SCORING_COVERAGE", details={"generation_reason": "COVERAGE_TEMPLATE"}
            ),
        )
        log = self._decision_log(
            action,
            context,
            difficulty=question.difficulty,
            probe_depth=1,
            topic=question.topic,
            question_type=question.question_type,
            total_agent_latency_ms=self._elapsed_ms(started_at),
        )
        return await self._commit_turn(
            original_context=context,
            updated_context=updated,
            action=action,
            question=question,
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
        """Functionality: Publish one versioned context/action/question transaction.
        Inputs: original_context, updated_context, action, question, decision_log,
        feedback_request_id.
        Outputs: Repository-returned action.
        Logic: Construct CommitTurnRequest using original state version, await the repository
        adapter, require committed=True and trace the resulting state/action.
        Constraints: Context, optional question, feedback identity and log share the repository
        transaction; uncommitted outcomes raise RepositoryUnavailable.
        """
        request = CommitTurnRequest(
            interview_id=original_context.interview_id,
            expected_state_version=original_context.state.state_version,
            new_state=updated_context.state,
            new_context=updated_context,
            evaluation_record=updated_context.pending_evaluation,
            shadow_job=updated_context.pending_shadow_job,
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
        if self._background_replanner and result.action.type == InterviewActionType.ASK_QUESTION:
            committed_snapshot = updated_context.model_copy(deep=True)
            committed_snapshot.state = result.state.model_copy(deep=True)
            self._background_replanner.start(committed_snapshot)
        return result.action

    async def close_background(self):
        if self._background_replanner:
            await self._background_replanner.close()

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
        """Functionality: Build diagnostic metadata for the selected action.
        Inputs: action, context, difficulty, probe_depth, topic, question_type, rag_sources,
        failed_sources, timeout_sources, fallback_used, planner_latency_ms, retrieval_latency_ms,
        generation_latency_ms, total_agent_latency_ms, decision_reasons, question_agent_result.
        Outputs: AgentDecisionLog.
        Logic: Collect supplied selection/timing/retrieval/reason values with current context,
        planned progress and optional ReAct result; bind the next state version.
        Constraints: No repository writes; defaults describe absent question/retrieval fields rather
        than fabricated model reasoning.
        """
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
    ) -> tuple[PlannedQuestion | None, str]:
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
            agent_result.locked_intent = result.locked_intent
            agent_result.retarget_requested = result.retarget_requested
            agent_result.blocking_issues = result.blocking_issues
            agent_result.repairs_used = result.repairs_used
            agent_result.model_calls = result.model_calls
            agent_result.elapsed_seconds = result.elapsed_seconds
            if result.question is not None:
                return result.question, "LLM_REPAIRED" if result.repaired else "LLM_GENERATED"
            if result.retarget_requested:
                emit_trace(
                    "controller.retarget",
                    approved=False,
                    intent_id=result.locked_intent.intent_id if result.locked_intent else None,
                    reason="KEEP_INTENT_WITHIN_EXISTING_TURN_BUDGET",
                )
            if result.locked_intent is not None:
                if set(result.blocking_issues) & {"SEMANTIC_REPEAT", "UNSUPPORTED_PREMISE"}:
                    return None, "UNSAFE_INTENT_FALLBACK_BLOCKED"
                locked = result.locked_intent
                selected_project = next(
                    (
                        p
                        for p in interview.candidate_profile.projects
                        if p.project_id == locked.project_id
                    ),
                    None,
                )
                fallback = self._fallback_policy.apply_locked(locked, project=selected_project)
                if self._question_validator.is_valid(fallback, locked):
                    return fallback, "SAME_INTENT_FALLBACK_UNREVIEWED"
                return None, "INVALID_INTENT_FALLBACK"
            # No model route was accepted: preserve the controller's original target.
            # The old recovery silently replaced it with a repeated ownership question.
            fallback = self._fallback_policy.apply_locked(question_plan, project=project)
            if self._question_validator.is_valid(fallback, question_plan):
                return fallback, "POLICY_INTENT_FALLBACK_UNREVIEWED"
            return None, "INVALID_POLICY_FALLBACK"
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

        question_plan = self._fallback_policy.prepare_plan(question_plan, interview)
        candidate_fallback = self._fallback_policy.apply(question_plan, project=project)
        if self._question_validator.is_valid(candidate_fallback, question_plan):
            return candidate_fallback, "CANDIDATE_SPECIFIC_FALLBACK"
        generic_fallback = self._fallback_policy.apply(question_plan, project=project, generic=True)
        return generic_fallback, "GENERIC_ANCHOR_FALLBACK"

    @property
    def _generation_prompt_name(self) -> str:
        """Functionality: Expose the configured question prompt name.
        Inputs: Instance configuration only..
        Outputs: Prompt-name string.
        Logic: Select ReactQuestionAgent prompt when its instance exists, otherwise
        QuestionGenerator prompt.
        Constraints: Read-only property; no model call.
        """
        return (
            ReactQuestionAgent.prompt_name
            if self._question_agent is not None
            else QuestionGenerator.prompt_name
        )

    async def _get_previous_questions(
        self,
        state: InterviewState,
    ) -> list[PlannedQuestion]:
        """Functionality: Load the bounded recent question snapshots.
        Inputs: state.
        Outputs: List of PlannedQuestion in ID order.
        Logic: Take recent_questions IDs from state; gather repository reads or return an empty list
        when retention is zero.
        Constraints: Each read uses the existing repository timeout and error translation.
        """
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
        """Functionality: Load this interview context through its repository port.
        Inputs: interview_id.
        Outputs: InterviewContext.
        Logic: Await get_interview_context through the bounded repository wrapper.
        Constraints: No local cache or memory fallback; failures propagate as
        AgentError/RepositoryUnavailable.
        """
        return await self._repository_call(
            self._repository.get_interview_context(interview_id),
            operation="load interview context",
        )

    async def _get_processed_action(
        self,
        interview_id: str,
        feedback_request_id: str,
    ) -> InterviewAction | None:
        """Functionality: Resolve feedback idempotency from persisted state.
        Inputs: interview_id, feedback_request_id.
        Outputs: InterviewAction or None.
        Logic: Read the previously committed feedback action through the repository wrapper.
        Constraints: No decision is recomputed when an existing action is returned.
        """
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
        """Functionality: Bound and annotate repository failures.
        Inputs: awaitable, operation.
        Outputs: The awaited repository result, generic ResultT.
        Logic: Await the supplied operation under repository_seconds; preserve AgentError and wrap
        other exception types with operation context.
        Constraints: No transaction or retry is introduced; exception messages contain
        operation/type rather than input bodies.
        """
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
        """Functionality: Construct the initial configured interview plan.
        Inputs: request, enabled_stages.
        Outputs: InterviewPlan.
        Logic: Allocate stage budgets from duration and copy safety question limits/planning flag
        from validated request and settings.
        Constraints: No model or persistence call; the caller validates stage/time constraints.
        """
        return InterviewPlan(
            interview_id=request.interview_id,
            duration_seconds=request.duration_seconds,
            stages=self._allocate_stage_budgets(request.duration_seconds, enabled_stages),
            max_questions_per_project=self._settings.max_questions_per_project,
            max_questions_per_topic=self._settings.max_questions_per_topic,
            max_questions=self._settings.max_questions,
            planning_enabled=request.planning_enabled,
        )

    def _sync_clock(self, context):
        """Functionality: Update actual elapsed/remaining time and active-topic duration.
        Inputs: context.
        Outputs: None; context state/progress are mutated in memory.
        Logic: When planning is active and first question has started, use nondecreasing wall-clock
        elapsed seconds and increment active-topic time by the elapsed delta.
        Constraints: Finished/unstarted/nonplanning context is unchanged; remaining time is clamped
        to zero.
        """
        if (
            not context.plan.planning_enabled
            or context.state.clock_started_at is None
            or context.state.status == "finished"
        ):
            return
        state = context.state
        previous_elapsed = state.elapsed_seconds
        state.elapsed_seconds = max(
            state.elapsed_seconds,
            int(self._clock() - state.clock_started_at),
        )
        state.remaining_seconds = max(0, context.plan.duration_seconds - state.elapsed_seconds)
        active = context.active_thread
        progress = context.topic_progress.get(active.topic_key) if active else None
        if progress is not None and progress.status == "active":
            progress.elapsed_seconds += state.elapsed_seconds - previous_elapsed

    @staticmethod
    def _normalize_stages(stages: Sequence[InterviewStage]) -> list[InterviewStage]:
        """Functionality: Normalize explicitly enabled stages.
        Inputs: stages.
        Outputs: List of enabled nonfinished InterviewStage values.
        Logic: Remove FINISHED, preserve first occurrence order and reject an empty result.
        Constraints: No implicit stage is inserted.
        """
        enabled = list(dict.fromkeys(stage for stage in stages if stage != InterviewStage.FINISHED))
        if not enabled:
            raise InvalidAgentState("At least one non-finished interview stage must be enabled")
        return enabled

    @staticmethod
    def _allocate_stage_budgets(
        duration_seconds: int,
        stages: Sequence[InterviewStage],
    ) -> list[StagePlan]:
        """Functionality: Allocate integer seconds evenly across enabled stages.
        Inputs: duration_seconds, stages.
        Outputs: List of StagePlan whose budgets sum to duration_seconds.
        Logic: Use quotient/remainder division; earlier stages receive the residual second.
        Constraints: Caller supplies a nonempty validated stage sequence and sufficient positive
        duration.
        """
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
        """Functionality: Validate the shared contract version.
        Inputs: contract_version.
        Outputs: None on success; ContractVersionError otherwise.
        Logic: Accept legacy 1.0 or current CONTRACT_VERSION; reject other strings.
        Constraints: Does not mutate or migrate input contracts.
        """
        if contract_version not in {"1.0", CONTRACT_VERSION}:
            raise ContractVersionError(
                f"Unsupported contract version {contract_version!r}; expected {CONTRACT_VERSION!r}"
            )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        """Functionality: Measure nonnegative operation latency.
        Inputs: started_at.
        Outputs: Integer milliseconds.
        Logic: Subtract started_at from perf_counter, convert to rounded milliseconds and clamp at
        zero.
        Constraints: Presentation/diagnostic timing only; does not alter interview budgets.
        """
        return max(0, round((perf_counter() - started_at) * 1000))
