import pytest

from agents.policies import DifficultyController
from shared.contracts import Competency, CompetencyState, EvaluationFeedback


def feedback(
    *,
    evidence_strength: float,
    evaluation_confidence: float,
    answer_relevance: float,
) -> EvaluationFeedback:
    return EvaluationFeedback(
        request_id="feedback-1",
        question_id="question-1",
        target_competency=Competency.DEBUGGING,
        answer_relevance=answer_relevance,
        evidence_strength=evidence_strength,
        evaluation_confidence=evaluation_confidence,
        rubric_level=3,
        updated_competency_state=CompetencyState(competency=Competency.DEBUGGING),
    )


@pytest.mark.parametrize(
    ("current", "expected"),
    [(3, 4), (5, 5)],
)
def test_strong_relevant_feedback_increases_difficulty_with_upper_bound(
    current: int,
    expected: int,
) -> None:
    strong_feedback = feedback(
        evidence_strength=0.9,
        evaluation_confidence=0.9,
        answer_relevance=0.9,
    )

    assert DifficultyController().adjust(current, strong_feedback) == expected


def test_weak_relevant_feedback_decreases_difficulty() -> None:
    weak_feedback = feedback(
        evidence_strength=0.2,
        evaluation_confidence=0.8,
        answer_relevance=0.9,
    )

    assert DifficultyController().adjust(3, weak_feedback) == 2


def test_low_relevance_does_not_decrease_difficulty() -> None:
    low_relevance_feedback = feedback(
        evidence_strength=0.2,
        evaluation_confidence=0.8,
        answer_relevance=0.2,
    )

    assert DifficultyController().adjust(3, low_relevance_feedback) == 3
