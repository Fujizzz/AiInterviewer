"""Offline 4+4 project / 2+2 topic demonstration through the real Agent service."""

import asyncio
from pathlib import Path

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from agents.tracing import emit_trace
from app.adapters import InMemoryInterviewRepository
from app.reporting.final_report import build_final_report
from app.tracing import FileTrace
from shared.contracts import (
    AnswerAnalysis,
    CandidateAnswer,
    CandidateProfile,
    CandidateProject,
    Competency,
    EvaluationFeedback,
    InitializeInterviewRequest,
    InterviewStage,
    JobProfile,
)


class DemoModel:
    async def generate_structured(self, *, payload, response_model, **kwargs):
        # Scripted semantic pass; quality rejection is tested separately.
        if response_model.__name__ == "QuestionQualityReview":
            return response_model(issues=[])
        view = payload["dialogue_state"]
        active = view["active_thread"]
        continuing = active and view["followup_block"] is None
        if continuing:
            project = next(p for p in view["projects"] if p["project_id"] == active["project_id"])
            key, topic = active["topic_key"], active["topic"]
            action = "probe"
        else:
            project = next(p for p in view["projects"] if p["topics"])
            selected = project["topics"][0]
            key, topic = selected["topic_key"], selected["label"]
            action = (
                "new_project"
                if active and project["project_id"] != active["project_id"]
                else "new_topic"
            )
            if not payload["observations"]:
                return response_model(action="get_project", project_id=project["project_id"])
        focus = "one specific implementation change in" if continuing else "the role of"
        goal = f"Explain {focus} {topic}"
        text = (
            f"In your {project['name']} project, "
            f"what specific change did you implement for {topic}?"
            if continuing
            else f"In your {project['name']} project, what role did {topic} play?"
        )
        return response_model(
            action="final",
            text=text,
            selection=dict(
                dialogue_action=action,
                project_id=project["project_id"],
                topic_key=key,
                information_goal=goal,
                decision_summary="Choose within the project and topic budgets.",
            ),
        )


async def run(trace):
    settings = load_agent_settings().model_copy(
        update={
            "max_questions_per_project": 4,
            "max_questions_per_topic": 2,
        }
    )
    repository = InMemoryInterviewRepository()
    service = InterviewAgentService(repository=repository, llm=DemoModel(), settings=settings)
    request = InitializeInterviewRequest(
        interview_id="two-level-budget-demo",
        candidate_profile=CandidateProfile(
            candidate_id="demo",
            projects=[
                CandidateProject(
                    project_id="lip",
                    name="Lip synchronization",
                    technologies=["keypoint regression", "temporal alignment"],
                ),
                CandidateProject(
                    project_id="qsm",
                    name="QSM reconstruction",
                    technologies=["attention integration", "validation metrics"],
                ),
            ],
        ),
        job_profile=JobProfile(
            job_id="demo",
            title="AI Engineer",
            competency_importance={Competency.TECHNICAL_DEPTH: 1},
        ),
        duration_seconds=960,
        enabled_stages=[InterviewStage.PROJECT_DEEP_DIVE],
    )
    emit_trace("simulation.started")
    emit_trace(
        "interview.started",
        job_title="AI Engineer",
        max_questions=8,
        max_questions_per_project=4,
        max_questions_per_topic=2,
    )
    action = (await service.initialize_interview(request)).first_action
    sequence = []
    while action.question:
        question = action.question
        sequence.append((question.project_id, question.topic_key, question.dialogue_action))
        answer = CandidateAnswer(
            interview_id=request.interview_id,
            question_id=question.question_id,
            answer_id=f"a{len(sequence)}",
            text="I implemented part of the component.",
        )
        emit_trace("answer.received", answer=answer.model_dump(mode="json"))
        action = await service.apply_evaluation_feedback(
            request.interview_id,
            EvaluationFeedback(
                request_id=f"r{len(sequence)}",
                question_id=question.question_id,
                answer_relevance=0.3,
                evidence_strength=0,
                analysis=AnswerAnalysis(status="partial", new_information=True),
            ),
            answer=answer,
            elapsed_seconds=120,
        )
    assert [item[0] for item in sequence] == ["lip"] * 4 + ["qsm"] * 4
    assert [item[2] for item in sequence] == [
        "new_topic",
        "probe",
        "new_topic",
        "probe",
        "new_project",
        "probe",
        "new_topic",
        "probe",
    ]
    context = await repository.get_interview_context(request.interview_id)
    report = await build_final_report(context, [])
    trace.save_result({"final_report": report.model_dump(mode="json")})
    print("OFFLINE: project A (2 + 2 questions) -> project B (2 + 2 questions)")


def main():
    with FileTrace(Path(__file__).resolve().parents[2] / "output") as trace:
        asyncio.run(run(trace))
    print(trace.path)


if __name__ == "__main__":
    main()
