"""Responsibilities: Adapt MVP use cases into a waitable network session that accepts user input.
Implementation: Preserve the existing Agent decision and scoring control while adding request-scoped
progress events and persistence.
Related Modules: agent_socket creates one session; agent_repository persists state; the MVP app
performs parsing, evaluation, and reporting.

Implementation: Optional pre-parsing caches candidate data for this connection only; stage events
wrap real await, with scoring preceding report text transmission.
Related Modules: agent_socket sets request-level emit_event; app provides parsing, evaluation, and
reporting, while Agent maintains decision order.

Declaration Index:
- AgentSession:
  Stores an application composition for one connection, persisting Agent state and responses via
  database repository.
- AgentSession.__init__:
  Constructs a unique MVP application composition for one connection, without starting interview or
  invoking model.
- AgentSession.start:
  Initializes candidate, position, and Agent from a validated Start command, then returns the first
  actionable output.
- AgentSession.prepare:
  Parses or reuses identical resume text within the current connection, returning preview-ready
  profile without consuming question budget.
- AgentSession._stage:
  Wraps real asynchronous stages to send progress updates and record completion, cancellation, and
  failure durations, without modifying timeout or retry behavior.
- AgentSession._emit:
  Sends notifications only to explicitly enabled requests; network failures propagate along original
  exception path.
- AgentSession.answer:
  Converts answer/skip/finish into feedback, records empty speech without competency evidence,
  and returns the next question or explicit early report.
- AgentSession._response:
  Normalizes Agent actions into question/finished format, along with planned revisions, topic
  progress, and decision snapshots, for full output validation.
- AgentSession._response.observe_report:
  Observes whether report model throws error and propagates it unchanged to existing report
  function, clearly marking original summary rollback without adding new rollback.
- AgentSession.close:
  Transfers cleanup requests to model instances supporting close, without proactively clearing state
  still referenced by background calls.

- AgentSession.finish: Evaluate optional current speech and produce a user-ended report without
  another question.

Variable Index:
- logger:
  Logs stage name, interview identifier, duration, and exception type, without recording resume,
  answers, or model content.

Key State Explanations:
AgentSession.interview_id is session identifier; app holds repository and adapter for this session.
history is display cache of submitted answers; action is most recent submitted Agent action.
profile, candidate_name established in prepare; prepared_text used only for exact matching within
this connection.
job, service established in start; emit_event is current request’s async callback or None.
Cache released upon connection closure; database history retained, but this module does not support
reconnecting or re-executing requests.
Parsing failure is not treated as successful reuse of cached data; request layer saves selected
version and input snapshot,
successful profile response and Agent context saved by database repository.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from agents.orchestrator import InterviewAgentService
from app.application import DEFAULT_COMPETENCY_IMPORTANCE, MVPInterviewApplication
from app.parsing.resume import parse_resume_profile
from app.reporting.final_report import build_final_report
from app.settings import interview_settings
from evaluation.assessment import assessed_report_context, update_display_history
from shared.contracts import (
    AnswerAnalysis,
    CandidateAnswer,
    EvaluationFeedback,
    EvaluationRequest,
    InitializeInterviewRequest,
    InterviewActionType,
    InterviewStage,
    JobProfile,
)

from .agent_provider import BackendLLM
from .agent_public_output import public_job_profile, public_question_history
from .agent_repository import DjangoInterviewRepository

logger = logging.getLogger(__name__)


class AgentSession:
    """Stores an application composition for one connection, persisting Agent state and responses
    using a database repository.
    """

    def __init__(self, llm=None):
        """Constructs a unique MVP application composition for one connection, without starting
        interview or invoking model.

        Input: Optional StructuredLLM; test explicitly injects stubs, omitting defaults to read real
        vendor configuration.
        State: Generates interview_id, creates database adapter bound to this ID, sets history to
        empty, action to None.
        Exception: Model configuration or SDK initialization failure is directly propagated,
        returned by protocol layer as configuration error.
        """
        self.interview_id = str(uuid4())
        self.llm = llm if llm is not None else BackendLLM(interview_id=self.interview_id)
        self.app = MVPInterviewApplication(
            self.llm, repository=DjangoInterviewRepository(self.interview_id)
        )
        self.history = []
        self.action = None
        self.prepared_text = None
        self.emit_event = None

    async def _emit(self, data):
        """Input is server-side event dictionary; invokes only current request’s emit_event, returns
        None, and does not swallow send failures.
        """
        if self.emit_event is not None:
            await self.emit_event(data)

    @asynccontextmanager
    async def _stage(self, name):
        """Input is fixed stage name; sends enter/complete events and measures actual duration,
        yielding no business value.

        Reads session ID and current event callback; exceptions and cancellations logged by type and
        propagated unchanged.
        This timing is only for observation, not part of Agent’s logical time budget, nor triggers
        automatic retry.
        """
        started = perf_counter()
        await self._emit({"type": "progress", "stage": name, "state": "running"})
        logger.info("Agent stage started interview=%s stage=%s", self.interview_id, name)
        try:
            yield
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning(
                "Agent stage stopped interview=%s stage=%s duration_ms=%d exception=%s",
                self.interview_id,
                name,
                (perf_counter() - started) * 1000,
                type(exc).__name__,
            )
            raise
        else:
            elapsed = round((perf_counter() - started) * 1000)
            logger.info(
                "Agent stage completed interview=%s stage=%s duration_ms=%d",
                self.interview_id,
                name,
                elapsed,
            )
            await self._emit(
                {"type": "progress", "stage": name, "state": "completed", "duration_ms": elapsed}
            )

    async def prepare(self, command):
        """Input is resume_text from command; parses into shared profile and returns prepared
        preview; does not initialize Agent.

        Reuses only if text is exactly identical within same connection; changing text invalidates
        old cache before calling existing parser.
        Model exceptions preserved in propagation; does not auto-parse every edit in input. Protocol
        layer saves successful response, not raw resume.
        """
        if self.prepared_text != command.resume_text:
            self.prepared_text = None
            async with self._stage("resume_parsing"):
                self.profile, self.candidate_name = await parse_resume_profile(
                    command.resume_text, llm=self.llm, candidate_id=f"candidate-{self.interview_id}"
                )
            self.prepared_text = command.resume_text
        else:
            logger.info("Agent resume reused interview=%s", self.interview_id)
        return {"type": "prepared", "candidate_profile": self.profile.model_dump(mode="json")}

    async def start(self, command):
        """Initializes candidate, job, and Agent from validated Start command, then returns first
        actionable output.

        Precondition: Protocol layer ensures this connection has not started; parameter types and
        ranges already validated by Start.
        Logic: Precisely reuses or parses resume → constructs job → assembles ports → initializes
        plan → normalizes stage transition.
        Budget: Minutes duration converted to real time budget; number of questions only serves as
        safety upper limit, Planner determines goals and allocation.
        Return: question response dict, or finished response dict if Agent ends immediately.
        Side effect: Invokes model and atomically writes to database context; interview shell
        created by protocol layer first, exceptions propagated upward.
        """
        await self.prepare(command)
        self.job = JobProfile(
            job_id=f"job-{self.interview_id}",
            title=command.job_title,
            competency_importance=DEFAULT_COMPETENCY_IMPORTANCE,
        )
        # CLI and web share budget parsing; old follow-up parameters converted only once at this
        # entry point.
        settings = interview_settings(
            max_questions=command.max_questions,
            max_follow_up_per_topic=command.max_follow_up_per_topic,
            max_questions_per_project=command.max_questions_per_project,
            max_questions_per_topic=command.max_questions_per_topic,
        )
        self.service = InterviewAgentService(
            repository=self.app.repository,
            evaluation=self.app.evaluation,
            llm=self.app.agent_llm,
            settings=settings,
            background_replanning=True,
        )
        async with self._stage("question_generation"):
            initialized = await self.service.initialize_interview(
                InitializeInterviewRequest(
                    interview_id=self.interview_id,
                    candidate_profile=self.profile,
                    job_profile=self.job,
                    duration_seconds=command.duration_minutes * 60,
                    planning_enabled=True,
                    enabled_stages=[InterviewStage.PROJECT_DEEP_DIVE],
                )
            )
        self.action = initialized.first_action
        return await self._response()

    async def answer(self, command, *, finishing=False):
        """Converts current answer into standard feedback, drives one Agent decision, and returns
        next question or final report.

        Input: Validated Answer, Skip, or Finish command; finishing selects explicit early finish.
        Skip stores an empty answer with non_answer analysis and no competency dimensions; it
        does not invoke the evaluator or fabricate spoken words. Protocol validates the current
        question ID and request uniqueness.
        Logic: Save pending evaluation answer → extract evidence → atomically submit feedback and
        state → append display cache → generate response.
        Atomic boundary: Database repository commits answer evaluation, state, next action, and logs
        together.
        On submission failure, keep un-scored answer but do not append display cache; protocol layer
        explicitly ends connection, no automatic retry.
        Time semantics: Agent calculates real duration based on persisted first-question start time,
        including input and model waiting.
        Exception: Evaluation, repository, or response generation errors propagated upward; this
        method does not re-send answer or retry entire round.
        """
        question = self.action.question
        answer = CandidateAnswer(
            interview_id=self.interview_id,
            question_id=question.question_id,
            answer_id=str(uuid4()),
            text=command.answer_text,
        )
        await self.app.repository.accept_answer(command.request_id, answer)
        async with self._stage("answer_evaluation"):
            feedback = (
                EvaluationFeedback(
                    request_id=str(command.request_id),
                    question_id=question.question_id,
                    answer_relevance=0,
                    evidence_strength=0,
                    analysis=AnswerAnalysis(
                        status="non_answer",
                        answer_scope="none",
                        thread_complete=True,
                        summary="No speech was recognized before automatic closure.",
                    ),
                )
                if command.type == "skip"
                else await self.app.evaluation.evaluate(
                    EvaluationRequest(
                        request_id=str(command.request_id),
                        interview_id=self.interview_id,
                        question=question,
                        answer=answer,
                    )
                )
            )
        history_entry = {
            "question_id": question.question_id,
            "dialogue_action": question.dialogue_action,
            "parent_question_id": question.parent_question_id,
            "thread_id": question.thread_id,
            "project_id": question.project_id,
            "topic": question.topic,
            "difficulty": question.difficulty,
            "question": question.text,
            "answer": answer.text,
            "evaluation": feedback.model_dump(
                mode="json",
                include={
                    "answer_relevance",
                    "evidence_strength",
                    "analysis",
                    "dimensions",
                    "evidence_ids",
                    "analysis_status",
                    "assessment_status",
                },
            ),
        }
        self.app.repository.pending_feedback = feedback
        async with self._stage("next_action"):
            advance = (
                self.service.finish_interview
                if finishing
                else self.service.apply_evaluation_feedback
            )
            self.action = await advance(
                self.interview_id,
                feedback=feedback,
                answer=answer if command.type != "skip" else None,
            )
        self.history.append(history_entry)
        if hasattr(self.app.evaluation, "start_background"):
            self.app.evaluation.start_background(self.interview_id)
        return await self._response()

    async def finish(self, command):
        """Functionality: Evaluate the optional current transcript and generate an early report.
        Inputs: Validated Finish with optional current question ID and nonempty answer text.
        Outputs: The existing finished envelope, approved/persisted by the socket gateway.
        Logic: A supplied answer uses the regular evaluator and an atomic finish commit; without
        an answer only previously committed evidence contributes. No next question is generated.
        Constraints: Empty/unfinished speech is not scored; model and persistence errors propagate.
        """
        logger.info(
            "Agent early finish interview=%s include_current_answer=%s",
            self.interview_id,
            bool(command.answer_text),
        )
        if command.answer_text:
            return await self.answer(command, finishing=True)
        self.action = await self.service.finish_interview(self.interview_id)
        return await self._response()

    async def _response(self):
        """Delivers Agent action and progress snapshot to full package output check.

        Logic: Reuses MVP stage advancement method, reads committed state; question branches attach
        latest evaluation, end branches build report.
        Return: question contains problem, status, evaluation, planned revision, topic progress, and
        decision log;
        finished.result contains candidate-facing feedback and progress; internal job weights,
        planning criteria, decision traces and evaluator control flags are excluded before the
        complete public response is inspected and persisted.
        Dependency: Calls internal stage helper methods of MVP; interface upgrade requires
        synchronized verification of this adapter and consistency tests.
        Exception: No text problem or unexpected action raises RuntimeError; other errors preserved
        in original propagation.
        Report generator uses MVP’s scoring and fallback text logic; when events enabled, send
        assessment with only deterministic values first,
        then generate report text; assessment does not indicate report text success or session
        recoverability.
        Report observer only records original rollback state, without modifying prompt, schema,
        values, or exception capture boundaries.
        """
        self.action = await self.app._advance_non_question_actions(
            self.service, self.interview_id, self.action
        )
        context = await self.app.repository.get_interview_context(self.interview_id)
        if self.action.type == InterviewActionType.ASK_QUESTION:
            if self.action.question is None or not self.action.question.text:
                raise RuntimeError("Agent returned a question without text.")
            details = self.action.decision_trace.details
            logger.info(
                "Agent question ready interview=%s question_index=%d generation_reason=%s "
                "generation_ms=%s planner_ms=%s retrieval_ms=%s",
                self.interview_id,
                context.state.question_index,
                details.get("generation_reason"),
                details.get("generation_latency_ms"),
                details.get("planner_latency_ms"),
                details.get("retrieval_latency_ms"),
            )
            return {
                "type": "question",
                "interview_id": self.interview_id,
                "question": self.action.question.model_dump(mode="json"),
                "question_index": context.state.question_index,
                "interview_state": context.state.model_dump(mode="json"),
                "interview_plan": context.plan.model_dump(mode="json"),
                "last_evaluation": self.history[-1]["evaluation"] if self.history else None,
                "plan_history": [item.model_dump(mode="json") for item in context.plan_history],
                "topic_progress": {
                    key: value.model_dump(mode="json")
                    for key, value in context.topic_progress.items()
                },
                "decision_logs": [
                    log.model_dump(mode="json")
                    for log in await self.app.repository.decision_logs_for(self.interview_id)
                ],
            }
        if self.action.type != InterviewActionType.FINISH:
            raise RuntimeError("Agent returned an unexpected action.")
        if hasattr(self.app.evaluation, "drain"):
            await self.app.evaluation.drain(self.interview_id)
        if context.assessment_feedback_ids:
            context = await assessed_report_context(self.app.repository, context)
            update_display_history(self.history, context)
        if self.emit_event is not None:
            numeric = await build_final_report(context, self.history, llm=None)
            await self._emit(
                {
                    "type": "assessment",
                    "assessment": numeric.model_dump(
                        mode="json", include={"overall_score", "competencies"}
                    ),
                }
            )
        narrative_status = "completed"

        def observe_report(prompt, data, schema):
            """Input is original model parameters for report function, returns original model
            result; throws error with fallback marker and rethrows unchanged.

            Catches Exception and shares same fallback boundary as existing build_final_report; call
            executed in worker thread,
            marker written before return; after cancellation, no event sent or additional model call
            appended, no exception body recorded.
            """
            nonlocal narrative_status
            try:
                return self.llm(prompt, data, schema)
            except Exception as exc:
                narrative_status = "fallback"
                logger.warning(
                    "Agent report narrative failed interview=%s exception=%s; "
                    "existing deterministic summary will be used",
                    self.interview_id,
                    type(exc).__name__,
                )
                raise

        async with self._stage("report_generation"):
            report = await build_final_report(context, self.history, llm=observe_report)
        return {
            "type": "finished",
            "result": {
                "interview_id": self.interview_id,
                "candidate_name": self.candidate_name,
                "candidate_profile": self.profile.model_dump(mode="json"),
                "job_profile": public_job_profile(self.job),
                "question_history": public_question_history(self.history),
                "interview_state": context.state.model_dump(
                    mode="json",
                    include={
                        "contract_version",
                        "interview_id",
                        "state_version",
                        "status",
                        "stage",
                        "question_index",
                        "remaining_seconds",
                        "elapsed_seconds",
                    },
                ),
                "interview_finished": context.state.status == "finished",
                "final_report": report.model_dump(mode="json"),
                "report_narrative_status": narrative_status,
            },
        }

    async def close_background(self):
        if getattr(self, "service", None) is not None:
            await self.service.close_background()
        if hasattr(self.app.evaluation, "close"):
            await self.app.evaluation.close()

    def close(self):
        """Transfers cleanup request to model instances supporting close, without proactively
        clearing state still referenced by background calls.

        Precondition: Protocol layer canceled and waited for local async tasks; synchronous SDK
        requests may still be in worker thread.
        BackendLLM responsible for delayed release of in-flight clients; test stubs can just record
        close flag.
        Returns None; session object loses reference upon connection task exit; close exception
        preserved in upward propagation.
        """
        if hasattr(self.llm, "close"):
            self.llm.close()
