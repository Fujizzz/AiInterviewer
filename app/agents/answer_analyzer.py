"""Analyze individual answers as evidence for subsequent interview decisions."""
from typing import Literal

from app.llm import OutputModel, StructuredLLM
from app.state import InterviewState

Level = Literal["low", "medium", "high"]


class AnswerAnalysis(OutputModel):
    summary: str
    specificity: Level
    technical_depth: Level
    evidence_strength: Level
    missing_information: list[str]
    suggest_follow_up: bool


def analyze_answer(state: InterviewState, *, llm: StructuredLLM) -> dict:
    """Return structured evidence and a follow-up suggestion for the current answer."""
    result = llm(
        "Analyze the candidate answer as interview evidence. Do not produce a final score. "
        "Estimate specificity, technical depth, evidence strength, missing information and "
        "whether a follow-up would be useful. Base the summary only on the actual answer.",
        {"question": state["current_question"], "answer": state["current_answer"],
         "topic": state["topics"][state["topic_index"]]}, AnswerAnalysis,
    )
    return {"answer_analysis": result.model_dump()}
