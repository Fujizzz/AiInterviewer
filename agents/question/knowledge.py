"""A single grounded mechanism question inside an existing project thread."""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from agents.policies.dialogue_controller import DialogueController
from shared.contracts import PlannedQuestion, QuestionType, RetrievalSource

if TYPE_CHECKING:
    from agents.domain.models import RetrievalBatch
    from agents.question.react import QuestionAgentResult
    from shared.contracts import RetrievalRequest


@dataclass
class KnowledgeTurn:
    question: PlannedQuestion
    result: "QuestionAgentResult"
    requests: list["RetrievalRequest"]
    batch: "RetrievalBatch"
    retrieval_ms: int
    generation_ms: int
    prepared_id: str | None = None


def knowledge_plan(context, settings):
    """Respect the same stop, time and question ceilings as any other follow-up."""
    active = context.active_thread
    latest = context.question_history[-1] if context.question_history else None
    if (
        not active
        or not active.project_id
        or active.project_id in context.project_knowledge
        or not latest
        or latest.question.thread_id != active.thread_id
        or latest.question.question_id != context.state.current_question_id
        or latest.answer is None
        or not latest.answer.text.strip()
        or getattr(latest.feedback, "analysis_status", "valid") != "valid"
        or DialogueController(context, settings).followup_block() is not None
    ):
        return None
    project = next(
        (p for p in context.candidate_profile.projects if p.project_id == active.project_id), None
    )
    if project is None:
        return None
    # The current topic keeps the query tied to this conversation. Technology names
    # provide exact lexical anchors without another model call or the whole resume.
    technologies = ", ".join(project.technologies[:3])[:200]
    subject = f"{active.topic[:160]} ({technologies})" if technologies else active.topic[:160]
    goal = f"Explain one underlying technical mechanism of {subject}"
    question_id = str(uuid4())
    return PlannedQuestion(
        question_id=question_id,
        project_id=active.project_id,
        topic=active.topic,
        topic_key=active.topic_key,
        difficulty=context.thread_difficulty,
        probe_depth=active.follow_up_count + 2,
        question_type=QuestionType.MECHANISM,
        intent=goal,
        information_goal=goal,
        answer_unit=goal,
        required_context_sources=[RetrievalSource.TECHNICAL],
        dialogue_action="probe",
        parent_question_id=context.state.current_question_id,
        thread_id=active.thread_id,
        intent_id=f"intent:{question_id}",
        objective_id=active.topic_key,
        need_id=f"knowledge:{question_id}",
    )
