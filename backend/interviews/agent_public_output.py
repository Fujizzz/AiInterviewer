"""Responsibilities: Define candidate-visible job metadata and individualized interview feedback.
Implementation: Project internal job data and question history before complete output review;
reject historical public job objects with extra or malformed fields.
Related Modules: agent_session constructs responses; agent_safety checks publication and history.

Declaration Index:
- PublicJobProfile: Candidate-facing role metadata, excluding competency_importance.
- public_job_profile: Project internal job data without changing the source or scoring.
- public_question_history: Retain answered questions and feedback without evaluator control flags.
- has_public_job_profile: Validate a present finished-result job object without rewriting output.

Variable Index:
None
"""

from pydantic import BaseModel, ConfigDict, ValidationError


class PublicJobProfile(BaseModel):
    """Expose role metadata; forbid unknown fields or type coercion at the public boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: str
    job_id: str
    title: str
    seniority: str | None
    domains: list[str]


def public_job_profile(job):
    """Return detached public job metadata; leave the internal scoring model unchanged."""
    selected = job.model_dump(mode="json", include=set(PublicJobProfile.model_fields))
    return PublicJobProfile.model_validate(selected).model_dump(mode="json")


def public_question_history(history):
    """Project own Q&A and awarded feedback without changing evidence or technical explanations.

    No text is truncated or rewritten: the complete public text still enters semantic review.
    Scoring and orchestration continue to use the original internal history.
    """
    public = []
    for entry in history:
        item = {
            key: entry[key]
            for key in (
                "question_id",
                "question",
                "answer",
                "topic",
                "difficulty",
                "dialogue_action",
                "parent_question_id",
            )
            if key in entry
        }
        evaluation = entry.get("evaluation")
        if isinstance(evaluation, dict):
            feedback = {
                key: evaluation[key]
                for key in (
                    "answer_relevance",
                    "evidence_strength",
                    "dimensions",
                    "evidence_ids",
                    "analysis_status",
                    "assessment_status",
                )
                if key in evaluation
            }
            analysis = evaluation.get("analysis")
            if isinstance(analysis, dict):
                feedback["analysis"] = {
                    key: analysis[key]
                    for key in (
                        "status",
                        "summary",
                        "missing_information",
                        "contradictions",
                        "uncertainties",
                    )
                    if key in analysis
                }
            item["evaluation"] = feedback
        public.append(item)
    return public


def has_public_job_profile(payload):
    """Reject private or malformed job objects; legacy outputs omitting a job remain readable.

    This shape check grants no semantic approval. Free text still requires the existing reviewer,
    and historical output still requires an intact approval receipt.
    """
    if payload.get("type") != "finished":
        return True
    result = payload.get("result")
    if not isinstance(result, dict):
        return False
    if "job_profile" not in result:
        return True
    try:
        PublicJobProfile.model_validate(result["job_profile"])
    except ValidationError:
        return False
    return True
