"""Schema-validated provider fixture for the integrated MVP tests."""

from agents.question.react import QuestionAgentDecision
from app.adapters.evaluation import AnswerEvidence
from app.adapters.llm import GeneratedText
from app.parsing.resume import ResumeExtraction
from app.reporting.final_report import ReportNarrative
from tests.agent.mocks.dialogue_output import plan_for, selection_for


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
        elif schema in (GeneratedText, QuestionAgentDecision):
            selection = selection_for(data) if "dialogue_state" in data else None
            plan = plan_for(data, selection) if selection else data["question_plan"]
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
                "analysis": {
                    "status": "substantive",
                    "new_information": True,
                    "thread_complete": True,
                },
                "dimensions": [
                    {
                        "competency": "ownership",
                        "observation": "supported",
                        "quote": data["answer"],
                        "fact": data["answer"],
                        "rationale": "Describes personal implementation",
                        "rubric_level": 3,
                        "strength": 0.8,
                    }
                ],
            }
        elif schema is ReportNarrative:
            output = {
                "strengths": ["Answers described implementation choices."],
                "weaknesses": ["Some standardized competencies remain untested."],
            }
        else:
            raise AssertionError(f"Unexpected schema: {schema}")
        if schema is QuestionAgentDecision:
            output.update(action="final", topic=None, limit=None, selection=selection)
        return schema.model_validate(output)
