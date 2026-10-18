"""Concrete adapters that connect the MVP shell to the local Agent ports."""

from app.adapters.evaluation import AnswerEvidence, LLMEvaluationAdapter
from app.adapters.llm import GeneratedText, ProviderLLMAdapter
from app.adapters.repository import InMemoryInterviewRepository

__all__ = [
    "AnswerEvidence",
    "GeneratedText",
    "InMemoryInterviewRepository",
    "LLMEvaluationAdapter",
    "ProviderLLMAdapter",
]
