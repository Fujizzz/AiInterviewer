"""Responsibilities: Add the internal evaluation audit ledger without rewriting old interviews.
Implementation: Create AgentEvaluation and its source-identity/state-version constraints.
Related Modules: evaluation_models defines the current ORM model; 0011 joins the two 0010 leaves.
Declaration Index:
- Migration: Create protected scoring receipts after the resume-editions schema.
Variable Index:
None
"""

import django.db.models.deletion
import django.db.models.expressions
from django.db import migrations, models


class Migration(migrations.Migration):
    """Create the scoring table; backwards migration removes it and its receipts.

    Existing answer/context data is unchanged. The independent automatic-end migration is
    joined by 0011 rather than renumbered, preserving either branch's applied migration history.
    """
    dependencies = [
        ("interviews", "0009_resume_editions"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentEvaluation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("base_state_version", models.PositiveIntegerField()),
                ("committed_state_version", models.PositiveIntegerField()),
                (
                    "snapshot_id",
                    models.CharField(blank=True, max_length=255, null=True, unique=True),
                ),
                ("payload", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "answer",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="scoring_record",
                        to="interviews.agentanswer",
                    ),
                ),
                (
                    "feedback_request",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="scoring_record",
                        to="interviews.agentrequest",
                    ),
                ),
                (
                    "interview",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="evaluations",
                        to="interviews.agentinterview",
                    ),
                ),
            ],
            options={
                "ordering": ["committed_state_version"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("interview", "committed_state_version"),
                        name="evaluation_turn_version",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "committed_state_version",
                                django.db.models.expressions.CombinedExpression(
                                    models.F("base_state_version"), "+", models.Value(1)
                                ),
                            )
                        ),
                        name="evaluation_base_version",
                    ),
                ],
            },
        ),
    ]
