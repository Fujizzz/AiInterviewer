"""Generate opening questions and follow-ups from interview context."""
from pydantic import Field

from app.llm import LLMError, OutputModel, StructuredLLM
from app.state import InterviewState


class QuestionOutput(OutputModel):
    current_question: str = Field(min_length=1)


def generate_question(state: InterviewState, *, llm: StructuredLLM) -> dict:
    """Request one contextual question and retry once if it exactly repeats an earlier question."""
    previous = [item["question"] for item in state["question_history"]]
    data = {
        "candidate_profile": state["candidate_profile"],
        "current_topic": state["topics"][state["topic_index"]],
        "previous_questions": previous,
        "previous_answer_analysis": state.get("answer_analysis", {}) if state["follow_up_count"] else {},
        "follow_up_count": state["follow_up_count"],
    }
    prompt = (
        "You are a technical interviewer. Generate exactly one concise question grounded in "
        "the candidate profile and current topic. With follow_up_count=0 ask an opening question "
        "about the candidate's contribution. Otherwise use previous answer analysis to ask for "
        "missing details or deeper reasoning. Do not provide hints or answers. Do not repeat "
        "previous questions, including paraphrases."
        " Ask about only ONE aspect, in one short sentence (at most 45 English words or "
        "90 Chinese characters). Do not list examples, possible technologies, answer options, "
        "or multiple subquestions. On a new topic, explicitly focus on that topic instead "
        "of returning to a previously discussed project."
    )
    for _ in range(2):
        result = llm(prompt, data, QuestionOutput)
        question = result.current_question.strip()
        if question.casefold() not in {q.strip().casefold() for q in previous}:
            return {"current_question": question}
        prompt += " Your last question was a duplicate. Ask a different question."
    raise LLMError("The model repeatedly generated a duplicate question.")
