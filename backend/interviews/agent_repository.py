"""Responsibilities: Provide a database adapter binding single interview for existing Agent v1.1
repository interface.

Implementation: Uses version-conditioned UPDATE to preempt single-round commits, followed by atomic
writes of questions, feedback, actions, scoring receipts and logs within the same transaction.
Related Modules: AgentSession injects this adapter; does not modify Agent policy, scoring,
questions, or logical timing parameters.

Declaration Index:
- DjangoInterviewRepository: Binds interview ID, prohibits cross-interview queries or writes.
- DjangoInterviewRepository.__init__: Only stores scope and pending feedback, does not open database
  or model connection.
- DjangoInterviewRepository._scope: Validates incoming interview identifier matches adapter scope.
- DjangoInterviewRepository._evaluation_records: Validate scoring payload identities in turn order.
- DjangoInterviewRepository.get_evaluation_records: Read the scoped internal scoring ledger.
- DjangoInterviewRepository.get_interview_context: Reads and validates latest context and
  relationship version consistency.
- DjangoInterviewRepository.initialize_interview: Upgrades preparation state to initialized context,
  version increases only once.
- DjangoInterviewRepository.accept_answer: Saves answer before evaluation, validates request,
  current question, and session ownership.
- DjangoInterviewRepository.commit_turn: Atomically publishes new version, question, feedback
  evidence, validated scoring receipt and audit log.
- DjangoInterviewRepository.get_processed_feedback_action: Queries only submitted feedback actions
  within the same interview.
- DjangoInterviewRepository.get_question: Reads immutable snapshot by question ID and interview dual
  condition.
- DjangoInterviewRepository.decision_logs_for: Asynchronously reads decision logs for the interview
  by submission version.

Variable Index:
- logger: Logs committed version and conflict category, does not record context or answer body.

State Notes:
pending_feedback temporarily holds current evaluation; corresponding answer is persisted but not yet
scored.
commit_turn writes evaluation and submission version only after success, clearing temporary storage;
failure rolls back transaction, preventing half-round results.
Only implements v1.1 path currently used by Agent; no additional legacy compatibility or memory
fallback.
"""

import logging

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from django.utils import timezone

from agents.domain.errors import InvalidAgentState, StateConflictError
from agents.domain.models import AgentDecisionLog, CommitTurnResult, InterviewContext
from evaluation.assessment import AssessmentRecord, validate_assessment, validate_assessment_append
from evaluation.background import ShadowJob, validate_shadow_job, validate_shadow_record
from evaluation.persistence import EvaluationRecord, validate_turn_evaluation
from shared.contracts import InterviewAction, PlannedQuestion

from .agent_models import AgentAnswer, AgentInterview, AgentQuestion, AgentRequest, AgentTurn
from .evaluation_models import AgentAssessment, AgentEvaluation, AgentShadowJob

logger = logging.getLogger(__name__)


class DjangoInterviewRepository:
    """Binds interview ID, prohibits cross-interview queries or writes. All ORM methods executed via
    synchronous threads in short transactions.
    """

    def __init__(self, interview_id):
        """Stores only scope and pending feedback, does not open database or model connection; input
        is server interview ID.
        """
        self.interview_id = str(interview_id)
        self.pending_feedback = None

    def _scope(self, interview_id):
        """Validates incoming interview identifier matches adapter scope; mismatch raises
        InvalidAgentState, no I/O performed.
        """
        if str(interview_id) != self.interview_id:
            raise InvalidAgentState("Repository interview scope mismatch")

    def _evaluation_records(self):
        """Read ordered receipts and reject payload/relational identity mismatches.

        Uses the bound interview scope; returns validated internal EvaluationRecord objects.
        ORM and schema errors propagate. Call inside the turn transaction when validating a write.
        """
        records = []
        for row in AgentEvaluation.objects.filter(interview_id=self.interview_id):
            record = EvaluationRecord.model_validate(row.payload)
            snapshot = record.scored.evaluation.score_snapshot
            if (
                record.input.interview_id != self.interview_id
                or record.input.request_id != str(row.feedback_request_id)
                or record.input.answer.answer_id != str(row.answer_id)
                or record.base_state_version != row.base_state_version
                or (snapshot.snapshot_id if snapshot else None) != row.snapshot_id
            ):
                raise InvalidAgentState("Stored evaluation does not match its relational identity")
            records.append(record)
        return records

    @sync_to_async
    def get_evaluation_records(self, interview_id):
        """Return ordered scoring receipts after rejecting a different interview ID.

        Runs ORM reads in the synchronous adapter thread; never exposes receipts to client APIs.
        """
        self._scope(interview_id)
        return self._evaluation_records()

    @sync_to_async
    def get_shadow_jobs(self, interview_id):
        self._scope(interview_id)
        return self._shadow_jobs()

    def _shadow_jobs(self):
        jobs = []
        for row in AgentShadowJob.objects.filter(interview_id=self.interview_id).select_related(
            "answer__question", "feedback_request"
        ):
            job = ShadowJob.model_validate(row.payload)
            if (
                job.request.interview_id != self.interview_id
                or job.request.request_id != str(row.feedback_request_id)
                or job.request.answer.answer_id != str(row.answer_id)
                or job.snapshot().state.state_version != row.base_state_version
                or row.answer.text != job.request.answer.text
                or str(row.answer.question_id) != job.request.question.question_id
                or row.answer.question.payload != job.request.question.model_dump(mode="json")
                or row.answer.evaluation != job.feedback.model_dump(mode="json")
                or row.answer.committed_state_version != row.committed_state_version
                or str(row.answer.request_id) != str(row.feedback_request_id)
                or str(row.feedback_request.interview_id) != self.interview_id
            ):
                raise InvalidAgentState("Stored shadow job does not match its relational identity")
            jobs.append(job)
        return jobs

    def _assessment_records(self):
        jobs = {j.request.request_id: j for j in self._shadow_jobs()}
        records = []
        for row in AgentAssessment.objects.filter(
            job__interview_id=self.interview_id
        ).select_related("job"):
            record = AssessmentRecord.model_validate(row.payload)
            job = jobs.get(str(row.job.feedback_request_id))
            if job is None:
                raise InvalidAgentState("Assessment is missing its saved source")
            records.append(validate_assessment(job, record))
        return records

    @sync_to_async
    def get_assessment_records(self, interview_id):
        self._scope(interview_id)
        return self._assessment_records()

    @sync_to_async
    def append_assessment_record(self, job, result):
        self._scope(job.request.interview_id)
        with transaction.atomic():
            changed = AgentInterview.objects.filter(id=self.interview_id).update(
                updated_at=timezone.now()
            )
            if changed != 1:
                raise InvalidAgentState("Assessment interview no longer exists")
            jobs = self._shadow_jobs()
            canonical = next(
                (j for j in jobs if j.request.request_id == job.request.request_id), None
            )
            if canonical != job:
                raise InvalidAgentState("Assessment job does not match the saved source")
            prior = self._assessment_records()
            existing = next((r for r in prior if r.request_id == result.request_id), None)
            if existing is not None:
                if existing != result:
                    raise StateConflictError("Assessment already has different content")
                return
            stored = InterviewContext.model_validate(
                AgentInterview.objects.get(id=self.interview_id).context
            )
            result = validate_assessment_append(job, result, stored, prior, jobs)
            row = AgentShadowJob.objects.get(
                interview_id=self.interview_id, feedback_request_id=job.request.request_id
            )
            AgentAssessment.objects.create(job=row, payload=result.model_dump(mode="json"))

    @sync_to_async
    def append_shadow_record(self, job, result):
        self._scope(job.request.interview_id)
        with transaction.atomic():
            # Serialize result publication only. Never advance live state or await a model here.
            changed = AgentInterview.objects.filter(id=self.interview_id).update(
                updated_at=timezone.now()
            )
            if changed != 1:
                raise InvalidAgentState("Shadow interview no longer exists")
            jobs = self._shadow_jobs()
            canonical = next(
                (j for j in jobs if j.request.request_id == job.request.request_id), None
            )
            if canonical != job:
                raise InvalidAgentState("Shadow job does not match the saved source")
            prior = self._evaluation_records()
            existing = next(
                (r for r in prior if r.input.request_id == job.request.request_id), None
            )
            if existing is not None:
                if existing != result:
                    raise StateConflictError("Shadow result already has different content")
                return
            stored = InterviewContext.model_validate(
                AgentInterview.objects.get(id=self.interview_id).context
            )
            result = validate_shadow_record(job, result, stored, prior, jobs)
            snapshot = result.scored.evaluation.score_snapshot
            AgentEvaluation.objects.create(
                interview_id=self.interview_id,
                answer_id=job.request.answer.answer_id,
                feedback_request_id=job.request.request_id,
                base_state_version=result.base_state_version,
                committed_state_version=result.base_state_version + 1,
                snapshot_id=snapshot.snapshot_id if snapshot else None,
                payload=result.model_dump(mode="json"),
            )

    @sync_to_async
    def get_interview_context(self, interview_id):
        """Reads and validates latest context and relationship version consistency; fails explicitly
        if uninitialized or version corrupted.
        """
        self._scope(interview_id)
        record = AgentInterview.objects.filter(id=self.interview_id).first()
        if record is None or record.context is None:
            raise InvalidAgentState("Interview context is not initialized")
        context = InterviewContext.model_validate(record.context)
        self._scope(context.interview_id)
        if context.state.state_version != record.state_version:
            raise InvalidAgentState("Stored context version does not match relational version")
        return context

    @sync_to_async
    def initialize_interview(self, context):
        """Upgrades preparation state to initialized context, version increases only once.

        Input: shared InterviewContext; interview shell must be established by accepted request.
        Returns deep copy of new context.
        Conditional update prevents duplicate initialization; does not rely on SQLite-specific row
        locking, nor waits for model inside transaction.
        """
        self._scope(context.interview_id)
        saved = context.model_copy(deep=True)
        saved.state.state_version += 1
        changed = AgentInterview.objects.filter(
            id=self.interview_id, context__isnull=True, state_version=0, status="preparing"
        ).update(
            context=saved.model_dump(mode="json"),
            state_version=saved.state.state_version,
            status="active",
            updated_at=timezone.now(),
        )
        if changed != 1:
            raise StateConflictError("Interview is missing or already initialized")
        return saved

    @sync_to_async
    def accept_answer(self, request_id, answer):
        """Saves answer before evaluation, validates request, current question, and session
        ownership.

        Input: accepted answer/skip/finish request UUID and CandidateAnswer; returns None. Empty
        text is permitted only through the validated Skip command; normal/finish text remains
        nonempty. Original answer persisted
        but evaluation remains empty.
        One-answer-per-question constraint prevents repeated consumption; does not reuse failed
        answers or auto-retry them.
        """
        self._scope(answer.interview_id)
        with transaction.atomic():
            record = AgentInterview.objects.get(id=self.interview_id)
            current = (record.latest_action or {}).get("question") or {}
            if record.status != "active" or current.get("question_id") != answer.question_id:
                raise InvalidAgentState("Answer does not target the active question")
            request = AgentRequest.objects.get(
                id=request_id,
                interview_id=self.interview_id,
                kind__in=["answer", "skip", "finish"],
                status="running",
            )
            question = AgentQuestion.objects.get(
                id=answer.question_id, interview_id=self.interview_id
            )
            AgentAnswer.objects.create(
                id=answer.answer_id, request=request, question=question, text=answer.text
            )

    @sync_to_async
    def commit_turn(self, request):
        """Atom publishes new versions, issues, feedback evidence, and audit logs.

        Input is a validated CommitTurnRequest; returns CommitTurnResult. First perform a version
        CAS write, then read the current context to avoid SQLite's read-then-upgrade write lock;
        subsequent failures will roll back the CAS and all associated writes.
        Feedback must match the adapter's temporary evaluation and received response. Unique
        constraint conflicts are converted to StateConflictError; database busy or unavailable
        errors retain the original exception without automatic retry or alternative scoring.
        """
        self._scope(request.interview_id)
        version = request.expected_state_version
        if request.new_state.state_version != version:
            raise StateConflictError("State payload version does not match expected version")
        if request.decision_log.state_version != version + 1:
            raise InvalidAgentState("Decision log must match the committed state version")
        try:
            with transaction.atomic():
                changed = AgentInterview.objects.filter(
                    id=self.interview_id,
                    state_version=version,
                    status="active",
                    context__isnull=False,
                ).update(state_version=version + 1, updated_at=timezone.now())
                if changed != 1:
                    raise StateConflictError("Interview version changed or interview is inactive")
                record = AgentInterview.objects.get(id=self.interview_id)
                stored = InterviewContext.model_validate(record.context)
                self._scope(stored.interview_id)
                if stored.state.state_version != version:
                    raise InvalidAgentState("Stored context version is inconsistent")
                saved = (
                    request.new_context.model_copy(deep=True)
                    if request.new_context is not None
                    else stored.model_copy(deep=True)
                )
                saved.state = request.new_state.model_copy(deep=True)
                saved.state.state_version = version + 1
                saved.pending_evaluation = None
                saved.pending_shadow_job = None
                saved.processed_feedback_ids = list(stored.processed_feedback_ids)
                # Replay/identity validation and the receipt insert share the same CAS transaction
                # as the answer and next action. Any subsequent failure rolls everything back.
                evaluation_record = None
                if request.evaluation_record is not None:
                    question = AgentQuestion.objects.get(
                        id=stored.state.current_question_id, interview_id=self.interview_id
                    )
                    evaluation_record = validate_turn_evaluation(
                        request,
                        stored,
                        self._evaluation_records(),
                        PlannedQuestion.model_validate(question.payload),
                    )
                else:
                    validate_turn_evaluation(request, stored, [], None)
                shadow_question = None
                if request.shadow_job is not None:
                    shadow_question = PlannedQuestion.model_validate(
                        AgentQuestion.objects.get(
                            id=stored.state.current_question_id, interview_id=self.interview_id
                        ).payload
                    )
                shadow_job = validate_shadow_job(request, stored, shadow_question)
                if request.feedback_request_id is not None:
                    feedback = self.pending_feedback
                    if feedback is None or feedback.request_id != request.feedback_request_id:
                        raise InvalidAgentState("Commit lacks the matching evaluated answer")
                    if feedback.request_id in stored.processed_feedback_ids:
                        raise StateConflictError("Feedback has already been committed")
                    answer = AgentAnswer.objects.select_related("request").get(
                        request_id=feedback.request_id,
                        request__interview_id=self.interview_id,
                        request__status="running",
                        question_id=feedback.question_id,
                        question__interview_id=self.interview_id,
                        committed_state_version__isnull=True,
                    )
                    if evaluation_record is not None:
                        source = evaluation_record.input.answer
                        if str(answer.id) != source.answer_id or answer.text != source.text:
                            raise InvalidAgentState("Evaluation must quote the accepted answer")
                        if feedback.model_dump() != evaluation_record.feedback.model_dump():
                            raise InvalidAgentState("Pending feedback does not match evaluation")
                        snapshot = evaluation_record.scored.evaluation.score_snapshot
                        AgentEvaluation.objects.create(
                            interview_id=self.interview_id,
                            answer=answer,
                            feedback_request_id=feedback.request_id,
                            base_state_version=version,
                            committed_state_version=version + 1,
                            snapshot_id=snapshot.snapshot_id if snapshot else None,
                            payload=evaluation_record.model_dump(mode="json"),
                        )
                    if shadow_job is not None:
                        source = shadow_job.request.answer
                        if (
                            str(answer.id) != source.answer_id
                            or answer.text != source.text
                            or feedback.model_dump() != shadow_job.feedback.model_dump()
                        ):
                            raise InvalidAgentState("Shadow source must match the accepted answer")
                        AgentShadowJob.objects.create(
                            interview_id=self.interview_id,
                            answer=answer,
                            feedback_request_id=feedback.request_id,
                            base_state_version=version,
                            committed_state_version=version + 1,
                            payload=shadow_job.model_dump(mode="json"),
                        )
                    answer.evaluation = feedback.model_dump(mode="json")
                    answer.committed_state_version = version + 1
                    answer.save(update_fields=["evaluation", "committed_state_version"])
                    saved.processed_feedback_ids.append(feedback.request_id)
                if request.question is not None:
                    question, created = AgentQuestion.objects.get_or_create(
                        id=request.question.question_id,
                        defaults={
                            "interview_id": self.interview_id,
                            "ordinal": saved.state.question_index,
                            "payload": request.question.model_dump(mode="json"),
                        },
                    )
                    if not created and (
                        str(question.interview_id) != self.interview_id
                        or question.payload != request.question.model_dump(mode="json")
                        or question.ordinal != saved.state.question_index
                    ):
                        raise StateConflictError("Question ID already belongs to another snapshot")
                AgentTurn.objects.create(
                    interview_id=self.interview_id,
                    state_version=version + 1,
                    feedback_request_id=request.feedback_request_id,
                    action=request.resulting_action.model_dump(mode="json"),
                    decision_log=request.decision_log.model_dump(mode="json"),
                )
                record.context = saved.model_dump(mode="json")
                record.latest_action = request.resulting_action.model_dump(mode="json")
                record.save(update_fields=["context", "latest_action"])
        except IntegrityError as exc:
            logger.warning(
                "Agent commit constraint conflict interview=%s version=%d",
                self.interview_id,
                version,
            )
            raise StateConflictError("Turn violates a persistence uniqueness constraint") from exc
        if request.feedback_request_id is not None:
            self.pending_feedback = None
        logger.info("Agent turn committed interview=%s version=%d", self.interview_id, version + 1)
        return CommitTurnResult(committed=True, state=saved.state, action=request.resulting_action)

    @sync_to_async
    def get_processed_feedback_action(self, interview_id, feedback_request_id):
        """Query only submitted feedback actions within this interview; return None if not found,
        without revealing other interview records.
        """
        self._scope(interview_id)
        row = AgentTurn.objects.filter(
            interview_id=self.interview_id, feedback_request_id=feedback_request_id
        ).first()
        return InterviewAction.model_validate(row.action) if row else None

    @sync_to_async
    def get_question(self, question_id):
        """Read an immutable snapshot by problem ID and interview pair; throw InvalidAgentState for
        out-of-bounds or non-existent entries.
        """
        row = AgentQuestion.objects.filter(id=question_id, interview_id=self.interview_id).first()
        if row is None:
            raise InvalidAgentState("Question is unavailable in this interview")
        return PlannedQuestion.model_validate(row.payload)

    @sync_to_async
    def decision_logs_for(self, interview_id):
        """Asynchronously read the decision log for this interview by commit version; return
        independent model lists, not lazy QuerySet.
        """
        self._scope(interview_id)
        return [
            AgentDecisionLog.model_validate(row.decision_log)
            for row in AgentTurn.objects.filter(interview_id=self.interview_id)
        ]
