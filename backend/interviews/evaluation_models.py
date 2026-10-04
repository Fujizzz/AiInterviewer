"""Responsibilities: Store internal scoring receipts, excluded from candidate-facing APIs.
Implementation: Bind each receipt to one accepted answer, request and committed state version.
Related Modules: agent_repository validates/appends; agent_records deletes on explicit discard.
Declaration Index:
- AgentEvaluation: Persist one internal scoring receipt with protected source references.
- AgentEvaluation.Meta: Order receipts and constrain atomic state transitions.
Variable Index:
None

Retention Notes:
One row is an atomic evaluation receipt. Its versioned payload contains exact
sources, relation decisions, all criterion assessments and the full aggregation
record/snapshot, including failed attempts. The repository never updates old rows;
explicit interview discard removes them atomically with the rest of that interview.
"""

from django.db import models
from django.db.models import F, Q


class AgentEvaluation(models.Model):
    """Persist a versioned scoring payload; source rows cannot be deleted while it exists.

    The repository validates payload identity and replay before insertion. Foreign keys and
    version constraints enforce relational identity; this model performs no scoring itself.
    """
    interview = models.ForeignKey(
        "AgentInterview", related_name="evaluations", on_delete=models.CASCADE
    )
    answer = models.OneToOneField(
        "AgentAnswer", related_name="scoring_record", on_delete=models.PROTECT
    )
    feedback_request = models.OneToOneField(
        "AgentRequest", related_name="scoring_record", on_delete=models.PROTECT
    )
    base_state_version = models.PositiveIntegerField()
    committed_state_version = models.PositiveIntegerField()
    snapshot_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Read in commit order, with one receipt per interview version and CAS increment of one."""
        ordering = ["committed_state_version"]
        constraints = [
            models.UniqueConstraint(
                fields=["interview", "committed_state_version"], name="evaluation_turn_version"
            ),
            models.CheckConstraint(
                condition=Q(committed_state_version=F("base_state_version") + 1),
                name="evaluation_base_version",
            ),
        ]
