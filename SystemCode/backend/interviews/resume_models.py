"""Responsibilities: Store immutable input versions of user's resume and explicit parsing lifecycle.
Implementation: Original file stored in database, avoiding public media paths; neither text nor file
overwrites historical versions.
Related Modules: resume_versions provides authorized API; AgentInterview fixes references and data
snapshots.
Declaration Index:
- ResumeVersion: Stores input, parsing results, current selection, and failure codes.
- ResumeVersion.Meta: Sorts by creation time and limits each user to at most one current version.
- ResumeVersion.has_structured_resume: Expose versioned canonical-document readiness.
- ResumeVersion.can_interview: Expose canonical interviewable facts independently of question
  preparation.
- PreparedResumeQuestion: Store private version-bound reviewed questions and their references.
- PreparedResumeQuestion.Meta: Enforce one row per scope and valid kind/status values.
Variable Index:
- INTERVIEW_PROFILE_VERSION: Canonical parser/schema version required for profile reuse.

Field and State Notes:
ResumeVersion.id is the version UUID and owner is the owning user; label is the display label.
original_name/original_pdf preserve the original PDF name and private bytes; text stores input or
parsed text.
status/error_code record parsing lifecycle and a fixed error code; is_current selects the current
version.
extraction_mode records the selected mode during parsing and is empty before parsing starts;
created_at/updated_at are audit timestamps.
Meta.constraints defines lifecycle and current-version uniqueness constraints.
source_version references the root original version; edited_from references the edit baseline; both
use PROTECT so referenced versions cannot be deleted.
units stores user-authored section text; recommendation_slots stores confirmed recommendation
fields; originals initialize these JSON fields as empty dictionaries.
interview_profile stores the sole canonical ResumeExtraction document and confirmation provenance;
its separate status/error, source hash and parser version control all downstream structured reads.
units/recommendation_slots preserve authored inputs, not independent downstream extraction results.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

INTERVIEW_PROFILE_VERSION = "4.0:source-complete-resume-v2"


class ResumeVersion(models.Model):
    """Function: Persist version; input is bounded PDF or text from authorized API, output is
    database record.
    Logic: PDF moves from uploaded through parsing to ready/failed/interrupted; text goes directly
    to ready.
    Unit edits save new ready snapshots, preserving original root and baseline.
    Constraints: owner must not be empty, original file and input are immutable, current flag does
    not alter version content; parsing does not auto-retry.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    label = models.CharField(max_length=120, blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    original_pdf = models.BinaryField(null=True, editable=False)
    source_version = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="editions"
    )
    edited_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="derived_versions"
    )
    units = models.JSONField(default=dict, blank=True)
    recommendation_slots = models.JSONField(default=dict, blank=True)
    text = models.TextField(blank=True)
    status = models.CharField(max_length=16, default="uploaded")
    extraction_mode = models.CharField(max_length=16, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    interview_profile = models.JSONField(null=True, blank=True)
    interview_profile_status = models.CharField(max_length=16, default="pending")
    interview_profile_hash = models.CharField(max_length=64, blank=True)
    interview_profile_version = models.CharField(max_length=64, blank=True)
    interview_profile_error = models.CharField(max_length=64, blank=True)
    question_preparation_status = models.CharField(max_length=16, default="pending")
    question_preparation_version = models.CharField(max_length=64, blank=True)
    question_preparation_error = models.CharField(max_length=64, blank=True)
    question_preparation_run_id = models.UUIDField(null=True, editable=False)
    question_preparation_started_at = models.DateTimeField(null=True, editable=False)
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def has_structured_resume(self):
        """Expose cache lifecycle/schema readiness; consumers validate payload and source hash."""
        return (
            self.status == "ready"
            and self.interview_profile_status == "ready"
            and self.interview_profile_version == INTERVIEW_PROFILE_VERSION
        )

    @property
    def can_interview(self):
        """Expose interviewable canonical facts without parsing or a model call.
        Inputs: Canonical readiness and stored document JSON. Outputs: Boolean.
        Logic: Require known global skills or grounded experiences in the versioned document.
        Constraints: Full schema/hash validation remains mandatory at the consumer boundary.
        """
        stored = self.interview_profile
        document = stored.get("document") if isinstance(stored, dict) else None
        if not isinstance(document, dict):
            return False
        return self.has_structured_resume and bool(
            document.get("skills") or document.get("projects")
        )

    class Meta:
        """Function: Sorting and state constraint; logic: conditional uniqueness constraint limits
        current selection; constraint: versions are not merged by content.
        """

        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner"], condition=Q(is_current=True), name="resume_one_current"
            ),
            models.CheckConstraint(
                condition=Q(status__in=["uploaded", "parsing", "ready", "failed", "interrupted"]),
                name="resume_valid_status",
            ),
            models.CheckConstraint(
                condition=Q(
                    interview_profile_status__in=[
                        "pending",
                        "processing",
                        "ready",
                        "failed",
                        "interrupted",
                    ]
                ),
                name="resume_valid_profile_status",
            ),
        ]


class PreparedResumeQuestion(models.Model):
    """Private, version-bound standalone content; no interview or answer IDs are cached."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resume_version = models.ForeignKey(
        ResumeVersion, on_delete=models.CASCADE, related_name="prepared_questions"
    )
    project_id = models.CharField(max_length=255)
    topic_key = models.TextField()
    kind = models.CharField(max_length=16)
    status = models.CharField(max_length=16, default="pending")
    profile_hash = models.CharField(max_length=64)
    review_version = models.CharField(max_length=64)
    content = models.JSONField(null=True, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Keep reviewed content unique within a resume's project/topic and question kind."""

        constraints = [
            models.UniqueConstraint(
                fields=["resume_version", "project_id", "topic_key", "kind"],
                name="prepared_resume_question_scope",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["pending", "ready", "failed", "unavailable"]),
                name="prepared_question_valid_status",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=["opening", "knowledge"]),
                name="prepared_question_valid_kind",
            ),
        ]
