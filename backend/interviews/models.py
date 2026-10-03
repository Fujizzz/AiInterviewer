"""Responsibilities: Define practice interview data models and enforce their database constraints.
Implementation: Store question snapshots, session state, timing, and optimistic-concurrency version
at the database boundary.

Related Modules: Import agent_models/resume_models to register Agent and resume version tables;
practice model and its timing rules remain unchanged.

Declaration Index:
- Question:
  Question bank entity. Sorting field determines question selection order in new sessions;
  deactivation does not affect already created question snapshots.
- Question.Meta:
  Sort by position, creation time, and primary key for stable ordering, avoiding random order under
  same position.
- PracticeSession:
  Session entity. Stores fixed preparation/answer durations, status, time, and optimistic
  concurrency version.
- PracticeSession.Status:
  Session status domain: active is modifiable, completed is terminal and cannot be reopened.
- PracticeSession.Meta:
  Retrieve by creation time in descending order, and constrain status and finished_at null
  relationship.
- SessionQuestion:
  Snapshot and answer entity for single question. Source question can be null; historical text and
  answers are retained.
- SessionQuestion.Status:
  Single-question status domain: pending, answering, completed, skipped.
- SessionQuestion.Meta:
  Constrain unique question order within same session, at most one answering question, and valid
  status-time combinations.

Variable Index:
None

Key State Explanations:
PracticeSession.version used for optimistic concurrency control; prep_seconds/answer_seconds
preserve practice timing.
PracticeSession.owner is the creator; null owner indicates old local records, not assigned to any
registered account.
SessionQuestion.question_text stores question snapshot.
Each Meta.constraints enforces status/time combination, unique order within session, and at most one
answering question.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from .agent_models import (  # noqa: F401
    AgentAnswer,
    AgentInterview,
    AgentQuestion,
    AgentRequest,
    AgentTurn,
)
from .resume_models import ResumeVersion  # noqa: F401


class Question(models.Model):
    """Question bank entity. Sorting field determines question selection order in new sessions;
    deactivation does not affect already created question snapshots.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    text = models.TextField(max_length=4000)
    position = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Sort by position, creation time, and primary key for stable ordering, avoiding random
        order under same position.
        """

        ordering = ["position", "created_at", "id"]


class PracticeSession(models.Model):
    """Stores creator, fixed timing, and version; null owner indicates old local records, not
    belonging to any registered user.
    """

    class Status(models.TextChoices):
        """Session status domain: active is modifiable, completed is terminal and cannot be
        reopened.
        """

        ACTIVE = "active"
        COMPLETED = "completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="practice_sessions",
    )
    status = models.CharField(max_length=16, choices=Status, default=Status.ACTIVE)
    prep_seconds = models.PositiveIntegerField(default=10, editable=False)
    answer_seconds = models.PositiveIntegerField(default=90, editable=False)
    version = models.PositiveIntegerField(default=1)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Retrieve by creation time in descending order, and constrain status and finished_at null
        relationship.
        """

        ordering = ["-started_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status="active", finished_at__isnull=True)
                | Q(status="completed", finished_at__isnull=False),
                name="session_status_timestamp",
            )
        ]


class SessionQuestion(models.Model):
    """Single-question snapshot and response entity. Associated source question may be null, but
    historical text and responses are retained.
    """

    class Status(models.TextChoices):
        """Single-question status fields: pending, answering, completed, skipped.
        """

        PENDING = "pending"
        ANSWERING = "answering"
        COMPLETED = "completed"
        SKIPPED = "skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(PracticeSession, related_name="items", on_delete=models.CASCADE)
    question = models.ForeignKey(Question, null=True, on_delete=models.SET_NULL)
    question_text = models.TextField(max_length=4000)
    position = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    answer_text = models.TextField(max_length=20000, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Constraints: unique order within the same session, at most one question in 'answering'
        state, and valid temporal state transitions.
        """

        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["session", "position"], name="unique_session_position"),
            models.UniqueConstraint(
                fields=["session"], condition=Q(status="answering"), name="one_answering_item"
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="pending", started_at__isnull=True, finished_at__isnull=True)
                    | Q(status="answering", started_at__isnull=False, finished_at__isnull=True)
                    | Q(status="completed", started_at__isnull=False, finished_at__isnull=False)
                    | Q(status="skipped", finished_at__isnull=False)
                ),
                name="item_status_timestamps",
            ),
        ]
