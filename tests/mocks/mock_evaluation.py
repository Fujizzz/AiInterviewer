"""Deterministic EvaluationPort adapter."""

from shared.contracts import CompetencyState, EvaluationFeedback, EvaluationRequest


class MockEvaluationAdapter:
    def __init__(self, feedback: EvaluationFeedback | None = None) -> None:
        self._feedback = feedback
        self.requests: list[EvaluationRequest] = []

    async def evaluate(self, request: EvaluationRequest) -> EvaluationFeedback:
        self.requests.append(request.model_copy(deep=True))
        if self._feedback is not None:
            return self._feedback.model_copy(deep=True)
        competency = request.question.target_competency
        return EvaluationFeedback(
            request_id=request.request_id,
            question_id=request.question.question_id,
            target_competency=competency,
            answer_relevance=0.8,
            evidence_strength=0.6,
            evaluation_confidence=0.7,
            rubric_level=3,
            updated_competency_state=CompetencyState(
                competency=competency,
                score=3.0,
                coverage=0.5,
                confidence=0.6,
                max_verified_difficulty=request.question.difficulty,
                evidence_count=1,
                independent_evidence_count=1,
            ),
            evidence_ids=[f"evidence-{request.answer.answer_id}"],
        )
