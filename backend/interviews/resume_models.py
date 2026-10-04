"""Responsibilities: Store immutable input versions of user's resume and explicit parsing lifecycle.
Implementation: Original file stored in database, avoiding public media paths; neither text nor file
overwrites historical versions.
Related Modules: resume_versions provides authorized API; AgentInterview fixes references and data
snapshots.
Declaration Index:
- ResumeVersion: Stores input, parsing results, current selection, and failure codes.
- ResumeVersion.Meta: Sorts by creation time and limits each user to at most one current version.
Variable Index:
None

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
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


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
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
        ]
