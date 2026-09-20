import pytest

from agents.policies import DifficultyController
from shared.contracts import AnswerAnalysis, EvaluationFeedback


@pytest.mark.parametrize(
    "current,status,strength,relevance,expected",
    [
        (3, "substantive", 0.9, 0.9, 4),
        (5, "substantive", 0.9, 0.9, 5),
        (3, "partial", 0.2, 0.9, 2),
        (1, "non_answer", 0, 0, 1),
        (3, "substantive", 0.2, 0.2, 3),
    ],
)
def test_difficulty_uses_response_quality(current, status, strength, relevance, expected):
    feedback = EvaluationFeedback(
        request_id="r",
        question_id="q",
        evidence_strength=strength,
        answer_relevance=relevance,
        analysis=AnswerAnalysis(status=status),
    )
    assert DifficultyController().adjust(current, feedback) == expected
