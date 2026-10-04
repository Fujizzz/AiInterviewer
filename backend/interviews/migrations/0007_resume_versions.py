"""Responsibilities: Add resume version storage and bind interviews to a version snapshot.
Implementation: Add private PDF/text storage, current-version constraints, and protected interview
references; old interviews remain unbound.
Related Modules: ResumeVersion, AgentInterview, account ownership migration, and the configured
authentication model.
Declaration Index:
- Migration: Define the resume version table, interview fields, and status constraints.
Variable Index:
None
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Functionality: Add resume version storage and interview snapshot binding.
    Inputs: Existing Agent interview schema and configured authentication model.
    Outputs: Creates ResumeVersion and adds resume snapshot and protected version reference fields.
    Logic: Apply schema operations in dependency order.
    Constraints: Existing interviews are not backfilled and retain their prior state.
    """

    dependencies = [
        ("interviews", "0006_account_ownership"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="agentinterview",
            name="resume_text_snapshot",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="ResumeVersion",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("label", models.CharField(blank=True, max_length=120)),
                ("original_name", models.CharField(blank=True, max_length=255)),
                ("original_pdf", models.BinaryField(null=True)),
                ("text", models.TextField(blank=True)),
                ("status", models.CharField(default="uploaded", max_length=16)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("is_current", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="agentinterview",
            name="resume_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="interviews",
                to="interviews.resumeversion",
            ),
        ),
        migrations.AddConstraint(
            model_name="resumeversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_current", True)),
                fields=("owner",),
                name="resume_one_current",
            ),
        ),
        migrations.AddConstraint(
            model_name="resumeversion",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "status__in",
                        ["uploaded", "parsing", "ready", "failed", "interrupted"],
                    )
                ),
                name="resume_valid_status",
            ),
        ),
    ]
