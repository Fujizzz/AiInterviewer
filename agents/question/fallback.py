"""Finite, candidate-aware question fallback policy."""

from __future__ import annotations

from shared.contracts import CandidateProject, Competency, PlannedQuestion

_GENERIC_ANCHORS: dict[Competency, str] = {
    Competency.TECHNICAL_DEPTH: "Explain how the most important technical mechanism works?",
    Competency.OWNERSHIP: "What exactly did you personally implement in this project?",
    Competency.DECISION_MAKING: "What important technical decision did you make and why?",
    Competency.DEBUGGING: (
        "What technical failure did you encounter and how did you find its root cause?"
    ),
    Competency.EVALUATION: "How did you verify that your solution actually improved the system?",
    Competency.ADAPTABILITY: "What would fail first if the workload increased substantially?",
}


class FallbackQuestionPolicy:
    def apply(
        self,
        question_plan: PlannedQuestion,
        *,
        project: CandidateProject | None = None,
        generic: bool = False,
    ) -> PlannedQuestion:
        if generic:
            text = _GENERIC_ANCHORS[question_plan.target_competency]
        else:
            project_name = project.name if project is not None else "this project"
            topic = question_plan.topic or "the main technical work"
            text = self._candidate_specific_text(
                question_plan.target_competency,
                project_name=project_name,
                topic=topic,
            )
        return question_plan.model_copy(update={"text": text})

    @staticmethod
    def _candidate_specific_text(
        competency: Competency,
        *,
        project_name: str,
        topic: str,
    ) -> str:
        templates = {
            Competency.TECHNICAL_DEPTH: (
                f"In {project_name}, how does the core mechanism behind {topic} work?"
            ),
            Competency.OWNERSHIP: (
                f"For {topic} in {project_name}, what exactly did you personally implement?"
            ),
            Competency.DECISION_MAKING: (
                f"What key decision did you make about {topic} in {project_name}, and why?"
            ),
            Competency.DEBUGGING: (
                f"In {project_name}, describe a failure involving {topic} "
                "and how you found its root cause?"
            ),
            Competency.EVALUATION: (
                f"How did you verify that the work on {topic} in {project_name} "
                "improved the system?"
            ),
            Competency.ADAPTABILITY: (
                f"If the constraints changed substantially, how would you adapt {topic} "
                f"in {project_name}?"
            ),
        }
        return templates[competency]
