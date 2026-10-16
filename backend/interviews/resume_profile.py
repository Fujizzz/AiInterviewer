"""Prepare immutable resume versions once, then load the canonical Agent profile without LLM I/O.

The cache belongs to the resume owner/version. Its text hash and parser version are checked before
use; the candidate ID is rebound to each interview for isolation. No database lock spans model I/O.
"""

import hashlib
import logging
from datetime import timedelta

from asgiref.sync import async_to_sync, sync_to_async
from django.utils import timezone
from pydantic import BaseModel, ConfigDict

from app.parsing.resume import parse_resume_profile
from app.providers.llm import OpenAILLM
from shared.contracts import CandidateProfile

from .resume_models import INTERVIEW_PROFILE_VERSION, ResumeVersion

logger = logging.getLogger(__name__)
# Explicit retry can reclaim a crashed worker; this exceeds both parser call deadlines combined.
PROFILE_LEASE = timedelta(minutes=10)


class StoredResumeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_profile: CandidateProfile
    candidate_name: str | None = None


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_profile(version, *, candidate_id):
    """Read only; reject unavailable, stale, or malformed caches rather than parsing at startup."""
    if not version.can_interview or version.interview_profile_hash != text_hash(version.text):
        raise ValueError("Resume interview profile is not ready; prepare it in resume management.")
    try:
        stored = StoredResumeProfile.model_validate(version.interview_profile)
        if (
            stored.candidate_profile.contract_version
            != CandidateProfile.model_fields["contract_version"].default
        ):
            raise ValueError("Outdated profile contract")
    except (ValueError, TypeError) as exc:
        raise ValueError("Resume interview profile is invalid; prepare it again.") from exc
    profile = stored.candidate_profile.model_copy(update={"candidate_id": candidate_id}, deep=True)
    return profile, stored.candidate_name


def prepare_profile(version_id, owner_id, *, llm=None):
    """Prepare text-ready input; atomic claims prevent duplicate model calls. Failures retain text.

    Called after text upload, PDF extraction, edition save, or explicit retry/backfill. Returns the
    persisted version, including a safe failure code. Injected providers remain caller-owned.
    """
    version = ResumeVersion.objects.defer("original_pdf", "units", "recommendation_slots").get(
        pk=version_id, owner_id=owner_id
    )
    if version.status != "ready":
        return version
    try:
        read_profile(version, candidate_id=f"resume-{version.pk}")
    except ValueError:
        pass
    else:
        from .question_preparation import schedule_question_preparation

        schedule_question_preparation(version.pk, owner_id)
        return version
    now = timezone.now()
    if (
        version.interview_profile_status == "processing"
        and version.updated_at > now - PROFILE_LEASE
    ):
        return version
    fingerprint = text_hash(version.text)
    claimed = ResumeVersion.objects.filter(
        pk=version.pk,
        status="ready",
        interview_profile_status=version.interview_profile_status,
        interview_profile_hash=version.interview_profile_hash,
        interview_profile_version=version.interview_profile_version,
        updated_at=version.updated_at,
    ).update(
        interview_profile_status="processing",
        interview_profile=None,
        interview_profile_hash=fingerprint,
        interview_profile_version=INTERVIEW_PROFILE_VERSION,
        interview_profile_error="",
        updated_at=now,
    )
    if not claimed:
        version.refresh_from_db()
        return version
    claim = ResumeVersion.objects.filter(
        pk=version.pk,
        interview_profile_status="processing",
        updated_at=now,
        interview_profile_hash=fingerprint,
        interview_profile_version=INTERVIEW_PROFILE_VERSION,
    )
    provider = llm
    try:
        provider = llm if llm is not None else OpenAILLM()
        profile, name = async_to_sync(parse_resume_profile)(
            version.text, llm=provider, candidate_id=f"resume-{version.pk}"
        )
        stored = StoredResumeProfile(candidate_profile=profile, candidate_name=name)
        claim.update(
            interview_profile=stored.model_dump(mode="json"),
            interview_profile_status="ready",
            interview_profile_error="",
            updated_at=timezone.now(),
        )
        logger.info("Resume profile ready version=%s", version.pk)
        from .question_preparation import schedule_question_preparation

        schedule_question_preparation(version.pk, owner_id)
    except Exception as exc:
        claim.update(
            interview_profile_status="failed",
            interview_profile_error="resume_profile_failed",
            updated_at=timezone.now(),
        )
        logger.warning(
            "Resume profile failed version=%s exception=%s", version.pk, type(exc).__name__
        )
    except BaseException:
        claim.update(
            interview_profile_status="interrupted",
            interview_profile_error="resume_profile_interrupted",
            updated_at=timezone.now(),
        )
        raise
    finally:
        if llm is None and provider is not None:
            try:
                client = getattr(provider, "client", provider)
                close = getattr(client, "close", None)
                if close is not None:
                    close()
            except Exception as exc:
                logger.warning("Resume provider cleanup failed exception=%s", type(exc).__name__)
    version.refresh_from_db()
    return version


@sync_to_async
def load_bound_profile(interview_id, resume_text):
    """Trust only the owner-checked version binding persisted before the Agent request starts."""
    from .agent_models import AgentInterview

    interview = (
        AgentInterview.objects.select_related("resume_version")
        .defer(
            "resume_version__original_pdf",
            "resume_version__units",
            "resume_version__recommendation_slots",
            "context",
        )
        .filter(pk=interview_id)
        .first()
    )
    if interview is None or interview.resume_version_id is None:
        return None  # Legacy raw-text/CLI flow retains its existing parser.
    version = interview.resume_version
    if (
        interview.owner_id != version.owner_id
        or resume_text != interview.resume_text_snapshot
        or resume_text != version.text
    ):
        raise ValueError("Resume version binding changed")
    return read_profile(version, candidate_id=f"candidate-{interview_id}")
