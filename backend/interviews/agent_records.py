"""Responsibilities: Record command reception, response submission, and connection exit outside
model invocation.

Implementation: Synchronous short transactions executed via sync_to_async; any database failure
propagates to caller, no downgrade to memory.
Related Modules: agent_socket reserves request before started; complete_request and result
transmission require a trusted gateway receipt: allow after review, disabled only during explicitly
suspended review. Both modes verify ownership, body integrity and state version.

Declaration Index:
- DuplicateRequest: Identifies previously accepted UUIDs, without revealing associated interview or
  input.
- PendingRequest: Identifies ongoing or unconfirmed requests within the same interview, preventing
  initiation of next request.
- reserve_request: Atomically creates interview shell and request record; duplicate requests do not
  create new interviews.
- complete_request: Atomically verifies gateway receipts, ownership, and version; saves the response
  and its actual allow/disabled review status.
- fail_request: Ends currently executing request with limited business/security error codes, marks
  interview failed.
- interrupt_interview: Marks unfinished requests and interviews upon connection exit, without
  overwriting saved successful results.
- discard_interview: Delete this connection's owned interview and cascading history after
  cancellation.

Variable Index:
- logger: Logs only interview, request, operation, and lifecycle events, not input or response body.
"""

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ai_security.errors import SecurityContextChanged

from .agent_models import AgentAnswer, AgentInterview, AgentRequest, AgentTurn
from .agent_safety import make_output_receipt
from .evaluation_models import AgentEvaluation
from .resume_models import ResumeVersion

logger = logging.getLogger(__name__)


@sync_to_async
def discard_interview(interview_id, owner_id):
    """Functionality: Honor the user's explicit end-without-saving choice.
    Inputs: Server session ID and authenticated connection owner; no arbitrary client ID.
    Outputs: None; deletes the matching interview, scoring receipts, requests, answers and turns.
    Logic: After awaiting cancellation, verify ownership and delete protected scoring/turn/answer
    links before the interview in one transaction; failure rolls back the entire removal.
    This explicit discard is the lifecycle exception to append-only scoring retention.
    Constraints: Saved resume versions are preserved; database failures propagate and are logged
    by the socket. Already sent vendor calls may finish but cannot recreate the deleted session.
    """
    with transaction.atomic():
        owned = AgentInterview.objects.filter(id=interview_id, owner_id=owner_id)
        if not owned.exists():
            return
        AgentEvaluation.objects.filter(interview_id=interview_id).delete()
        AgentTurn.objects.filter(interview_id=interview_id).delete()
        AgentAnswer.objects.filter(question__interview_id=interview_id).delete()
        deleted, _ = owned.delete()
    logger.info("Agent interview discarded interview=%s deleted_records=%d", interview_id, deleted)


class DuplicateRequest(Exception):
    """Identifies previously accepted UUIDs, without revealing associated interview or input."""


class PendingRequest(Exception):
    """Identifies ongoing or unconfirmed requests within the same interview, preventing initiation
    of next request.
    """


@sync_to_async
def reserve_request(interview_id, command, *, owner_id=None, resume_version_id=None):
    """Atomically creates interview shell and request record; duplicate requests do not create new
    interviews.

    Input: server interview UUID, validated command, and handshake-authenticated owner_id; default
    None for legacy local flow.
    owner_id cannot be provided by client field; mismatched session ownership is rejected.
    resume_version_id is optional personal version; transaction re-validates ownership, status, and
    text, locks historical input.
    Old text path does not save text; version path saves actual text snapshot in interview, version
    deletion subject to PROTECT constraint.
    Returns None. Global primary key conflict converted to DuplicateRequest; existing uncompleted
    request triggers PendingRequest;
    other database errors preserved for propagation.
    This step must occur before any paid call; same UUID with different content will not trigger
    re-execution.
    """
    try:
        with transaction.atomic():
            interview, _ = AgentInterview.objects.get_or_create(
                id=interview_id, defaults={"owner_id": owner_id}
            )
            if interview.owner_id != owner_id:
                raise PermissionError("Interview does not belong to this connection")
            if interview.status not in {"preparing", "active"}:
                raise RuntimeError("Interview is no longer accepting commands")
            if command.type in {"prepare", "start"}:
                version = None
                if resume_version_id is not None:
                    version = ResumeVersion.objects.select_for_update().get(
                        pk=resume_version_id, owner_id=owner_id, status="ready"
                    )
                    if version.text != command.resume_text:
                        raise ValueError("Resume input changed")
                interview.resume_version = version
                interview.resume_text_snapshot = command.resume_text if version else ""
                interview.save(
                    update_fields=["resume_version", "resume_text_snapshot", "updated_at"]
                )
            AgentRequest.objects.create(
                id=command.request_id, interview=interview, kind=command.type
            )
            if command.type == "start":
                interview.job_title = command.job_title
                interview.save(update_fields=["job_title", "updated_at"])
    except IntegrityError as exc:
        if AgentRequest.objects.filter(id=command.request_id).exists():
            raise DuplicateRequest("Request was already accepted") from exc
        if AgentRequest.objects.filter(interview_id=interview_id, status="running").exists():
            raise PendingRequest("Interview has an unresolved request") from exc
        raise
    logger.info(
        "Agent request accepted interview=%s request=%s kind=%s",
        interview_id,
        command.request_id,
        command.type,
    )


@sync_to_async
def complete_request(interview_id, request_id, response, *, receipt, owner_id=None):
    """Submit a gateway-released response; input: interview/request ID, full body, receipt, and
    authentication ownership; returns None.

    Required receipt comes from a trusted gateway callback, not the browser or Agent. Disabled
    receipts are accepted only while the explicit settings switch is false; they never claim review.
    Transaction validates response digest, ownership,
    current version, and running status; saves _security for historical interface verification;
    online body excludes this internal field.
    Final report confirms successful save and marks as completed; send failure does not undo
    completed work, no retransmission or retry.
    """
    version = receipt.get("state_version")
    review_status = receipt.get("status")
    if (
        type(version) is not int
        or version < 0
        or review_status not in {"allow", "disabled"}
        or (review_status == "disabled" and settings.AI_SECURITY_ENABLED)
        or "_security" in response
        or receipt
        != make_output_receipt(response, request_id, version, review_status=review_status)
    ):
        raise ValueError("invalid output release receipt")
    now = timezone.now()
    with transaction.atomic():
        # Conditional update acquires write lock; does not wait for model inside lock, preventing
        # state change between check and commit.
        matched = AgentInterview.objects.filter(
            id=interview_id,
            owner_id=owner_id,
            state_version=version,
            status__in=["preparing", "active"],
        ).update(updated_at=now)
        if matched != 1:
            raise SecurityContextChanged("output approval state changed")
        changed = AgentRequest.objects.filter(
            id=request_id, interview_id=interview_id, status="running"
        ).update(status="succeeded", response={**response, "_security": receipt}, finished_at=now)
        if changed != 1:
            raise RuntimeError("Request is not pending in this interview")
        if response["type"] == "finished":
            AgentInterview.objects.filter(id=interview_id, status="active").update(
                status="completed", closed_at=now, updated_at=now
            )
    logger.info(
        "Agent request saved interview=%s request=%s review_status=%s",
        interview_id,
        request_id,
        review_status,
    )


@sync_to_async
def fail_request(interview_id, request_id, *, error_code="agent_failed"):
    """Ends request with limited error codes; input: interview/request ID and fixed code; output
    None, without saving original exception.

    Default remains agent_failed; security failures use dedicated codes. Successfully completed
    requests are not rolled back to failed.
    Database unavailability propagates exception, handed to protocol layer for logging; success is
    not masked by failure.
    """
    if error_code not in {
        "agent_failed",
        "security_denied",
        "security_check_failed",
        "security_context_changed",
        "security_contract_failed",
    }:
        raise ValueError("unknown request failure code")
    now = timezone.now()
    with transaction.atomic():
        changed = AgentRequest.objects.filter(
            id=request_id, interview_id=interview_id, status="running"
        ).update(status="failed", error_code=error_code, finished_at=now)
        if changed:
            AgentInterview.objects.filter(
                id=interview_id, status__in=["preparing", "active"]
            ).update(status="failed", closed_at=now, updated_at=now)
    logger.warning("Agent request failed interview=%s request=%s", interview_id, request_id)


@sync_to_async
def interrupt_interview(interview_id):
    """Marks unfinished requests and interviews upon connection exit, without overwriting saved
    successful results.

    Input: interview ID associated with the connection; returns None. Must be called after local
    business tasks canceled and awaited.
    Interruption does not imply vendor has stopped billing; forced process exit cannot invoke this
    function, leaving running states unautomatically replayed.
    """
    now = timezone.now()
    with transaction.atomic():
        AgentRequest.objects.filter(interview_id=interview_id, status="running").update(
            status="interrupted", error_code="connection_closed", finished_at=now
        )
        changed = AgentInterview.objects.filter(
            id=interview_id, status__in=["preparing", "active"]
        ).update(status="interrupted", closed_at=now, updated_at=now)
    if changed:
        logger.info("Agent interview interrupted interview=%s", interview_id)
