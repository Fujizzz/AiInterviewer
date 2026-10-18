"""Responsibilities: Add derived resume edition metadata, editable units, and user-confirmed
recommendation slots.
Implementation: Add protected self-references and JSON fields with empty defaults; existing records
are not inferred or rewritten.
Related Modules: ResumeVersion, resume_editor, and the owner-scoped version API.
Declaration Index:
- Migration: Add edition source relationships and structured edit fields.
Variable Index:
None
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Functionality: Add structured resume edition source and edit metadata.
    Inputs: ResumeVersion schema from migration 0008.
    Outputs: Adds protected self-relations and JSON fields for units and recommendation slots.
    Logic: Apply the declared AddField operations.
    Constraints: No historical values are inferred; reverse migration removes the added metadata.
    """

    dependencies = [
        ("interviews", "0008_resume_extraction_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="resumeversion",
            name="edited_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="derived_versions",
                to="interviews.resumeversion",
            ),
        ),
        migrations.AddField(
            model_name="resumeversion",
            name="recommendation_slots",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="resumeversion",
            name="source_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="editions",
                to="interviews.resumeversion",
            ),
        ),
        migrations.AddField(
            model_name="resumeversion",
            name="units",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
