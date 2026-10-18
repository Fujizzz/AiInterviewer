"""Responsibilities: Make request history indexing cover the complete stable pagination order.
Implementation: Replace the two-column request index with a composite index on interview, creation
time, and identifier.
Related Modules: AgentRequest metadata and migration 0004_agent_persistence.
Declaration Index:
- Migration: Declare the reversible request-history index replacement.
Variable Index:
None
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Functionality: Replace the request history index with the full stable sort order.
    Inputs: Schema from migration 0004.
    Outputs: Adds an index over interview, created_at, and id.
    Logic: Remove the previous index before adding its replacement.
    Constraints: No business rows or request state are changed.
    """

    dependencies = [
        ("interviews", "0004_agent_persistence"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="agentrequest",
            name="agent_request_history",
        ),
        migrations.AddIndex(
            model_name="agentrequest",
            index=models.Index(
                fields=["interview", "created_at", "id"], name="agent_request_history"
            ),
        ),
    ]
