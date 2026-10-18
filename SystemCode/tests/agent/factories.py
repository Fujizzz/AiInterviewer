"""Compact model factories shared by tests."""

from shared.contracts import (
    CandidateProfile,
    Competency,
    CompetencyState,
    InitializeInterviewRequest,
    InterviewPlan,
    InterviewStage,
    InterviewState,
    JobProfile,
    StagePlan,
)


def competency_states() -> dict[Competency, CompetencyState]:
    return {competency: CompetencyState(competency=competency) for competency in Competency}


def interview_state(
    *,
    interview_id: str = "interview-1",
    remaining_seconds: int = 900,
) -> InterviewState:
    return InterviewState(
        interview_id=interview_id,
        remaining_seconds=remaining_seconds,
        competencies=competency_states(),
    )


def interview_plan(*, interview_id: str = "interview-1") -> InterviewPlan:
    return InterviewPlan(
        interview_id=interview_id,
        duration_seconds=900,
        stages=[StagePlan(stage=InterviewStage.INTRO, budget_seconds=900)],
    )


def initialize_request(*, interview_id: str = "interview-1") -> InitializeInterviewRequest:
    return InitializeInterviewRequest(
        interview_id=interview_id,
        candidate_profile=CandidateProfile(candidate_id="candidate-1"),
        job_profile=JobProfile(
            job_id="job-1",
            title="Software Engineer",
            competency_importance={competency: 0.8 for competency in Competency},
        ),
        duration_seconds=900,
        enabled_stages=[
            InterviewStage.INTRO,
            InterviewStage.PROJECT_DEEP_DIVE,
            InterviewStage.CLOSING,
        ],
    )
