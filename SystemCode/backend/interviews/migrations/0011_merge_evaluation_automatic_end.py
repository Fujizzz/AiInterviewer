"""Responsibilities: Join the independent scoring and automatic-end migration branches.
Implementation: Require both existing 0010 migrations without changing their operations.
Related Modules: AgentEvaluation stores receipts; AgentRequest accepts skip/finish commands.
Declaration Index:
- Migration: Join both leaves so fresh and previously migrated databases can upgrade.
Variable Index:
None
"""

from django.db import migrations


class Migration(migrations.Migration):
    """Require both additive schema changes; no data or schema operation is repeated.

    Either 0010 may already be applied. Django runs only the missing branch before
    recording this merge; reversing the merge itself leaves both branches intact.
    """

    dependencies = [
        ("interviews", "0010_agent_automatic_end"),
        ("interviews", "0010_evaluation_ledger"),
    ]
    operations = []
