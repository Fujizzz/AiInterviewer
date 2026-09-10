"""Define the shared state passed between LangGraph interview nodes."""
from typing import TypedDict


class InterviewState(TypedDict, total=False):
    resume_text: str
    candidate_profile: dict
    topics: list[str]
    topic_index: int
    current_question: str
    current_answer: str
    answer_analysis: dict
    question_history: list[dict]
    follow_up_count: int
    max_follow_up_per_topic: int
    max_questions: int
    decision: str
    interview_finished: bool
    final_report: dict
