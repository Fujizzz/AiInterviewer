"""Deterministic EvaluationPort adapter."""

from shared.contracts import AnswerAnalysis, EvaluationFeedback, EvaluationRequest


class MockEvaluationAdapter:
    def __init__(self, feedback: EvaluationFeedback | None = None) -> None:
        self._feedback = feedback
        self.requests: list[EvaluationRequest] = []

    async def evaluate(self, request: EvaluationRequest) -> EvaluationFeedback:
        self.requests.append(request.model_copy(deep=True))
        if self._feedback is not None:
            return self._feedback.model_copy(deep=True)
        return EvaluationFeedback(
            request_id=request.request_id,
            question_id=request.question.question_id,
            answer_relevance=0.8,
            evidence_strength=0.6,
            analysis=AnswerAnalysis(status="substantive", new_information=True),
            evidence_ids=[f"evidence-{request.answer.answer_id}"],
        )
