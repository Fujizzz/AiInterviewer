"""Deterministic stage transition policy."""

from agents.domain.errors import InvalidAgentState
from shared.contracts import InterviewPlan, InterviewStage, InterviewState


class InterviewStageMachine:
    """Advance stages when their cumulative time budget is exhausted."""

    def should_transition(self, state: InterviewState, plan: InterviewPlan) -> bool:
        if state.stage == InterviewStage.FINISHED:
            return False
        return state.elapsed_seconds >= self._stage_deadline(state.stage, plan)

    def next_stage(self, state: InterviewState, plan: InterviewPlan) -> InterviewStage:
        stages = [stage_plan.stage for stage_plan in plan.stages]
        try:
            current_index = stages.index(state.stage)
        except ValueError as error:
            raise InvalidAgentState(
                f"Stage {state.stage.value!r} is not present in interview plan "
                f"{plan.interview_id!r}"
            ) from error
        if current_index + 1 >= len(stages):
            return InterviewStage.FINISHED
        return stages[current_index + 1]

    @staticmethod
    def _stage_deadline(stage: InterviewStage, plan: InterviewPlan) -> int:
        elapsed_budget = 0
        for stage_plan in plan.stages:
            elapsed_budget += stage_plan.budget_seconds
            if stage_plan.stage == stage:
                return elapsed_budget
        raise InvalidAgentState(
            f"Stage {stage.value!r} is not present in interview plan {plan.interview_id!r}"
        )
