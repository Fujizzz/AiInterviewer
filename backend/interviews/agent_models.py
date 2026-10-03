"""Responsibilities: Store agent interview relationships, separated from fixed question bank
practice tables.

Implementation: Relationship columns carry identity, status, order, and uniqueness constraints; JSON
stores versioned Agent contract snapshots.
Related Modules: Models are registered by the app; agent_repository performs atomic commits, and
agent_records tracks request lifecycle.

Declaration Index:
- AgentInterview: Stores latest context and service lifecycle, without duplicating full history.
- AgentInterview.Meta: Constrains context version and final state time, indexes historical list for
  sorting.
- AgentRequest: Persists accepted commands and results, without storing original resume or input
  summary hashes.
- AgentRequest.Meta: Constrains request status and completion time, indexes request history within
  interviews.
- AgentQuestion: Stores immutable question snapshots; question ID is globally unique and bound to a
  single interview.
- AgentQuestion.Meta: Ensures unique question numbers within the same interview and sorts by
  question number.
- AgentAnswer: Stores accepted answers; evaluation and submission versions are either both empty or
  both present.
- AgentAnswer.Meta: Constrains evaluation submission flags; unanswered responses do not constitute
  evidence of scoring.
- AgentTurn: Stores a submitted action and decision log, forming an audit sequence by state version.
- AgentTurn.Meta: Ensures unique feedback requests and submission versions within the same
  interview.

Variable Index:
None

State Notes:
AgentInterview.owner identifies the creator user; old data is null, not publicly exposed or
automatically claimed by registered users.
resume_version fixes the user's input version; resume_text_snapshot fixes the actual input;
context's candidate_profile fixes the actual structured data. Subsequent version switches do not
modify historical basis; public data is still read from verified responses.
Interview.status is preparing/active/completed/interrupted/failed; separate from internal Agent
state.
Request.status is running/succeeded/failed/interrupted; abrupt process termination may leave
requests in running state, which cannot be automatically replayed.
context/state_version is the sole source of current state; Request.response is a stored immutable
response snapshot before sending, used to confirm results, not indicating client receipt. JSON is
not used for cross-candidate retrieval or as a substitute for relationship constraints.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class AgentInterview(models.Model):
    """Stores latest context and service lifecycle, without duplicating full history.

    Input provided by backend and agent repository; id reuses backend UUID, owner takes only
    authenticated WebSocket identity.
    context being empty indicates initialization not yet completed; state_version and JSON version
    synchronized via repository transaction.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="agent_interviews",
    )
    status = models.CharField(max_length=16, default="preparing")
    job_title = models.TextField(blank=True)
    resume_version = models.ForeignKey(
        "ResumeVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="interviews"
    )
    resume_text_snapshot = models.TextField(blank=True)
    context = models.JSONField(null=True, blank=True)
    state_version = models.PositiveIntegerField(default=0)
    latest_action = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Constrains context version and final state time, indexes historical list for sorting."""

        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["-created_at", "-id"], name="agent_history_order")]
        constraints = [
            models.CheckConstraint(
                condition=Q(context__isnull=True, state_version=0)
                | Q(context__isnull=False, state_version__gte=1),
                name="agent_context_version",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["preparing", "active"], closed_at__isnull=True)
                | Q(status__in=["completed", "interrupted", "failed"], closed_at__isnull=False),
                name="agent_lifecycle_time",
            ),
        ]


class AgentRequest(models.Model):
    """Persists accepted commands and results, without storing original resume or input summary
    hashes.

    id is client-side UUID, globally unique to prevent duplicate triggering after reconnection.
    Duplicate IDs do not return responses from other connections.
    kind permits prepare/start/answer/skip/finish; discard is an out-of-band deletion control.
    response saves successful result; error_code only records backend-fixed error codes, not vendor
    exceptions or keys.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    interview = models.ForeignKey(
        AgentInterview, related_name="requests", on_delete=models.CASCADE, db_index=False
    )
    kind = models.CharField(max_length=16)
    status = models.CharField(max_length=16, default="running")
    response = models.JSONField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Constrain request status/completion time and index requests within each interview."""

        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["interview", "created_at", "id"], name="agent_request_history")
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["interview"],
                condition=Q(status="running"),
                name="agent_one_running_request",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=["prepare", "start", "answer", "skip", "finish"]),
                name="agent_request_kind",
            ),
            models.CheckConstraint(
                condition=Q(status="running", finished_at__isnull=True, response__isnull=True)
                | Q(status="succeeded", finished_at__isnull=False, response__isnull=False)
                | Q(
                    status__in=["failed", "interrupted"],
                    finished_at__isnull=False,
                    response__isnull=True,
                ),
                name="agent_request_result",
            ),
        ]


class AgentQuestion(models.Model):
    """Stores immutable question snapshots; question ID is globally unique and bound to a single
    interview.

    id uses Agent’s string identifier rather than assuming it must be UUID; payload retains original
    value from shared PlannedQuestion.
    ordinal uses Agent’s question_index; this table does not alter question order, difficulty, or
    competency dimensions.
    """

    id = models.CharField(primary_key=True, max_length=255)
    interview = models.ForeignKey(
        AgentInterview, related_name="questions", on_delete=models.CASCADE, db_index=False
    )
    ordinal = models.PositiveIntegerField()
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Ensure unique, ordered question numbers within each interview."""

        ordering = ["ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["interview", "ordinal"], name="agent_question_order")
        ]


class AgentAnswer(models.Model):
    """Stores accepted answers; evaluation and submission versions are either both empty or both
    present.

    question and request are one-to-one, preventing multiple answers per question or multiple
    answers per request; session ownership across tables is validated by transaction layer.
    committed_state_version is non-empty only when the answer enters Agent state; model failures
    retain original answer for diagnosis.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.OneToOneField(AgentQuestion, related_name="answer", on_delete=models.CASCADE)
    request = models.OneToOneField(AgentRequest, related_name="answer", on_delete=models.CASCADE)
    text = models.TextField()
    evaluation = models.JSONField(null=True, blank=True)
    committed_state_version = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Constrains evaluation submission flag; unanswered responses do not serve as evidence of
        scoring.
        """

        constraints = [
            models.CheckConstraint(
                condition=Q(evaluation__isnull=True, committed_state_version__isnull=True)
                | Q(
                    evaluation__isnull=False,
                    committed_state_version__isnull=False,
                    committed_state_version__gte=1,
                ),
                name="agent_answer_evidence",
            )
        ]


class AgentTurn(models.Model):
    """Stores a submitted action and decision log, forming an audit sequence by state version.

    action and decision_log are original Agent contracts; feedback_request being empty indicates
    initialization or stage transition.
    Database transaction commits this row, current context, questions, and answer evaluations
    together, avoiding partial scoring rounds.
    """

    interview = models.ForeignKey(
        AgentInterview, related_name="turns", on_delete=models.CASCADE, db_index=False
    )
    state_version = models.PositiveIntegerField()
    feedback_request = models.OneToOneField(
        AgentRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="committed_turn"
    )
    action = models.JSONField()
    decision_log = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Constrains unique submission version and feedback request within the same interview."""

        ordering = ["state_version"]
        constraints = [
            models.UniqueConstraint(
                fields=["interview", "state_version"], name="agent_turn_version"
            )
        ]
