"""Evaluation boundary; scoring internals remain outside the Agent."""

from typing import Protocol

from shared.contracts import EvaluationFeedback, EvaluationRequest


class EvaluationPort(Protocol):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationFeedback: ...
