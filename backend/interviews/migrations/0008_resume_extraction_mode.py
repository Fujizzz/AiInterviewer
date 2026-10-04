"""Responsibilities: Record the extraction mode used for each resume version.
Implementation: Add a nullable display field; historical records remain empty and new parses record
their selected mode.
Related Modules: ResumeVersion and the version parsing API.
Declaration Index:
- Migration: Add the extraction mode field without changing stored resume content.
Variable Index:
None
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Functionality: Add extraction-mode metadata to resume versions.
    Inputs: ResumeVersion schema from migration 0007.
    Outputs: Adds an optional extraction_mode field.
    Logic: Apply one AddField operation.
    Constraints: Existing records remain empty; stored text is not inferred or rewritten.
    """

    dependencies = [
        ("interviews", "0007_resume_versions"),
    ]

    operations = [
        migrations.AddField(
            model_name="resumeversion",
            name="extraction_mode",
            field=models.CharField(blank=True, max_length=16),
        ),
    ]
