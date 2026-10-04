"""Internal append-only Evaluation ledger, excluded from candidate-facing APIs.

One row is an atomic evaluation receipt. Its versioned payload contains exact
sources, relation decisions, all criterion assessments and the full aggregation
record/snapshot, including failed attempts. Old rows are never updated by the
repository; a new snapshot is a new row linked through supersedes_snapshot_id.
"""

from django.db import models
from django.db.models import F, Q


class AgentEvaluation(models.Model):
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
