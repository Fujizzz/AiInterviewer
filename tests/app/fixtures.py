"""Schema-validated provider fixture for the integrated MVP tests."""

from app.adapters.evaluation import AnswerEvidence
from app.adapters.llm import GeneratedText
from app.parsing.resume import ResumeExtraction
from app.reporting.final_report import ReportNarrative


class FixtureLLM:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, prompt, data, schema):
        self.calls.append((schema, data))
        if schema is ResumeExtraction:
            topics = ["Book Recommendation System", "Log Analysis Pipeline"]
            assert all(topic in data["resume_text"] for topic in topics)
            output = {
                "candidate_name": "Alex Chen",
                "skills": ["Python", "SQL", "pandas", "scikit-learn", "pytest"],
                "projects": [
                    {
                        "name": topics[0],
                        "domain": "recommendation systems",
                        "description": "Built an item-based book recommender.",
                        "technologies": ["Python", "pandas", "scikit-learn"],
                        "claims": [
                            "Implemented item-based collaborative filtering.",
                            "Evaluated recommendations against a popularity baseline.",
                        ],
                        "metrics": ["precision on held-out interactions"],
                    },
                    {
                        "name": topics[1],
                        "domain": "data engineering",
                        "description": "Built a streaming log-analysis pipeline.",
                        "technologies": ["Python", "SQL", "pytest"],
                        "claims": [
                            "Parsed logs with bounded memory.",
                            "Handled malformed records separately.",
                        ],
                        "metrics": [],
                    },
                ],
            }
        elif schema is GeneratedText:
            plan = data["question_plan"]
            topic = str(plan["topic"]).rstrip(".,;:")
            output = {
                "text": (
                    f"What did you personally implement for {topic}, "
                    f"and why did you choose that approach?"
                )
            }
        elif schema is AnswerEvidence:
            output = {
                "answer_relevance": 0.9,
                "evidence_strength": 0.8,
                "evaluation_confidence": 0.85,
                "rubric_level": 3,
                "contradiction_detected": False,
                "needs_clarification": False,
                "evidence_summary": "The answer describes implementation and rationale.",
            }
        elif schema is ReportNarrative:
            output = {
                "strengths": ["Answers described implementation choices."],
                "weaknesses": ["Some standardized competencies remain untested."],
                "summary": "Offline evidence-based fixture report.",
            }
        else:
            raise AssertionError(f"Unexpected schema: {schema}")
        return schema.model_validate(output)
