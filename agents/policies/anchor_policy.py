"""Comparable anchor policy backed by explicit orchestration state."""

from collections.abc import Mapping

from agents.config import AgentSettings, load_agent_settings
from agents.domain.models import AnchorState
from shared.contracts import Competency, InterviewState


class AnchorPolicy:
    """Read explicit anchor state, with v1.0 evidence fallback for callers."""

    required_competencies = frozenset(
        {
            Competency.OWNERSHIP,
            Competency.DECISION_MAKING,
            Competency.DEBUGGING,
            Competency.EVALUATION,
            Competency.ADAPTABILITY,
        }
    )

    def __init__(self, settings: AgentSettings | None = None) -> None:
        self._settings = settings or load_agent_settings()

    def completion_map(self, state: InterviewState) -> dict[Competency, bool]:
        """Compatibility helper for v1.0 callers without InterviewContext."""

        return {
            competency: state.competencies[competency].evidence_count > 0
            for competency in self.required_competencies
        }

    def is_completed(
        self,
        competency: Competency,
        *,
        state: InterviewState | None = None,
        completed: Mapping[Competency, bool] | None = None,
        anchor_state: Mapping[Competency, AnchorState] | None = None,
    ) -> bool:
        if competency not in self.required_competencies:
            return True
        if anchor_state is not None:
            anchor = anchor_state.get(competency)
            return anchor.completed if anchor is not None else False
        if completed is not None:
            return completed.get(competency, False)
        if state is None:
            return False
        return state.competencies[competency].evidence_count > 0

    def bonus(
        self,
        competency: Competency,
        *,
        state: InterviewState | None = None,
        completed: Mapping[Competency, bool] | None = None,
        anchor_state: Mapping[Competency, AnchorState] | None = None,
    ) -> float:
        if self.is_completed(
            competency,
            state=state,
            completed=completed,
            anchor_state=anchor_state,
        ):
            return 0.0
        return self._settings.competency_selector.anchor_bonus
