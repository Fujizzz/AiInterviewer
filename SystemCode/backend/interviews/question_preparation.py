"""Responsibilities: Prepare version-bound questions and expose owned snapshots to interviews.
Implementation: Dispatch after commit, claim a renewable run, reuse reviewed rows and fence writes.
Related Modules: resume_profile schedules work; agents.question.prepared writes and admits content;
agent_session reads snapshots, and tasks provides optional Celery dispatch.

Declaration Index:
- schedule_question_preparation: Register background work after the resume transaction commits.
- schedule_question_preparation.dispatch: Submit a local or Celery job and record dispatch failure.
- _background: Isolate database connections owned by a local background worker.
- preparation_context: Build an empty dialogue for standalone generation and review.
- prepare_questions: Claim preparation, manage provider ownership and persist aggregate status.
- _prepare: Generate missing opening and knowledge questions with no interview side effects.
- _prepare.existing: Validate an existing reviewed row before reusing it.
- _prepare.store: Fence one row write and renew its worker lease in a short transaction.
- load_bound_questions: Load current reviewed rows through an owned interview/version binding.

Variable Index:
- logger: Emit identifiers and exception categories without resume text or credentials.
- _executor: One local worker for background preparation in this process.
- LEASE: Maximum idle worker interval before explicit retry may reclaim a run.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from agents.domain.errors import ProviderUnavailable
from agents.domain.models import InterviewContext
from agents.policies.topic_selector import TopicSelector
from agents.question.prepared import (
    accepted_review_versions,
    knowledge_material,
    opening_plan,
    prepare_one,
    profile_fingerprint,
    review_version,
)
from app.adapters.llm import ProviderLLMAdapter
from app.adapters.rag import get_shared_rag
from app.providers.llm import OpenAILLM
from app.settings import interview_settings
from shared.contracts import InterviewPlan, InterviewStage, InterviewState, JobProfile
from shared.contracts.prepared import PreparedQuestion

from .resume_models import PreparedResumeQuestion, ResumeVersion
from .resume_profile import read_profile

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="resume-questions")
LEASE = timedelta(minutes=15)


def schedule_question_preparation(version_id, owner_id):
    """Commit first; do not retain a request's model client or hold an upload open for writing."""
    if settings.QUESTION_PREPARATION_EXECUTION == "disabled":
        return

    def dispatch():
        """Submit committed version IDs only; failures retain normal live interview availability."""
        try:
            if settings.QUESTION_PREPARATION_EXECUTION == "celery":
                from .tasks import prepare_questions_task

                prepare_questions_task.delay(str(version_id), owner_id)
            else:
                _executor.submit(_background, str(version_id), owner_id)
        except Exception as exc:
            ResumeVersion.objects.filter(pk=version_id, owner_id=owner_id).update(
                question_preparation_status="failed", question_preparation_error="dispatch_failed"
            )
            logger.warning("Question preparation dispatch failed exception=%s", type(exc).__name__)

    transaction.on_commit(dispatch)


def _background(version_id, owner_id):
    """Run one owned preparation job and close only this thread's database connections."""
    close_old_connections()
    try:
        prepare_questions(version_id, owner_id)
    except Exception as exc:
        logger.warning(
            "Question preparation stopped version=%s exception=%s", version_id, type(exc).__name__
        )
    finally:
        close_old_connections()


def preparation_context(profile):
    """An empty dialogue used only by the existing standalone writer/quality gate."""
    iid = "prepare-" + str(uuid4())
    return InterviewContext(
        interview_id=iid,
        candidate_profile=profile,
        job_profile=JobProfile(
            job_id="preparation", title="Technical interview", competency_importance={}
        ),
        plan=InterviewPlan(interview_id=iid, duration_seconds=3600, stages=[]),
        state=InterviewState(
            interview_id=iid,
            remaining_seconds=3600,
            competencies={},
            stage=InterviewStage.PROJECT_DEEP_DIVE,
        ),
        policy_config_version="question-preparation-v1",
    )


def prepare_questions(version_id, owner_id, *, llm=None, rag=None, include_knowledge=True):
    """Resume successful rows on retry. Atomic lease/run fencing prevents stale writers."""
    version = ResumeVersion.objects.get(pk=version_id, owner_id=owner_id)
    profile, _ = read_profile(version, candidate_id=f"resume-{version_id}")
    revision = review_version()
    fingerprint = profile_fingerprint(profile)
    now = timezone.now()
    if (
        version.question_preparation_status == "processing"
        and version.question_preparation_started_at
        and version.question_preparation_started_at > now - LEASE
    ):
        return version
    run_id = uuid4()
    claimed = ResumeVersion.objects.filter(
        pk=version_id,
        owner_id=owner_id,
        question_preparation_run_id=version.question_preparation_run_id,
        question_preparation_status=version.question_preparation_status,
    ).update(
        question_preparation_status="processing",
        question_preparation_run_id=run_id,
        question_preparation_started_at=now,
        question_preparation_error="",
    )
    if not claimed:
        return ResumeVersion.objects.get(pk=version_id, owner_id=owner_id)
    provider = llm
    try:
        provider = llm if llm is not None else OpenAILLM()
        async_to_sync(_prepare)(
            version_id, run_id, profile, fingerprint, provider, rag, include_knowledge
        )
        statuses = list(
            PreparedResumeQuestion.objects.filter(
                resume_version_id=version_id, profile_hash=fingerprint, review_version=revision
            ).values_list("status", flat=True)
        )
        status = (
            "ready"
            if statuses and all(s == "ready" for s in statuses)
            else ("partial" if "ready" in statuses else "failed")
        )
        ResumeVersion.objects.filter(pk=version_id, question_preparation_run_id=run_id).update(
            question_preparation_status=status,
            question_preparation_version=revision,
            question_preparation_error="" if status == "ready" else "some_questions_unavailable",
        )
    except Exception as exc:
        ResumeVersion.objects.filter(pk=version_id, question_preparation_run_id=run_id).update(
            question_preparation_status="failed",
            question_preparation_error=getattr(exc, "reason_code", type(exc).__name__)[:64],
        )
        logger.warning(
            "Question preparation failed version=%s exception=%s", version_id, type(exc).__name__
        )
    finally:
        if llm is None and provider is not None:
            getattr(provider, "client", provider).close()
    return ResumeVersion.objects.get(pk=version_id, owner_id=owner_id)


async def _prepare(version_id, run_id, profile, fingerprint, provider, rag, include_knowledge):
    """Generate missing reviewed content sequentially; database writes never span model calls."""
    context = preparation_context(profile)
    llm = ProviderLLMAdapter(provider)
    config = interview_settings()
    revision = review_version()

    @sync_to_async
    def existing(project_id, topic_key, kind):
        """Reuse a row only when both its metadata and validated content match the current scope."""
        row = PreparedResumeQuestion.objects.filter(
            resume_version_id=version_id,
            project_id=project_id,
            topic_key=topic_key,
            kind=kind,
            profile_hash=fingerprint,
            review_version=revision,
            status="ready",
        ).first()
        if row is None:
            return False
        try:
            item = PreparedQuestion.model_validate(row.content)
            return (
                item.kind == kind
                and item.project_id == project_id
                and item.topic_key == topic_key
                and item.review_version == revision
            )
        except (ValueError, TypeError):
            return False

    @sync_to_async
    def store(project_id, topic_key, kind, item=None, error="", unavailable=False):
        """Persist one result under its run lease; a newer run makes this transaction fail."""
        with transaction.atomic():
            current = ResumeVersion.objects.select_for_update().get(pk=version_id)
            if current.question_preparation_run_id != run_id:
                raise ValueError("Question preparation lease changed")
            current.question_preparation_started_at = timezone.now()
            current.save(update_fields=["question_preparation_started_at"])
            PreparedResumeQuestion.objects.update_or_create(
                resume_version_id=version_id,
                project_id=project_id,
                topic_key=topic_key,
                kind=kind,
                defaults={
                    "profile_hash": fingerprint,
                    "review_version": revision,
                    "status": "ready" if item else "unavailable" if unavailable else "failed",
                    "content": item.model_dump(mode="json") if item else None,
                    "error_code": error[:128],
                },
            )

    for project in profile.projects:
        topics = TopicSelector().candidates(project)
        for topic in topics:
            if await existing(project.project_id, topic.topic_key, "opening"):
                continue
            try:
                item = await prepare_one(opening_plan(project, topic), context, llm, config)
            except Exception as exc:
                await store(
                    project.project_id,
                    topic.topic_key,
                    "opening",
                    error=getattr(exc, "reason_code", type(exc).__name__),
                )
                if isinstance(exc, ProviderUnavailable):
                    raise
            else:
                await store(project.project_id, topic.topic_key, "opening", item)
        if not include_knowledge or not topics:
            continue
        if await existing(project.project_id, topics[0].topic_key, "knowledge"):
            continue
        try:
            if rag is None:
                rag = await get_shared_rag()
            plan, evidence = await knowledge_material(project, context, rag, config)
            if plan is None:
                await store(
                    project.project_id,
                    topics[0].topic_key,
                    "knowledge",
                    error="NO_USABLE_EVIDENCE",
                    unavailable=True,
                )
                continue
            item = await prepare_one(
                plan, context, llm, config, kind="knowledge", evidence=evidence
            )
        except Exception as exc:
            await store(
                project.project_id,
                topics[0].topic_key,
                "knowledge",
                error=getattr(exc, "reason_code", type(exc).__name__),
            )
            if isinstance(exc, ProviderUnavailable):
                raise
        else:
            await store(project.project_id, topics[0].topic_key, "knowledge", item)


@sync_to_async
def load_bound_questions(interview_id):
    """Read only owned, current, fully reviewed rows. Malformed rows use the normal writer."""
    from .agent_models import AgentInterview

    interview = (
        AgentInterview.objects.select_related("resume_version").filter(pk=interview_id).first()
    )
    if not interview or not interview.resume_version_id:
        return []
    version = interview.resume_version
    if interview.owner_id != version.owner_id or version.text != interview.resume_text_snapshot:
        raise ValueError("Resume version binding changed")
    profile, _ = read_profile(version, candidate_id=f"candidate-{interview_id}")
    fingerprint = profile_fingerprint(profile)
    allowed = {
        t.topic_key: p.project_id for p in profile.projects for t in TopicSelector().candidates(p)
    }
    result = []
    for row in version.prepared_questions.filter(
        status="ready", profile_hash=fingerprint, review_version__in=accepted_review_versions()
    ).order_by("project_id", "topic_key", "kind"):
        try:
            item = PreparedQuestion.model_validate(row.content)
            if (
                item.review_version != row.review_version
                or item.kind != row.kind
                or item.project_id != row.project_id
                or item.topic_key != row.topic_key
                or allowed.get(item.topic_key) != item.project_id
            ):
                continue
            result.append(item)
        except (ValueError, TypeError):
            continue
    return result
