"""Deterministic probe continuation policy."""

from agents.config import AgentSettings, load_agent_settings
from agents.domain.models import ProbeDecision
from shared.contracts import Competency, InterviewPlan, InterviewState


class ProbeController:
    def __init__(self, settings: AgentSettings | None = None) -> None:
        self._settings = settings or load_agent_settings()

    def decide(
        self,
        *,
        competency: Competency,
        state: InterviewState,
        plan: InterviewPlan,
    ) -> ProbeDecision:
        if state.consecutive_probes >= plan.max_consecutive_probes:
            return ProbeDecision(
                should_probe=False,
                next_probe_depth=state.consecutive_probes,
                reason_code="MAX_PROBES_REACHED",
            )
        if state.remaining_seconds <= self._settings.probe.minimum_remaining_seconds:
            return ProbeDecision(
                should_probe=False,
                next_probe_depth=state.consecutive_probes,
                reason_code="TIME_LIMIT",
            )

        competency_state = state.competencies[competency]
        coverage_complete = competency_state.coverage >= plan.target_coverage[competency]
        confidence_complete = competency_state.confidence >= plan.target_confidence[competency]
        if coverage_complete and confidence_complete:
            return ProbeDecision(
                should_probe=False,
                next_probe_depth=state.consecutive_probes,
                reason_code="COMPETENCY_COMPLETE",
            )

        should_probe = not coverage_complete and not confidence_complete
        return ProbeDecision(
            should_probe=should_probe,
            next_probe_depth=state.consecutive_probes + 1 if should_probe else 0,
            reason_code="TARGET_NOT_REACHED" if should_probe else "INSUFFICIENT_EVIDENCE",
        )
