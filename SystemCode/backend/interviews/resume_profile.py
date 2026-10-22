"""Responsibilities: Persist and load one canonical structured document per immutable resume.
Implementation: Claim extraction without holding database locks, validate shared output, apply
explicit user overrides and publish once. Every downstream consumer uses read_document/projections.
Related Modules: app.parsing.resume owns model/extraction contracts; resume_versions exposes APIs;
question_preparation and bound interviews consume deterministic CandidateProfile projections.
Declaration Index:
- StoredResumeDocument: Canonical output plus explicit user-confirmation field names.
- text_hash: Fingerprint exact source text.
- read_document: Validate and load the sole structured resume record.
- read_profile: Project the canonical document for an interview-specific candidate ID.
- prepare_profile: Claim, extract and persist a canonical original or authored edition.
- load_bound_profile: Read the owner/version-bound profile without model calls.
Variable Index:
- logger: Logs lifecycle, version IDs and safe failure categories without source content.
- PROFILE_LEASE: Explicit retry age for a crashed processing claim.
Constraints:
Original text/PDF and authored inputs stay immutable. No independent section/slot parser or silent
fallback remains. User-confirmed values override the same canonical fields, including null and [].
"""

import hashlib
import logging
from datetime import timedelta

from asgiref.sync import async_to_sync, sync_to_async
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from app.parsing.resume import document_profile, parse_resume_document, validate_document
from app.providers.llm import OpenAILLM
from shared.resume_contract import ResumeExtraction, ResumeFacts

from .resume_models import INTERVIEW_PROFILE_VERSION, ResumeVersion

logger = logging.getLogger(__name__)
# Explicit retry can reclaim a crashed worker; this exceeds the single canonical extraction
# deadline.
PROFILE_LEASE = timedelta(minutes=10)


class StoredResumeDocument(BaseModel):
    """Functionality: Store canonical model data and explicit user-confirmation provenance.
    Logic: document holds the shared typed output; confirmed_fields names authoritative user values.
    Constraints: Extra wrapper fields are rejected; no duplicate CandidateProfile is persisted.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    document: ResumeExtraction
    confirmed_fields: list[str] = Field(default_factory=list)


def text_hash(text):
    """Functionality: Fingerprint immutable source text for cache binding.
    Inputs: Source string. Outputs: SHA-256 hex digest.
    Logic: Hash UTF-8 bytes without normalization. Constraints: No side effects or model calls.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_document(version):
    """Functionality: Load the sole structured source used by editor, recommendation and interviews.
    Inputs: Resume version including cached JSON and source text.
    Outputs: Validated ResumeExtraction; stale, missing or malformed caches raise ValueError.
    Logic: Check lifecycle, schema version, text hash, source coverage and confirmation provenance.
    Constraints: No model I/O, retry, rule extraction or writes; unknown values stay unknown.
    """
    if not version.has_structured_resume or version.interview_profile_hash != text_hash(
        version.text
    ):
        raise ValueError("Resume structure is not ready; prepare it in resume management.")
    stored = StoredResumeDocument.model_validate(version.interview_profile)
    if set(stored.confirmed_fields) - set(ResumeFacts.model_fields):
        raise ValueError("Invalid confirmed resume fields")
    validate_document(stored.document, version.text, confirmed_fields=stored.confirmed_fields)
    return stored.document


def read_profile(version, *, candidate_id):
    """Functionality: Adapt the shared canonical document for a bound interview.
    Inputs: Owned version and interview-specific candidate identifier.
    Outputs: CandidateProfile and name derived deterministically from the canonical document.
    Logic: Validate the cache, then project identical global skills and project facts.
    Constraints: No independent extraction or provider I/O; invalid/uninterviewable input fails.
    """
    return document_profile(read_document(version), version.text, candidate_id=candidate_id)


def prepare_profile(version_id, owner_id, *, llm=None):
    """Prepare text-ready input; atomic claims prevent duplicate model calls. Failures retain text.

    Called after text upload, PDF extraction, edition save, or explicit retry/backfill. Returns the
    persisted version, including a safe failure code. Injected providers remain caller-owned.
    A new canonical claim fences old question-preparation runs and resets their readiness; existing
    rows remain private and require the new profile fingerprint before reuse.
    """
    version = ResumeVersion.objects.defer("original_pdf").get(pk=version_id, owner_id=owner_id)
    if version.status != "ready":
        return version
    try:
        read_document(version)
    except ValueError:
        pass
    else:
        from .question_preparation import schedule_question_preparation

        if version.can_interview:
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
        question_preparation_status="pending",
        question_preparation_error="",
        question_preparation_version="",
        question_preparation_run_id=None,
        question_preparation_started_at=None,
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
        document = async_to_sync(parse_resume_document)(
            version.text, llm=provider, units=version.units if version.source_version_id else None
        )
        confirmed_fields = []
        if version.source_version_id:
            confirmed = ResumeFacts.model_validate(version.recommendation_slots).model_dump()
            confirmed_fields = list(confirmed)
            document = ResumeExtraction.model_validate({**document.model_dump(), **confirmed})
            for field in confirmed_fields:
                setattr(document.field_evidence, field, [])
        stored = StoredResumeDocument(document=document, confirmed_fields=confirmed_fields)
        committed = claim.update(
            interview_profile=stored.model_dump(mode="json"),
            interview_profile_status="ready",
            interview_profile_error="",
            updated_at=timezone.now(),
        )
        if not committed:
            logger.warning("Canonical resume claim superseded version=%s", version.pk)
            version.refresh_from_db()
            return version
        logger.info(
            "Canonical resume ready version=%s confirmed=%d", version.pk, len(confirmed_fields)
        )
        from .question_preparation import schedule_question_preparation

        version.refresh_from_db()
        if version.can_interview:
            schedule_question_preparation(version.pk, owner_id)
    except Exception as exc:
        claim.update(
            interview_profile_status="failed",
            interview_profile_error="resume_profile_failed",
            updated_at=timezone.now(),
        )
        logger.warning(
            "Canonical resume failed version=%s exception=%s code=%s",
            version.pk,
            type(exc).__name__,
            str(exc)
            if isinstance(exc, ValueError) and str(exc).startswith("resume_")
            else "contract_or_provider_error",
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
