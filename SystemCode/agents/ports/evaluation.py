"""Evaluation boundary; scoring internals remain outside the Agent.

RubricEvaluationAdapter implements the formal port; ShadowEvaluationAdapter
returns the legacy contract while attaching a private, version-bound receipt.
Pass the returned object to apply_evaluation_feedback without JSON round-tripping
so the repository can atomically append that receipt. Serialization is the safe
contract 2.0/UI projection and deliberately excludes internal scoring artifacts.
"""

from typing import Protocol

from shared.contracts import EvaluationFeedback, EvaluationRequest


class EvaluationPort(Protocol):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationFeedback: ...
