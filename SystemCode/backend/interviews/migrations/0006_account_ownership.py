"""Responsibilities: Add optional user ownership to interview and practice sessions.
Implementation: Add nullable protected foreign keys; existing records remain unowned and are not
rewritten.
Related Modules: The configured authentication user model, AgentInterview, and PracticeSession.
Declaration Index:
- Migration: Add both nullable ownership fields without backfilling old records.
Variable Index:
None
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Functionality: Add user ownership fields to Agent and practice sessions.
    Inputs: Existing session tables and the configured authentication user model.
    Outputs: Adds nullable protected foreign keys.
    Logic: Apply two AddField operations.
    Constraints: Existing rows remain unowned; data and evaluation state are unchanged.
    """

    dependencies = [
        ("interviews", "0005_agent_request_order"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="agentinterview",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="agent_interviews",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="practicesession",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="practice_sessions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
