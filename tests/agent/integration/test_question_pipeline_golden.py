from agents.domain import ProbeDecision
from agents.policies import CompetencySelector, ProjectSelector, TopicSelector
from agents.question import QuestionPlanner
from shared.contracts import (
    CandidateClaim,
    CandidateProfile,
    CandidateProject,
    Competency,
    JobProfile,
    QuestionType,
    RetrievalSource,
)
from tests.agent.factories import interview_plan, interview_state


def test_low_debugging_coverage_produces_expected_structured_plan() -> None:
    state = interview_state()
    plan = interview_plan()
    for competency_state in state.competencies.values():
        competency_state.evidence_count = 1
        competency_state.coverage = 0.8
        competency_state.confidence = 0.8
    state.competencies[Competency.TECHNICAL_DEPTH].coverage = 0.9
    state.competencies[Competency.TECHNICAL_DEPTH].confidence = 0.9
    state.competencies[Competency.DEBUGGING].coverage = 0.2
    state.competencies[Competency.DEBUGGING].confidence = 0.2
    project = CandidateProject(
        project_id="llm",
        name="LLM Serving",
        domain="AI Infrastructure",
        technologies=["CUDA", "vLLM"],
        claims=[CandidateClaim(claim_id="memory", text="Reduced GPU memory usage")],
    )
    profile = CandidateProfile(candidate_id="candidate-1", projects=[project])
    job = JobProfile(
        job_id="job-1",
        title="AI Infrastructure Engineer",
        domains=["AI Infrastructure"],
        competency_importance=plan.competency_importance,
    )

    competency = CompetencySelector().select(state=state, plan=plan)
    project_selection = ProjectSelector().select(
        profile=profile,
        job=job,
        state=state,
        competency=competency.competency,
    )
    selected_project = next(
        item for item in profile.projects if item.project_id == project_selection.project_id
    )
    topic = TopicSelector().select(
        project=selected_project,
        competency=competency.competency,
    )
    question_plan = QuestionPlanner().plan(
        target_competency=competency.competency,
        selected_project=selected_project,
        selected_topic=topic,
        difficulty=4,
        probe_decision=ProbeDecision(
            should_probe=True,
            next_probe_depth=6,
            reason_code="TARGET_NOT_REACHED",
        ),
    )

    assert question_plan.target_competency == Competency.DEBUGGING
    assert question_plan.project_id == "llm"
    assert question_plan.topic == "GPU memory"
    assert question_plan.question_type == QuestionType.FAILURE_ANALYSIS
    assert RetrievalSource.TECHNICAL in question_plan.required_context_sources
    assert question_plan.text is None
