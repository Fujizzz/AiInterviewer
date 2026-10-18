"""Responsibilities: Implement transactional practice-session use cases and their explicit write
boundaries.
Implementation: Select questions, claim a session version, transition one item, or finish a session
atomically.
Related Modules: API views and serializers validate requests; models define persisted session and
question state.

Declaration Index:
- create_session: Create an owned session and question snapshots from enabled questions or the
  requested ID order.
- claim_session: Claim write access to an active session at the requested version.
- update_item: Apply a start, complete, or skip transition to one session item.
- finish_session: Complete a session and mark unfinished items as skipped.

Variable Index:
- logger: Module-level logger for session and item transition metadata.
"""

import logging

from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .errors import Conflict
from .models import PracticeSession, Question, SessionQuestion

logger = logging.getLogger(__name__)


@transaction.atomic
def create_session(question_ids=None, *, owner=None):
    """Create a session and text snapshots from enabled questions or the requested ID order.

    Inputs are optional question_ids and an authenticated owner; None is reserved for local
    anonymous development.
    Return the created session. Validate the 1–100 question bound, then create the session and items
    in one transaction.
    Missing, disabled, empty, or oversized selections raise ValidationError and roll back the
    transaction.
    """
    available = Question.objects.filter(enabled=True)
    if question_ids is not None:
        questions = list(available.filter(pk__in=question_ids))
        by_id = {q.id: q for q in questions}
        if any(pk not in by_id for pk in question_ids):
            raise ValidationError({"question_ids": "Some questions are missing or disabled."})
        questions = [by_id[pk] for pk in question_ids]
    else:
        # Read at most one item beyond the established limit so oversized banks are not loaded into
        # memory.
        questions = list(available[:101])
    if not questions or len(questions) > 100:
        raise ValidationError({"question_ids": "Select between 1 and 100 enabled questions."})
    session = PracticeSession.objects.create(owner=owner)
    SessionQuestion.objects.bulk_create(
        [
            SessionQuestion(session=session, question=q, question_text=q.text, position=index)
            for index, q in enumerate(questions, start=1)
        ]
    )
    logger.info("Session created session=%s questions=%d", session.id, len(questions))
    return session


def claim_session(session_id, version):
    """Claim write access to an active session at version using a conditional version increment.

    Return the updated session; a missing row is 404 and an unchanged version raises Conflict. The
    caller must hold
    an outer atomic transaction so later validation failures roll back the version increment as
    well.
    """
    changed = PracticeSession.objects.filter(
        pk=session_id, version=version, status="active"
    ).update(version=F("version") + 1)
    session = get_object_or_404(PracticeSession, pk=session_id)
    if not changed:
        raise Conflict(
            "Session is completed or version is stale. "
            "Fetch its current state before deciding the next action."
        )
    return session


@transaction.atomic
def update_item(session_id, item_id, command):
    """Apply a validated start, complete, or skip command and return the updated session.

    Inputs are session ID, item ID, and validated command. Claim the session version, verify item
    ownership/state,
    and save the answer and server timestamps. Database writes and contextual logging occur in the
    transaction;
    invalid transitions leave no partial changes.
    """
    session = claim_session(session_id, command["version"])
    item = get_object_or_404(SessionQuestion, pk=item_id, session=session)
    action = command["action"]
    now = timezone.now()
    if action == "start":
        if item.status != "pending" or session.items.filter(status="answering").exists():
            raise Conflict("Only a pending item can start, and only one item may be answering.")
        item.status, item.started_at = "answering", now
    elif action == "complete":
        if item.status != "answering":
            raise Conflict("Only an answering item can complete.")
        item.status, item.finished_at = "completed", now
        item.answer_text = command.get("answer_text", "")
        item.duration_ms = command.get("duration_ms")
    else:
        if item.status not in ("pending", "answering"):
            raise Conflict("Only pending or answering items can be skipped.")
        item.status, item.finished_at = "skipped", now
    item.save()
    logger.info(
        "Item transition session=%s item=%s action=%s version=%d",
        session.id,
        item.id,
        action,
        session.version,
    )
    return session


@transaction.atomic
def finish_session(session_id, version):
    """Complete a session and mark all unfinished items skipped using one captured timestamp and one
    transaction.

    Return the new-version session. Preserve existing errors for version conflicts, completed
    sessions, and missing rows.
    """
    session = claim_session(session_id, version)
    now = timezone.now()
    session.items.filter(status__in=["pending", "answering"]).update(
        status="skipped", finished_at=now
    )
    session.status, session.finished_at = "completed", now
    session.save(update_fields=["status", "finished_at"])
    logger.info("Session finished session=%s version=%d", session.id, session.version)
    return session
