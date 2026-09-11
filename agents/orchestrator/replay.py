"""Deterministic replay of the Agent's core policy decision."""

from agents.config import AgentSettings, load_agent_settings
from agents.domain.models import InterviewContext, PolicyReplayResult, TopicSelection
from agents.policies import (
    CompetencySelector,
    ProbeController,
    ProjectSelector,
    TopicSelector,
)


def replay_decision(
    context: InterviewContext,
    settings: AgentSettings | None = None,
) -> PolicyReplayResult:
    """Recompute policy selections without RAG, LLM wording, or persistence."""

    resolved_settings = settings or load_agent_settings()
    state = context.state
    selection = CompetencySelector(resolved_settings).select(
        state=state,
        plan=context.plan,
        anchor_state=context.anchor_state,
    )
    selected_project = None
    if context.candidate_profile.projects:
        project_selection = ProjectSelector(resolved_settings).select(
            profile=context.candidate_profile,
            job=context.job_profile,
            state=state,
            competency=selection.competency,
        )
        selected_project = next(
            project
            for project in context.candidate_profile.projects
            if project.project_id == project_selection.project_id
        )

    probe = ProbeController(resolved_settings).decide(
        competency=selection.competency,
        state=state,
        plan=context.plan,
    )
    difficulty = context.current_difficulty.get(
        selection.competency,
        max(
            resolved_settings.initial_question_difficulty,
            state.competencies[selection.competency].max_verified_difficulty,
        ),
    )
    difficulty = min(difficulty, resolved_settings.max_question_difficulty)
    continue_topic = bool(
        probe.should_probe
        and state.last_competency == selection.competency
        and state.last_topic
        and selected_project is not None
        and selected_project.project_id == state.active_project_id
    )
    if continue_topic and state.last_topic is not None:
        topic = TopicSelection(topic=state.last_topic, reason_code="CONTINUE_PROBE_TOPIC")
    elif selected_project is None:
        topic = TopicSelection(topic="project experience", reason_code="NO_PROJECT_FALLBACK")
    else:
        recent_topics = (
            [state.last_topic]
            if state.last_topic and state.last_competency == selection.competency
            else []
        )
        topic = TopicSelector().select(
            project=selected_project,
            competency=selection.competency,
            recent_topics=recent_topics,
        )
    is_same_topic_probe = bool(continue_topic and state.last_topic == topic.topic)
    return PolicyReplayResult(
        selected_competency=selection.competency,
        selected_project_id=(
            selected_project.project_id if selected_project is not None else None
        ),
        selected_topic=topic.topic,
        difficulty=difficulty,
        probe_depth=probe.next_probe_depth if is_same_topic_probe else 1,
        should_probe=probe.should_probe,
        reason_code=selection.reason_code,
    )
