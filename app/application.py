"""Compose the remote MVP shell around the canonical local Agent implementation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from app.adapters import InMemoryInterviewRepository, LLMEvaluationAdapter, ProviderLLMAdapter
from app.agents.evaluator import build_final_report
from app.agents.resume_parser import parse_resume_profile
from app.llm import OpenAILLM, StructuredLLM
from shared.contracts import (
    CandidateAnswer,
    Competency,
    EvaluationRequest,
    InitializeInterviewRequest,
    InterviewAction,
    InterviewActionType,
    InterviewStage,
    JobProfile,
)

DEFAULT_COMPETENCY_IMPORTANCE: dict[Competency, float] = {
    Competency.TECHNICAL_DEPTH: 0.25,
    Competency.OWNERSHIP: 0.15,
    Competency.DECISION_MAKING: 0.15,
    Competency.DEBUGGING: 0.20,
    Competency.EVALUATION: 0.15,
    Competency.ADAPTABILITY: 0.10,
}


class MVPInterviewApplication:
    """Run the terminal MVP while delegating all interview decisions to the Agent core."""

    seconds_per_question = 120

    def __init__(
        self,
        llm: StructuredLLM,
        *,
        repository: InMemoryInterviewRepository | None = None,
    ) -> None:
        self.llm = llm
        self.repository = repository or InMemoryInterviewRepository()
        self.agent_llm = ProviderLLMAdapter(llm)
        self.evaluation = LLMEvaluationAdapter(llm, self.repository)

    async def run(
        self,
        resume_text: str,
        *,
        max_questions: int = 5,
        max_follow_up_per_topic: int = 2,
        job_title: str = "General AI / Software Engineer",
        read_answer: Callable[[str], str] = input,
        write: Callable[[str], None] = print,
    ) -> dict[str, Any]:
        if max_questions < 1 or max_follow_up_per_topic < 0:
            raise ValueError(
                "max_questions must be positive and max_follow_up_per_topic nonnegative"
            )

        interview_id = str(uuid4())
        candidate_profile, candidate_name = await parse_resume_profile(
            resume_text,
            llm=self.llm,
            candidate_id=f"candidate-{interview_id}",
        )
        job_profile = JobProfile(
            job_id=f"job-{interview_id}",
            title=job_title,
            competency_importance=DEFAULT_COMPETENCY_IMPORTANCE,
        )
        settings = load_agent_settings().model_copy(
            update={"max_consecutive_probes": max_follow_up_per_topic}
        )
        service = InterviewAgentService(
            repository=self.repository,
            evaluation=self.evaluation,
            llm=self.agent_llm,
            settings=settings,
        )
        duration_seconds = max_questions * self.seconds_per_question
        initialized = await service.initialize_interview(
            InitializeInterviewRequest(
                interview_id=interview_id,
                candidate_profile=candidate_profile,
                job_profile=job_profile,
                duration_seconds=duration_seconds,
                enabled_stages=[InterviewStage.PROJECT_DEEP_DIVE],
            )
        )

        write("AI Interviewer started (local deterministic Agent core).")
        history: list[dict[str, Any]] = []
        action = await self._advance_non_question_actions(
            service,
            interview_id,
            initialized.first_action,
        )
        while action.type == InterviewActionType.ASK_QUESTION:
            question = action.question
            if question is None or not question.text:
                raise RuntimeError("Agent returned an ASK_QUESTION action without question text.")
            write(f"\nQuestion {len(history) + 1} [{question.target_competency.value}]:")
            write(question.text)
            answer = read_answer("Your answer:\n> ").strip()
            while not answer:
                write("Please enter an answer (Ctrl+C to cancel).")
                answer = read_answer("Your answer:\n> ").strip()

            candidate_answer = CandidateAnswer(
                interview_id=interview_id,
                question_id=question.question_id,
                answer_id=str(uuid4()),
                text=answer,
            )
            evaluation_request = EvaluationRequest(
                request_id=str(uuid4()),
                interview_id=interview_id,
                question=question,
                answer=candidate_answer,
            )
            feedback = await self.evaluation.evaluate(evaluation_request)
            history.append(
                {
                    "question_id": question.question_id,
                    "competency": question.target_competency.value,
                    "project_id": question.project_id,
                    "topic": question.topic,
                    "difficulty": question.difficulty,
                    "question": question.text,
                    "answer": answer,
                    "evaluation": {
                        "answer_relevance": feedback.answer_relevance,
                        "evidence_strength": feedback.evidence_strength,
                        "evaluation_confidence": feedback.evaluation_confidence,
                        "rubric_level": feedback.rubric_level,
                        "needs_clarification": feedback.needs_clarification,
                        "contradiction_detected": feedback.contradiction_detected,
                        "evidence_ids": feedback.evidence_ids,
                    },
                }
            )
            action = await service.apply_evaluation_feedback(
                interview_id,
                feedback,
                elapsed_seconds=self.seconds_per_question,
            )
            action = await self._advance_non_question_actions(service, interview_id, action)

        if action.type != InterviewActionType.FINISH:
            raise RuntimeError(f"Interview stopped with unexpected action {action.type.value!r}.")
        context = await self.repository.get_interview_context(interview_id)
        report = await build_final_report(context, history, llm=self.llm)
        return {
            "interview_id": interview_id,
            "candidate_name": candidate_name,
            "candidate_profile": candidate_profile.model_dump(mode="json"),
            "job_profile": job_profile.model_dump(mode="json"),
            "topics": [project.name for project in candidate_profile.projects],
            "question_history": history,
            "interview_state": context.state.model_dump(mode="json"),
            "decision_logs": [
                log.model_dump(mode="json")
                for log in self.repository.decision_logs_for(interview_id)
            ],
            "interview_finished": context.state.status == "finished",
            "final_report": report.model_dump(mode="json"),
        }

    @staticmethod
    async def _advance_non_question_actions(
        service: InterviewAgentService,
        interview_id: str,
        action: InterviewAction,
    ) -> InterviewAction:
        transitions = 0
        while action.type == InterviewActionType.CHANGE_STAGE:
            transitions += 1
            if transitions > len(InterviewStage):
                raise RuntimeError("Agent produced an invalid stage-transition loop.")
            action = await service.next_action(interview_id)
        return action


def build_application(llm: StructuredLLM | None = None) -> MVPInterviewApplication:
    return MVPInterviewApplication(llm or OpenAILLM())
