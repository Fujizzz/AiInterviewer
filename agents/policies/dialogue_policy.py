"""Resolve conversation continuation before project/topic selection."""

from agents.domain.models import TopicSelection
from agents.policies.dialogue_controller import DialogueController
from agents.policies.probe_controller import ProbeController
from agents.policies.project_selector import ProjectSelector


def choose_dialogue(context, settings):
    probe = ProbeController(settings).decide(context=context)
    projects = context.candidate_profile.projects
    active = context.active_thread
    if probe.should_probe and active:
        project = next((p for p in projects if p.project_id == active.project_id), None)
        return (
            project,
            TopicSelection(
                topic=active.topic,
                topic_key=active.topic_key,
                reason_code="CONTINUE_CURRENT_THREAD",
            ),
            probe,
        )
    controller = DialogueController(context, settings)
    available = {}
    for project, topic in controller.available_topics().values():
        available.setdefault(project.project_id, topic)
    if not projects and "general:experience" not in context.used_topic_keys:
        return (
            None,
            TopicSelection(
                topic="your project experience",
                topic_key="general:experience",
                reason_code="GENERAL_EXPERIENCE",
            ),
            probe,
        )
    if not available:
        return None, None, probe
    switch_project = probe.reason_code in {"CANDIDATE_STOPPED_THREAD", "NO_NEW_INFORMATION"}
    if active and active.project_id in available and not switch_project:
        project_id = active.project_id
    else:
        excluded = {p.project_id for p in projects if p.project_id not in available}
        if switch_project and len(available) > 1 and active:
            excluded.add(active.project_id)
        project_id = (
            ProjectSelector(settings)
            .select(
                profile=context.candidate_profile,
                job=context.job_profile,
                state=context.state,
                excluded_project_ids=excluded,
            )
            .project_id
        )
    project = next(p for p in projects if p.project_id == project_id)
    return project, available[project_id], probe
