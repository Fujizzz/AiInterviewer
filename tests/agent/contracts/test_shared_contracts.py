import pytest
from pydantic import BaseModel

from shared.contracts import (
    CandidateAnswer,
    CandidateClaim,
    CandidateProfile,
    CandidateProject,
    CompetencyState,
    DecisionTrace,
    EvaluationFeedback,
    EvaluationRequest,
    InitializeInterviewRequest,
    InitializeInterviewResponse,
    InterviewAction,
    InterviewPlan,
    JobProfile,
    PlannedQuestion,
    RetrievalRequest,
    RetrievalResponse,
    RetrievedChunk,
    StagePlan,
)


@pytest.mark.parametrize(
    "contract_model",
    [
        CandidateAnswer,
        CandidateClaim,
        CandidateProfile,
        CandidateProject,
        CompetencyState,
        DecisionTrace,
        EvaluationFeedback,
        EvaluationRequest,
        InitializeInterviewRequest,
        InitializeInterviewResponse,
        InterviewAction,
        InterviewPlan,
        JobProfile,
        PlannedQuestion,
        RetrievalRequest,
        RetrievalResponse,
        RetrievedChunk,
        StagePlan,
    ],
)
def test_every_cross_module_model_declares_contract_version(
    contract_model: type[BaseModel],
) -> None:
    version_field = contract_model.model_fields["contract_version"]

    assert version_field.default == "2.0"
