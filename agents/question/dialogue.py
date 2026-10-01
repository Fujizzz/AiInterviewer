"""Read-only dialogue view and validation of model-selected conversation actions."""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.planning.planner import execution_topics, planning_view
from agents.policies.dialogue_controller import DialogueController
from shared.contracts import PlannedQuestion, QuestionType


class DialogueSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dialogue_action: Literal["new_topic", "new_project", "clarify", "probe"]
    project_id: str | None
    topic_key: str = Field(min_length=1)
    information_goal: str = Field(min_length=3, max_length=400)
    decision_summary: str = Field(min_length=3, max_length=300)

    @field_validator("decision_summary", mode="before")
    @classmethod
    def bound_display_summary(cls, value):
        return value.strip()[:300] if isinstance(value, str) else value


def followup_block(context, settings):
    return DialogueController(context, settings).followup_block()


def available_topics(context):
    return DialogueController(context).available_topics()


def dialogue_view(context, settings):
    topics = available_topics(context)
    active = context.active_thread
    controller = DialogueController(context, settings)
    budget = controller.budget_view(active.project_id, active.topic_key) if active else None
    block = controller.followup_block()
    allowed_actions = []
    if block is None:
        allowed_actions.extend(["clarify", "probe"])
    if any(not active or p.project_id == active.project_id for p, _ in topics.values()):
        allowed_actions.append("new_topic")
    if active and any(p.project_id != active.project_id for p, _ in topics.values()):
        allowed_actions.append("new_project")
    if (
        not context.candidate_profile.projects
        and "general:experience" not in context.used_topic_keys
        and (not context.plan.planning_enabled or execution_topics(context))
    ):
        allowed_actions.append("new_topic")
    return {
        "agenda": planning_view(context) if context.plan.planning_enabled else None,
        "safety_guardrails": {
            "max_questions": context.plan.max_questions,
            "questions_asked": context.state.question_index,
            "max_questions_per_project": context.plan.max_questions_per_project,
            "max_questions_per_topic": context.plan.max_questions_per_topic,
        },
        "job_title": context.job_profile.title,
        "projects": [
            {
                "project_id": project.project_id,
                "name": project.name,
                "domain": project.domain,
                "questions_asked": controller.project_questions(project.project_id),
                "question_limit": context.plan.max_questions_per_project,
                "topics": [
                    {"topic_key": key, "label": topic.topic[:100]}
                    for key, (owner, topic) in topics.items()
                    if owner.project_id == project.project_id
                ],
            }
            for project in context.candidate_profile.projects
        ],
        "general_topic_key": (
            "general:experience"
            if not context.candidate_profile.projects
            and "general:experience" not in context.used_topic_keys
            and (not context.plan.planning_enabled or execution_topics(context))
            else None
        ),
        "active_thread": context.active_thread.model_dump(mode="json")
        if context.active_thread
        else None,
        "budget": budget,
        "previous_topics": [
            {
                "project_id": t.project_id,
                "topic_key": t.topic_key,
                "topic": t.topic,
                "information_goals": t.goals,
                "questions_asked": 1 + t.follow_up_count,
            }
            for t in context.closed_threads
        ],
        "followup_block": block,
        "allowed_dialogue_actions": allowed_actions,
        "followups_remaining": max(
            0,
            min(
                budget["topic_limit"] - budget["topic_questions"],
                budget["project_limit"] - budget["project_questions"],
            ),
        )
        if budget
        else 0,
        "retained_history_count": len(context.question_history),
    }


def writing_brief(context, view):
    """Expose the next usable scope without turning agenda objectives into questions."""
    topics = available_topics(context)
    ordered_keys = [item.topic_key for item in execution_topics(context)]
    ordered_keys.extend(key for key in topics if key not in ordered_keys)
    active = context.active_thread
    can_continue = view["followup_block"] is None
    next_scope = None
    for key in ordered_keys:
        if key not in topics:
            continue
        project, topic = topics[key]
        action = (
            "new_project" if active and project.project_id != active.project_id else "new_topic"
        )
        if action not in view["allowed_dialogue_actions"]:
            continue
        next_scope = {
            "dialogue_action": action,
            "project_id": project.project_id,
            "project_name": project.name,
            "topic_key": key,
            "topic": topic.topic,
        }
        break
    return {
        "mode": "continue_or_switch" if can_continue else "open_new_scope",
        "active_scope": {
            "project_id": active.project_id,
            "topic_key": active.topic_key,
            "topic": active.topic,
        }
        if active and can_continue
        else None,
        "next_available_scope": next_scope,
        "scope_rule": (
            "For clarify/probe use the active scope and latest answer. For new_topic/new_project "
            "use only the selected new scope; old answer gaps are not questions for the new topic."
            if can_continue
            else "The old thread is closed for this turn. Choose an available new scope; "
            "do not continue the latest answer or its missing-information list."
        ),
        "question_rule": "Select ONE unresolved detail from the topic objective, not the entire "
        "objective or completion criteria. Request one answer without supplying possible answers.",
    }


def resolve_selection(selection, context, settings, question_id=None):
    """Derive server-owned IDs/depth/difficulty; reject invalid model choices."""
    active = context.active_thread
    continuing = selection.dialogue_action in {"clarify", "probe"}
    if continuing:
        block = followup_block(context, settings)
        if block:
            raise ValueError(block)
        if selection.project_id != active.project_id or selection.topic_key != active.topic_key:
            raise ValueError("FOLLOWUP_MUST_KEEP_CURRENT_THREAD")
        topic, project_id = active.topic, active.project_id
    else:
        item = available_topics(context).get(selection.topic_key)
        if item:
            project, chosen = item
            if project.project_id != selection.project_id:
                raise ValueError("PROJECT_TOPIC_MISMATCH")
            topic, project_id = chosen.topic, project.project_id
        elif (
            not context.candidate_profile.projects
            and selection.project_id is None
            and selection.topic_key == "general:experience"
            and selection.topic_key not in context.used_topic_keys
            and (not context.plan.planning_enabled or execution_topics(context))
        ):
            topic, project_id = "your project experience", None
        else:
            raise ValueError("UNKNOWN_OR_USED_TOPIC")
        expected = "new_project" if active and project_id != active.project_id else "new_topic"
        if selection.dialogue_action != expected:
            raise ValueError("INCORRECT_DIALOGUE_ACTION")
    if DialogueController(context, settings).goal_already_asked(
        project_id, selection.information_goal, allow_current_clarification=continuing
    ):
        raise ValueError("REPEATED_INFORMATION_GOAL")
    question_id = question_id or str(uuid4())
    return PlannedQuestion(
        question_id=question_id,
        project_id=project_id,
        topic=topic,
        topic_key=selection.topic_key,
        dialogue_action=selection.dialogue_action,
        information_goal=selection.information_goal,
        intent=selection.information_goal,
        difficulty=context.thread_difficulty
        if continuing
        else settings.initial_question_difficulty,
        probe_depth=active.follow_up_count + 2 if continuing else 1,
        question_type=QuestionType.IMPLEMENTATION if continuing else QuestionType.DESCRIPTION,
        thread_id=active.thread_id if continuing else question_id,
        parent_question_id=context.state.current_question_id if continuing else None,
    )
