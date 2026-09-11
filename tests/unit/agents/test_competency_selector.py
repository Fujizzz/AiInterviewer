from agents.policies import CompetencySelector
from shared.contracts import Competency
from tests.factories import interview_plan, interview_state


def test_low_coverage_competency_has_higher_priority() -> None:
    state = interview_state()
    plan = interview_plan()
    state.competencies[Competency.TECHNICAL_DEPTH].coverage = 0.90
    state.competencies[Competency.TECHNICAL_DEPTH].confidence = 0.90
    state.competencies[Competency.DEBUGGING].coverage = 0.20
    state.competencies[Competency.DEBUGGING].confidence = 0.20

    selection = CompetencySelector().select(state=state, plan=plan)

    assert (
        selection.all_priorities[Competency.DEBUGGING]
        > selection.all_priorities[Competency.TECHNICAL_DEPTH]
    )


def test_recency_penalty_avoids_immediate_reselection_when_priorities_are_close() -> None:
    state = interview_state()
    plan = interview_plan()
    for competency_state in state.competencies.values():
        competency_state.coverage = 0.6
        competency_state.confidence = 0.6
        competency_state.evidence_count = 1
    state.question_index = 4
    state.competencies[Competency.DEBUGGING].last_asked_at_question_index = 4
    plan.competency_importance[Competency.DEBUGGING] = 0.81
    plan.competency_importance[Competency.OWNERSHIP] = 0.80

    selection = CompetencySelector().select(state=state, plan=plan)

    assert selection.competency != Competency.DEBUGGING
    assert (
        selection.all_priorities[Competency.OWNERSHIP]
        > selection.all_priorities[Competency.DEBUGGING]
    )
