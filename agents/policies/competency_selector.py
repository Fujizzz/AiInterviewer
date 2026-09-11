"""Explainable competency priority calculation."""

from collections.abc import Mapping

from agents.config import AgentSettings, load_agent_settings
from agents.domain.models import AnchorState, CompetencySelection
from agents.policies.anchor_policy import AnchorPolicy
from shared.contracts import Competency, InterviewPlan, InterviewState


def recency_penalty(current_index: int, last_index: int | None) -> float:
    """Return the documented discrete penalty for recently asked competencies."""

    if last_index is None:
        return 0.0
    distance = current_index - last_index
    if distance <= 1:
        return 1.0
    if distance == 2:
        return 0.5
    return 0.0


class CompetencySelector:
    def __init__(
        self,
        settings: AgentSettings | None = None,
        anchor_policy: AnchorPolicy | None = None,
    ) -> None:
        self._settings = settings or load_agent_settings()
        self._anchor_policy = anchor_policy or AnchorPolicy(self._settings)

    def select(
        self,
        *,
        state: InterviewState,
        plan: InterviewPlan,
        stage_fit: Mapping[Competency, float] | None = None,
        anchor_state: Mapping[Competency, AnchorState] | None = None,
    ) -> CompetencySelection:
        weights = self._settings.competency_selector
        priorities: dict[Competency, float] = {}
        for competency in Competency:
            competency_state = state.competencies[competency]
            fit = (
                stage_fit.get(competency, self._settings.default_stage_fit)
                if stage_fit is not None
                else self._settings.default_stage_fit
            )
            priorities[competency] = (
                weights.importance_weight * plan.competency_importance.get(competency, 0.0)
                + weights.coverage_weight * (1.0 - competency_state.coverage)
                + weights.confidence_weight * (1.0 - competency_state.confidence)
                + weights.stage_fit_weight * fit
                - weights.recency_penalty_weight
                * recency_penalty(
                    state.question_index,
                    competency_state.last_asked_at_question_index,
                )
                + self._anchor_policy.bonus(
                    competency,
                    state=state,
                    anchor_state=anchor_state,
                )
            )

        selected = max(Competency, key=lambda competency: priorities[competency])
        return CompetencySelection(
            competency=selected,
            priority=priorities[selected],
            reason_code=self._reason_code(state, plan, selected, anchor_state),
            all_priorities=priorities,
        )

    def _reason_code(
        self,
        state: InterviewState,
        plan: InterviewPlan,
        competency: Competency,
        anchor_state: Mapping[Competency, AnchorState] | None,
    ) -> str:
        competency_state = state.competencies[competency]
        if not self._anchor_policy.is_completed(
            competency,
            state=state,
            anchor_state=anchor_state,
        ):
            return "ANCHOR_NOT_COMPLETED"
        if competency_state.coverage < plan.target_coverage[competency]:
            if competency_state.confidence < plan.target_confidence[competency]:
                return "LOW_COVERAGE_LOW_CONFIDENCE"
            return "LOW_COVERAGE_HIGH_IMPORTANCE"
        if competency_state.confidence < plan.target_confidence[competency]:
            return "LOW_CONFIDENCE"
        return "HIGHEST_PRIORITY"
