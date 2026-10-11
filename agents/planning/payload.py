"""Project persisted facts onto a bounded planning view without another model call.

Question generation, completion checks and scoring retain their full contexts.
This view is deliberately not the question agent's ``planning_view``.
"""

import json

from shared.contracts.planning import TopicProgress


def _text(value, limit):
    return " ".join((value or "").split())[:limit]


def _items(values, *, limit=6, chars=240):
    return list(dict.fromkeys(_text(value, chars) for value in values if value))[:limit]


def planner_payload(context, trigger, eligible):
    """Send scope facts once and current decisions, never raw evidence or transcripts."""
    prior = context.plan_history[-1].proposal if context.plan_history else None
    preferences = {item.topic_key: item for item in prior.topics} if prior else {}
    goals = []
    closed = []
    for item in context.plan.topics:
        progress = context.topic_progress.get(item.topic_key, TopicProgress())
        if progress.status in {"completed", "skipped"} or progress.coverage_status == "sufficient":
            closed.append(item.topic_key)
            continue
        blocked = [need.target for need in progress.information_needs if need.status == "blocked"]
        preference = preferences.get(item.topic_key)
        goals.append(
            {
                "topic_key": item.topic_key,
                "objective": item.objective,
                "objective_version": progress.objective_version,
                "depth": preference.depth if preference else "standard",
                "relative_weight": preference.relative_weight if preference else 1,
                "status": progress.status,
                "coverage": progress.coverage_status,
                "questions_asked": progress.questions_asked,
                "remaining_allocation_seconds": max(
                    0, item.budget_seconds - progress.elapsed_seconds
                ),
                "gaps": _items(progress.missing_information),
                "gap_count": len(progress.missing_information),
                "blocked_needs": _items(blocked),
                "blocked_need_count": len(blocked),
                "criteria_met": sum(
                    r.coverage_status == "sufficient" for r in progress.completion_requirements
                ),
                "criteria_total": len(progress.completion_requirements),
            }
        )
    changes = []
    # Cumulative goal state is above; only the last three answer changes need prose.
    for entry in context.question_history[-3:]:
        feedback = entry.feedback
        change = {
            "topic_key": entry.question.topic_key,
            "answer_id": entry.answer.answer_id if entry.answer else None,
            "analysis_status": feedback.analysis_status,
        }
        if feedback.analysis_status == "valid":
            analysis = feedback.analysis
            change.update(
                status=analysis.status,
                summary=_text(analysis.summary, 240),
                thread_complete=analysis.thread_complete,
                new_information=analysis.new_information,
                contradictions=_items(analysis.contradictions, limit=3),
                limitations=_items(analysis.limitations, limit=3),
            )
        changes.append(change)
    project_ids = {item["project_id"] for item in eligible.values()}
    return {
        "trigger": trigger,
        "base_plan_version": context.plan.version,
        "job": {
            "title": context.job_profile.title,
            "seniority": context.job_profile.seniority,
            "domains": _items(context.job_profile.domains),
            "competency_importance": {
                key.value: value for key, value in context.job_profile.competency_importance.items()
            },
        },
        "projects": [
            {
                "project_id": project.project_id,
                "name": project.name,
                "domain": project.domain,
                "summary": _text(project.description, 240),
                "technologies": _items(project.technologies, limit=12, chars=60),
            }
            for project in context.candidate_profile.projects
            if project.project_id in project_ids
        ],
        "remaining_seconds": context.state.remaining_seconds,
        "estimated_question_seconds": round(context.estimated_question_seconds),
        "closing_seconds": context.plan.closing_seconds,
        "reserve_seconds": context.plan.reserve_seconds,
        "eligible_topics": [
            {**item, "label": _text(item["label"], 270)} for item in eligible.values()
        ],
        "current_goals": goals,
        "closed_topic_keys": closed,
        "recent_changes": changes,
        "safety_guardrails": {
            "questions_remaining": max(
                0, context.plan.max_questions - context.state.question_index
            ),
            "max_questions_per_project": context.plan.max_questions_per_project,
            "max_questions_per_topic": context.plan.max_questions_per_topic,
        },
    }


def payload_chars(payload):
    """Comparable serialized size, not a tokenizer estimate."""
    return len(json.dumps(payload, ensure_ascii=False))
