"""Deterministic construction of the structured question intent."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

from agents.domain.models import ProbeDecision, TopicSelection
from shared.contracts import (
    CandidateClaim,
    CandidateProject,
    Competency,
    PlannedQuestion,
    QuestionType,
    RetrievalSource,
)

_SOURCES_BY_COMPETENCY: dict[Competency, list[RetrievalSource]] = {
    Competency.TECHNICAL_DEPTH: [
        RetrievalSource.CANDIDATE,
        RetrievalSource.TECHNICAL,
        RetrievalSource.QUESTION,
    ],
    Competency.OWNERSHIP: [RetrievalSource.CANDIDATE],
    Competency.DECISION_MAKING: [
        RetrievalSource.CANDIDATE,
        RetrievalSource.TECHNICAL,
        RetrievalSource.QUESTION,
    ],
    Competency.DEBUGGING: [
        RetrievalSource.CANDIDATE,
        RetrievalSource.TECHNICAL,
        RetrievalSource.QUESTION,
    ],
    Competency.EVALUATION: [RetrievalSource.CANDIDATE, RetrievalSource.QUESTION],
    Competency.ADAPTABILITY: [
        RetrievalSource.CANDIDATE,
        RetrievalSource.TECHNICAL,
        RetrievalSource.QUESTION,
    ],
}

_INTENTS: dict[Competency, str] = {
    Competency.TECHNICAL_DEPTH: "test the candidate's explanation of the underlying mechanism",
    Competency.OWNERSHIP: "establish the candidate's personal implementation responsibility",
    Competency.DECISION_MAKING: "test how the candidate made and justified a technical choice",
    Competency.DEBUGGING: "test systematic failure diagnosis and root-cause validation",
    Competency.EVALUATION: "test how the candidate verified outcomes with meaningful evidence",
    Competency.ADAPTABILITY: "test reasoning under changed constraints and scale",
}


class QuestionPlanner:
    """Build a plan without reconsidering upstream policy decisions."""

    prompt_name = "question_planner_v1"

    def plan(
        self,
        *,
        target_competency: Competency,
        selected_project: CandidateProject | None,
        selected_topic: TopicSelection | str,
        difficulty: int,
        probe_decision: ProbeDecision | None = None,
        probe_depth: int | None = None,
        recent_question_summaries: Sequence[str] = (),
        recent_feedback_summary: Sequence[str] = (),
        candidate_claims: Sequence[CandidateClaim] = (),
    ) -> PlannedQuestion:
        del recent_question_summaries, recent_feedback_summary, candidate_claims
        topic = (
            selected_topic.topic if isinstance(selected_topic, TopicSelection) else selected_topic
        )
        resolved_probe_depth = probe_depth
        if resolved_probe_depth is None and probe_decision is not None:
            resolved_probe_depth = probe_decision.next_probe_depth
        resolved_probe_depth = max(1, resolved_probe_depth or 1)
        return PlannedQuestion(
            question_id=str(uuid4()),
            target_competency=target_competency,
            project_id=selected_project.project_id if selected_project is not None else None,
            topic=topic,
            difficulty=difficulty,
            probe_depth=resolved_probe_depth,
            question_type=self.question_type_for(
                competency=target_competency,
                difficulty=difficulty,
                probe_depth=resolved_probe_depth,
            ),
            intent=_INTENTS[target_competency],
            required_context_sources=list(_SOURCES_BY_COMPETENCY[target_competency]),
            text=None,
        )

    @staticmethod
    def question_type_for(
        *,
        competency: Competency,
        difficulty: int,
        probe_depth: int,
    ) -> QuestionType:
        if competency == Competency.DEBUGGING or probe_depth == 6:
            return QuestionType.FAILURE_ANALYSIS
        if competency == Competency.ADAPTABILITY and (difficulty == 5 or probe_depth >= 7):
            return QuestionType.COUNTERFACTUAL
        if competency == Competency.OWNERSHIP:
            return QuestionType.IMPLEMENTATION
        if competency == Competency.DECISION_MAKING:
            return (
                QuestionType.TRADEOFF
                if difficulty >= 4 or probe_depth >= 5
                else QuestionType.DESIGN
            )
        if competency == Competency.EVALUATION:
            return QuestionType.DESIGN
        if difficulty <= 1 or probe_depth <= 1:
            return QuestionType.DESCRIPTION
        if difficulty == 2 or probe_depth == 2:
            return QuestionType.IMPLEMENTATION
        if difficulty == 3 or probe_depth == 3:
            return QuestionType.MECHANISM
        if difficulty == 4 or probe_depth in {4, 5}:
            return QuestionType.TRADEOFF
        return QuestionType.COUNTERFACTUAL
