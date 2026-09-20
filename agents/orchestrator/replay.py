"""Replay dialogue policy without model calls or writes."""

from agents.config import load_agent_settings
from agents.domain.models import PolicyReplayResult
from agents.policies.dialogue_policy import choose_dialogue


def replay_decision(context, settings=None):
    settings = settings or load_agent_settings()
    project, topic, probe = choose_dialogue(context, settings)
    action = probe.dialogue_action
    if (
        not probe.should_probe
        and context.active_thread
        and project
        and (project.project_id != context.active_thread.project_id)
    ):
        action = "new_project"
    return PolicyReplayResult(
        selected_project_id=project.project_id if project else None,
        selected_topic=topic.topic if topic else None,
        difficulty=context.thread_difficulty
        if probe.should_probe
        else settings.initial_question_difficulty,
        probe_depth=probe.next_probe_depth,
        should_probe=probe.should_probe,
        dialogue_action=action,
        reason_code=probe.reason_code,
    )
