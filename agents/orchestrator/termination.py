"""Deterministic interview termination policy."""

from collections.abc import Iterable

from agents.config import AgentSettings, load_agent_settings
from shared.contracts import Competency, InterviewPlan, InterviewStage, InterviewState


class TerminationPolicy:
    def __init__(self, settings: AgentSettings | None = None) -> None:
        self._settings = settings or load_agent_settings()

    def should_finish(
        self,
        state: InterviewState,
        plan: InterviewPlan | None = None,
        required_competencies: Iterable[Competency] | None = None,
    ) -> bool:
        if state.remaining_seconds <= 0 or state.stage == InterviewStage.FINISHED:
            return True
        if plan is None or required_competencies is None:
            return False
        if state.elapsed_seconds < self._settings.minimum_interview_seconds:
            return False
        return all(
            state.competencies[competency].coverage >= plan.target_coverage[competency]
            and state.competencies[competency].confidence >= plan.target_confidence[competency]
            for competency in required_competencies
        )

    @staticmethod
    def reason_code(state: InterviewState) -> str:
        if state.remaining_seconds <= 0:
            return "TIME_EXHAUSTED"
        if state.stage == InterviewStage.FINISHED:
            return "STAGE_FINISHED"
        return "REQUIRED_COMPETENCIES_COMPLETE"
