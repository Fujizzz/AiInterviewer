"""Extract a candidate profile and interview topics from resume facts."""
from pydantic import Field

from app.llm import OutputModel, StructuredLLM
from app.state import InterviewState


class CandidateProfile(OutputModel):
    name: str
    skills: list[str]
    experiences: list[str]
    projects: list[str]


class ResumeOutput(OutputModel):
    candidate_profile: CandidateProfile
    topics: list[str] = Field(description="Real project/experience topics, or explicit skills if none.")


def parse_resume(state: InterviewState, *, llm: StructuredLLM) -> dict:
    """Validate initial inputs, extract resume facts, and initialize topics and interview counters."""
    if not state.get("resume_text", "").strip():
        raise ValueError("Resume must contain text.")
    if state.get("max_questions", 5) < 1 or state.get("max_follow_up_per_topic", 2) < 0:
        raise ValueError("max_questions must be positive and the follow-up limit nonnegative.")
    parsed = llm(
        "You are a resume information extractor. Extract facts supported by the resume only. "
        "Do not invent or infer skills. Use empty strings/lists for missing facts. "
        "Focus on technical projects, experience, technologies, responsibilities and measurable "
        "results. Prefer projects and experiences as topics; use explicit skills only if needed. "
        "Create one topic per distinct project or experience. Do not split different technologies "
        "or subtasks within the same project into separate topics. Prefer a small list of distinct projects. "
        "Return no topics if there is no interviewable information.",
        {"resume_text": state["resume_text"]}, ResumeOutput,
    )
    topics = list(dict.fromkeys(t.strip() for t in parsed.topics if t.strip()))
    if not topics:
        raise ValueError("No interview topics found. Provide a resume with projects, experience, or skills.")
    return {
        "candidate_profile": parsed.candidate_profile.model_dump(), "topics": topics,
        "topic_index": 0, "follow_up_count": 0, "question_history": [],
        "max_questions": state.get("max_questions", 5),
        "max_follow_up_per_topic": state.get("max_follow_up_per_topic", 2),
        "interview_finished": False,
    }
