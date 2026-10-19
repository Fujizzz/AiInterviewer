"""Database-backed scoring worker with fenced leases, resumable stages and report release."""

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.db import InterfaceError, OperationalError, transaction
from django.db.models import Q
from django.utils import timezone

from agents.domain.models import InterviewContext
from app.adapters.rubric_evaluation import build_evaluation_adapter
from app.reporting.final_report import build_final_report
from evaluation.assessment import assessed_report_context, update_display_history

from .agent_models import AgentInterview
from .agent_provider import BackendLLM
from .agent_public_output import public_job_profile, public_question_history
from .agent_repository import DjangoInterviewRepository
from .agent_safety import InterviewIOGateway, IOSafetyError, make_output_receipt
from .evaluation_models import AgentEvaluationRun

logger = logging.getLogger(__name__)
LEASE_SECONDS = 300
PUBLICATION_ATTEMPTS = 3
STORAGE_ATTEMPTS = 3


def ready_for_report(interview):
    return (
        interview.status in {"completed", "interrupted", "failed"}
        or (interview.context or {}).get("state", {}).get("status") == "finished"
    )


@sync_to_async
def claim_run():
    now = timezone.now()
    eligible = (Q(status="pending") & (Q(lease_until__isnull=True) | Q(lease_until__lte=now))) | Q(
        status="running", lease_until__lte=now
    )
    for key in (
        AgentEvaluationRun.objects.filter(eligible)
        .order_by("updated_at")
        .values_list("pk", flat=True)[:20]
    ):
        token = str(uuid4())
        if AgentEvaluationRun.objects.filter(eligible, pk=key).update(
            status="running",
            lease_token=token,
            lease_until=now + timedelta(seconds=LEASE_SECONDS),
            updated_at=now,
        ):
            return str(key), token
    return None


def locked_run(interview_id, token):
    if (
        AgentEvaluationRun.objects.filter(
            interview_id=interview_id,
            status="running",
            lease_token=token,
            lease_until__gt=timezone.now(),
        ).update(updated_at=timezone.now())
        != 1
    ):
        raise IOSafetyError("evaluation lease lost")
    return AgentEvaluationRun.objects.select_related("interview").get(pk=interview_id)


@sync_to_async
def release_run(interview_id, token, status, *, retry_delay=0):
    with transaction.atomic():
        if not AgentInterview.objects.filter(pk=interview_id).update(updated_at=timezone.now()):
            return
        run = AgentEvaluationRun.objects.filter(
            pk=interview_id, lease_token=token, status="running"
        ).first()
        if run is None:
            return
        if status in {"idle", "completed"}:
            repo = DjangoInterviewRepository(interview_id)
            records = {r.input.request_id for r in repo._evaluation_records()}
            assessments = {r.request_id for r in repo._assessment_records()}
            pending = any(
                (j.shadow_enabled and j.request.request_id not in records)
                or (j.assessment_requested and j.request.request_id not in assessments)
                for j in repo._shadow_jobs()
            )
            closed = ready_for_report(run.interview)
            if pending or (closed and run.report is None):
                status = "pending"
        AgentEvaluationRun.objects.filter(pk=interview_id, lease_token=token).update(
            status=status,
            lease_token="",
            lease_until=timezone.now() + timedelta(seconds=retry_delay)
            if status == "pending" and retry_delay
            else None,
            updated_at=timezone.now(),
        )


@sync_to_async
def publication_snapshot(interview_id, token, owner_id):
    with transaction.atomic():
        run = locked_run(interview_id, token)
        record = run.interview
        if record.owner_id != owner_id or not ready_for_report(record):
            raise IOSafetyError("report owner or lifecycle changed")
        context = InterviewContext.model_validate(record.context)
        if (
            context.interview_id != interview_id
            or context.state.state_version != record.state_version
        ):
            raise IOSafetyError("inconsistent report state")
        from app.reporting.explanation import build_explanation
        from evaluation.assessment import project_assessments
        from evaluation.publication import formal_publication

        repo = DjangoInterviewRepository(interview_id)
        context, _ = project_assessments(context, repo._shadow_jobs(), repo._assessment_records())
        records = repo._evaluation_records()
        scores = formal_publication(context, records)
        scores["explanation"] = build_explanation(context, records, scores).model_dump(mode="json")
        # A closed connection can end an interview before its planning state says FINISH.
        # This is a report-only projection; canonical state and live request receipts stay intact.
        context = context.model_copy(deep=True)
        context.state.status = "finished"
        return {
            "state_version": record.state_version,
            "context": context,
            "kind": "finish",
            "latest_action": record.latest_action,
            "formal_scores": scores,
        }


class ReportGateway(InterviewIOGateway):
    def __init__(self, interview_id, token, owner_id):
        super().__init__(interview_id, connection_id=token, owner_id=owner_id)
        self._command = SimpleNamespace(type="finish", request_id=interview_id)
        self.token = token

    async def _read_snapshot(self):
        return await publication_snapshot(self.interview_id, self.token, self.owner_id)


@sync_to_async
def reserve_publication(interview_id, token, version):
    with transaction.atomic():
        run = locked_run(interview_id, token)
        if run.interview.state_version != version:
            raise IOSafetyError("report version changed")
        if run.report is not None and run.report_version == version:
            return False
        if run.publication_attempts >= PUBLICATION_ATTEMPTS:
            raise IOSafetyError("report release budget exhausted")
        run.publication_attempts += 1
        run.save(update_fields=["publication_attempts"])
        return True


@sync_to_async
def save_report(interview_id, token, owner_id, payload, receipt):
    with transaction.atomic():
        run = locked_run(interview_id, token)
        if (
            run.interview.owner_id != owner_id
            or run.interview.state_version != receipt["state_version"]
            or not ready_for_report(run.interview)
            or receipt
            != make_output_receipt(
                payload, interview_id, receipt["state_version"], review_status=receipt["status"]
            )
        ):
            raise IOSafetyError("report release changed")
        run.report, run.report_receipt = payload, receipt
        run.report_version = receipt["state_version"]
        run.save(update_fields=["report", "report_receipt", "report_version", "updated_at"])


async def publish_report(interview_id, token, repo):
    interview = await AgentInterview.objects.aget(pk=interview_id)
    # Reserve before preparing/checking a report. Crashes consume a bounded attempt,
    # but do not permanently block a replacement worker after its first attempt.
    if not await reserve_publication(interview_id, token, interview.state_version):
        return
    context = await assessed_report_context(repo, await repo.get_interview_context(interview_id))
    records = await repo.get_evaluation_records(interview_id)
    history = [
        {
            "question_id": j.request.question.question_id,
            "question": j.request.question.text,
            "answer": j.request.answer.text,
            "evaluation": j.feedback.model_dump(mode="json"),
        }
        for j in await repo.get_shadow_jobs(interview_id)
    ]
    update_display_history(history, context)
    report = await build_final_report(context, history, formal_records=records)
    payload = {
        "type": "finished",
        "result": {
            "interview_id": interview_id,
            "job_profile": public_job_profile(context.job_profile),
            "question_history": public_question_history(history),
            "final_report": report.model_dump(mode="json"),
            "evaluation_status": "completed_with_errors"
            if report.unscored_answer_count
            else "completed",
        },
    }
    gateway = ReportGateway(interview_id, token, interview.owner_id)

    async def deliver(body, receipt):
        # Retry only the atomic write, keeping the exact approved body/receipt in memory.
        # An enabled safety review is not repeated for a transient storage failure.
        for attempt in range(STORAGE_ATTEMPTS):
            try:
                return await save_report(interview_id, token, interview.owner_id, body, receipt)
            except (OperationalError, InterfaceError):
                if attempt + 1 == STORAGE_ATTEMPTS:
                    raise
                await asyncio.sleep(0.25 * (2**attempt))

    try:
        await gateway.publish(payload, deliver)
    finally:
        await gateway.aclose()


async def scoring_complete(repo, interview_id, jobs):
    records = {r.input.request_id for r in await repo.get_evaluation_records(interview_id)}
    assessments = {r.request_id for r in await repo.get_assessment_records(interview_id)}
    return all(
        (not j.shadow_enabled or j.request.request_id in records)
        and (not j.assessment_requested or j.request.request_id in assessments)
        for j in jobs
    )


async def process_one(*, llm_factory=BackendLLM, batch_seconds=120):
    if not 0 < batch_seconds <= 120:
        raise ValueError("batch_seconds must be between 0 and 120, below the worker lease")
    claimed = await claim_run()
    if claimed is None:
        return False
    interview_id, token = claimed
    repo = DjangoInterviewRepository(interview_id)
    repo.evaluation_lease = token
    llm = adapter = None
    status = "failed"
    publishing = False
    retry_delay = 0
    try:
        jobs = await repo.get_shadow_jobs(interview_id)
        complete = await scoring_complete(repo, interview_id, jobs)
        if not complete:
            llm = llm_factory(interview_id=interview_id)
            adapter = build_evaluation_adapter(
                llm, repo, None, mode="shadow" if any(j.shadow_enabled for j in jobs) else "legacy"
            )
            complete = await adapter.drain(interview_id, timeout_seconds=batch_seconds)
            # A new committed answer may arrive while closing the previous batch.
            await adapter.close()
        jobs = await repo.get_shadow_jobs(interview_id)
        complete = complete and await scoring_complete(repo, interview_id, jobs)
        interview = await AgentInterview.objects.aget(pk=interview_id)
        if complete and ready_for_report(interview):
            publishing = True
            await publish_report(interview_id, token, repo)
            status = "completed"
        else:
            status = "idle" if complete else "pending"
    except asyncio.CancelledError:
        status = "pending"
        raise
    except AgentInterview.DoesNotExist:
        pass  # Explicit discard wins; never recreate it or retry a model for it.
    except Exception as error:
        if publishing and isinstance(error, (OperationalError, InterfaceError)):
            run = await AgentEvaluationRun.objects.aget(pk=interview_id)
            if run.publication_attempts < PUBLICATION_ATTEMPTS:
                status = "pending"
                retry_delay = min(30, 2 ** (run.publication_attempts + 1))
        logger.error(
            "Evaluation worker failed interview=%s error=%s", interview_id, type(error).__name__
        )
    finally:
        if adapter is not None:
            await adapter.close()
        if llm is not None and hasattr(llm, "close"):
            llm.close()
        await release_run(interview_id, token, status, retry_delay=retry_delay)
    return True


def verified_report(run):
    """Read only: polling never enqueues work or calls a model."""
    if (
        not isinstance(run.report, dict)
        or not isinstance(run.report_receipt, dict)
        or not isinstance(run.report.get("result"), dict)
        or run.report_version != run.interview.state_version
    ):
        return None
    receipt = run.report_receipt
    try:
        request_id = receipt.get("request_id")
        if request_id != str(run.interview_id):
            from .agent_safety import verified_response

            original = run.interview.requests.filter(pk=request_id, status="succeeded").first()
            if original is None or verified_response(original) != run.report:
                return None
        expected = make_output_receipt(
            run.report, request_id, run.report_version, review_status=receipt.get("status")
        )
        return run.report["result"] if receipt == expected else None
    except (KeyError, TypeError, ValueError):
        return None
