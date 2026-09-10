"""Produce a validated final evaluation using the interview Q&A history."""
from typing import Annotated

from pydantic import Field

from app.llm import OutputModel, StructuredLLM
from app.state import InterviewState

Score = Annotated[float, Field(ge=1, le=5)]


class FinalReport(OutputModel):
    overall_score: Score
    technical_depth: Score
    problem_solving: Score
    communication: Score
    strengths: list[str]
    weaknesses: list[str]
    summary: str


def evaluate_interview(state: InterviewState, *, llm: StructuredLLM) -> dict:
    """Score the Q&A history on a 1-5 scale and mark the interview as finished."""
    result = llm(
        "Evaluate the candidate using only the interview Q&A history. Use a 1-5 scale: "
        "1 very weak/vague; 2 basic understanding; 3 adequate interview-level answer; "
        "4 strong explanation with reasoning and examples; 5 excellent depth, tradeoff reasoning "
        "and adaptation to new constraints. Score technical depth, problem solving, communication "
        "and overall performance. Support strengths, weaknesses and summary with specific answer "
        "evidence. Do not give credit based only on resume keywords or treat analyzer opinions "
        "as facts beyond the answers. Acknowledge untested areas.",
        {"question_history": state["question_history"]}, FinalReport,
    )
    return {"final_report": result.model_dump(), "interview_finished": True}
