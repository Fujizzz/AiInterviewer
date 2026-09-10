"""Provide canned, schema-validated model outputs for offline workflow tests."""
from app.agents.answer_analyzer import AnswerAnalysis
from app.agents.evaluator import FinalReport
from app.agents.question_agent import QuestionOutput
from app.agents.resume_parser import ResumeOutput


class FixtureLLM:
    def __init__(self, follow_up=True):
        """Configure follow-up behavior and initialize the model-call log."""
        self.follow_up = follow_up
        self.calls = []

    def __call__(self, prompt, data, schema):
        """Return a canned response for the requested schema and record the call."""
        self.calls.append((schema, data))
        if schema is ResumeOutput:
            topics = ["Book Recommendation System", "Log Analysis Pipeline"]
            assert all(topic in data["resume_text"] for topic in topics)
            output = {"candidate_profile": {
                "name": "Alex Chen", "skills": ["Python", "SQL", "pandas", "scikit-learn", "pytest"],
                "experiences": [], "projects": topics,
            }, "topics": topics}
        elif schema is QuestionOutput:
            topic = data["current_topic"]
            count = data["follow_up_count"]
            questions = [
                f"What was your specific contribution to {topic}?",
                f"Why did you choose that approach for {topic}?",
                f"How did you validate the implementation of {topic}?",
            ]
            output = {"current_question": questions[count] if count < 3 else
                      f"What would you improve in {topic} in iteration {count}?"}
        elif schema is AnswerAnalysis:
            output = {
                "summary": "Fixture evidence: " + data["answer"],
                "specificity": "medium", "technical_depth": "medium",
                "evidence_strength": "medium", "missing_information": ["Design tradeoffs"],
                "suggest_follow_up": self.follow_up,
            }
        elif schema is FinalReport:
            assert set(data) == {"question_history"}
            output = {
                "overall_score": 3.0, "technical_depth": 3.0, "problem_solving": 3.0,
                "communication": 3.0,
                "strengths": ["Fixture only: the answers describe implementation and validation."],
                "weaknesses": ["Fixture only: scaling tradeoffs were not explored."],
                "summary": f"Offline fixture report for {len(data['question_history'])} answers; not an LLM evaluation.",
            }
        else:
            raise AssertionError(f"Unexpected schema: {schema}")
        return schema.model_validate(output)
