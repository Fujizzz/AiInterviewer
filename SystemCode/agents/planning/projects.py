"""Keep project visits contiguous using durable threads and explicit replan grants."""

from agents.planning.pace import round_cost
from shared.contracts.planning import TopicPreference


def unvisited_projects(context):
    """Only committed technical questions count; intro and model selection do not."""
    visited = set(project_route(context)["left_project_ids"])
    visited.add(project_route(context)["active_project_id"])
    visited.update(e.question.project_id for e in context.question_history)
    visited.update(p for p, count in context.state.project_visit_count.items() if count)
    return [p.project_id for p in context.candidate_profile.projects if p.project_id not in visited]


def coverage_due(context, settings):
    """Do not spend the remaining first-visit slots on optional depth."""
    pending = unvisited_projects(context) if context.plan.planning_enabled else []
    if not pending:
        return False
    slots = context.plan.max_questions - context.state.question_index
    available = context.state.remaining_seconds - context.plan.closing_seconds
    return slots <= len(pending) or available <= (len(pending) + 1) * round_cost(context, settings)


def preserve_project_entries(context, proposal, eligible):
    """Model preferences may remove bullets, never an entire unvisited project."""
    present = {t.project_id for t in proposal.topics}
    existing = {t.topic_key: t for t in context.plan.topics}
    adjustments = []
    for owner in unvisited_projects(context):
        if owner in present:
            continue
        choices = [v for v in eligible.values() if v["project_id"] == owner]
        if not choices:
            adjustments.append(
                {
                    "action": "project_coverage_unavailable",
                    "project_id": owner,
                    "reason": "NO_ELIGIBLE_TOPIC",
                }
            )
            continue
        topic = next((v for v in choices if v["topic_key"] in existing), choices[0])
        old = existing.get(topic["topic_key"])
        label = topic["label"].replace("?", "").replace("？", "")[:220]
        proposal.topics.append(
            TopicPreference(
                project_id=owner,
                topic_key=topic["topic_key"],
                objective=old.objective
                if old
                else f"Assess one implementation mechanism within {label}",
                completion_criteria=old.completion_criteria
                if old
                else "A concrete personal implementation mechanism",
                depth="brief",
                objective_change="preserve" if old else "replace",
            )
        )
        adjustments.append(
            {
                "action": "project_entry_preserved",
                "project_id": owner,
                "topic_key": topic["topic_key"],
                "reason": "ALL_PROJECTS_REQUIRED",
            }
        )
    return adjustments


def project_route(context):
    """An uncommitted selection or intro does not start a project visit."""
    threads = [*context.closed_threads]
    if context.active_thread:
        threads.append(context.active_thread)
    seen, visits = set(), []
    for thread in threads:
        if thread.thread_id in seen:
            continue
        seen.add(thread.thread_id)
        if thread.project_id and (not visits or visits[-1] != thread.project_id):
            visits.append(thread.project_id)
    current = visits[-1] if visits else None
    return {
        "active_project_id": current,
        "left_project_ids": list(dict.fromkeys(p for p in visits[:-1] if p != current)),
        "visit_index": len(visits),
    }


def permitted_revisits(context):
    """A model grant lasts until the next project transition, including local recompiles."""
    route = project_route(context)
    return {
        item["project_id"]
        for revision in context.plan_history
        for item in revision.adjustments
        if item.get("action") == "project_revisit_allowed"
        and item.get("from_project_id") == route["active_project_id"]
        and item.get("visit_index") == route["visit_index"]
    }


def project_allowed(context, project_id, *, revisit_projects=None):
    if not context.plan.planning_enabled:
        return True
    return (
        project_id not in project_route(context)["left_project_ids"]
        or project_id in permitted_revisits(context)
        or bool((revisit_projects or {}).get(project_id))
    )


def order_project_topics(context, topics):
    """Stable project groups; prioritize the current visit without changing goal text."""
    topics = list(topics)
    if not context.plan.planning_enabled:
        return topics
    current = project_route(context)["active_project_id"]
    groups = {}
    for item in topics:
        groups.setdefault(item.project_id, []).append(item)
    owners = list(groups)
    if current in groups:
        owners.remove(current)
        owners.insert(0, current)
    return [item for owner in owners for item in groups[owner]]


def constrain_project_order(context, proposal):
    """Apply before allocating time; skipped returns never acquire evidence coverage."""
    adjustments, allowed = [], []
    for item in proposal.topics:
        if project_allowed(context, item.project_id, revisit_projects=proposal.revisit_projects):
            allowed.append(item)
        else:
            adjustments.append(
                {
                    "topic_key": item.topic_key,
                    "action": "deferred",
                    "reason": "PROJECT_ALREADY_LEFT",
                }
            )
    ordered = order_project_topics(context, allowed)
    if ordered != allowed:
        adjustments.append(
            {
                "action": "project_topics_grouped",
                "reason": "CURRENT_PROJECT_FIRST",
                "topic_keys": [item.topic_key for item in ordered],
            }
        )
    route = project_route(context)
    for project_id, reason in proposal.revisit_projects.items():
        if project_id in route["left_project_ids"] and any(
            item.project_id == project_id for item in ordered
        ):
            adjustments.append(
                {
                    "action": "project_revisit_allowed",
                    "project_id": project_id,
                    "from_project_id": route["active_project_id"],
                    "visit_index": route["visit_index"],
                    "reason": reason,
                }
            )
    proposal.topics = ordered
    return adjustments
