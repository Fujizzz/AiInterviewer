"""Responsibilities: Permit explicit early-finish and silent-answer requests in persisted history.
Implementation: Replace the request-kind constraint without changing existing records or scores.
Related Modules: AgentRequest and agent_socket introduce finish/skip; discard deletes its session.
Declaration Index:
- Migration: Declare the reversible request-kind constraint extension.
Variable Index:
None
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Functionality: Extend accepted request kinds to include finish and skip.
    Logic: Replace the named check constraint; existing prepare/start/answer rows remain valid.
    Constraints: No data mutation. Reversing requires removing finish/skip records beforehand.
    Class configuration: dependencies targets resume editions; operations replaces only one check.
    """

    dependencies = [("interviews", "0009_resume_editions")]
    operations = [
        migrations.RemoveConstraint(model_name="agentrequest", name="agent_request_kind"),
        migrations.AddConstraint(
            model_name="agentrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(kind__in=["prepare", "start", "answer", "skip", "finish"]),
                name="agent_request_kind",
            ),
        ),
    ]
