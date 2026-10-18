"""Reconcile speculative preferences with current goals without restoring old evidence."""

from agents.planning.allocation import PlanConstraintError
from agents.planning.projects import project_allowed
from shared.contracts.planning import TopicPreference


def prune_unavailable(proposal, known, eligible):
    """Remove known unavailable topics; malformed identity still invalidates the proposal."""
    result = proposal.model_copy(deep=True)
    adjustments, selected, seen = [], [], set()
    for item in result.topics:
        key = item.topic_key
        if key not in known:
            raise PlanConstraintError("UNKNOWN_TOPIC", topic_key=key)
        if key in seen:
            raise PlanConstraintError("DUPLICATE_TOPIC", topic_key=key)
        seen.add(key)
        if hasattr(item, "project_id") and item.project_id != known[key]["project_id"]:
            raise PlanConstraintError("PROJECT_TOPIC_MISMATCH", topic_key=key)
        if key not in eligible:
            adjustments.append(
                {
                    "action": "background_topic_filtered",
                    "topic_key": key,
                    "reason": "TOPIC_NO_LONGER_ELIGIBLE",
                }
            )
        else:
            selected.append(item)
    if not selected:
        raise PlanConstraintError("NO_VALID_BACKGROUND_TOPICS")
    result.topics = selected
    return result, adjustments


def reconcile(proposal, source, context, known, eligible):
    """Keep current goal contracts while merging still-usable agenda preferences."""
    result, adjustments = prune_unavailable(proposal, known, eligible)
    existing = {item.topic_key: item for item in context.plan.topics}
    source_goals = {item.topic_key: item for item in source.plan.topics}
    active_key = context.active_thread.topic_key if context.active_thread else None
    new_turn = (
        context.state.question_index != source.state.question_index
        or context.processed_feedback_ids != source.processed_feedback_ids
        or context.applied_assessment_ids != source.applied_assessment_ids
    )
    selected = []
    for item in result.topics:
        key = item.topic_key
        if not project_allowed(context, item.project_id, revisit_projects=result.revisit_projects):
            adjustments.append(
                {
                    "action": "background_topic_filtered",
                    "topic_key": key,
                    "reason": "PROJECT_ALREADY_LEFT",
                }
            )
            continue
        previous = existing.get(key)
        old_progress = source.topic_progress.get(key)
        progress = context.topic_progress.get(key)
        objective_changed = (old_progress.objective_version if old_progress else None) != (
            progress.objective_version if progress else None
        )
        facts_changed = bool(
            old_progress
            and progress
            and old_progress.model_dump(exclude={"elapsed_seconds"})
            != progress.model_dump(exclude={"elapsed_seconds"})
        )
        protected = previous and (
            objective_changed
            or facts_changed
            or (item.objective_change == "replace" and (new_turn or key == active_key))
            or (key not in source_goals and item.objective_change == "replace")
        )
        if protected:
            adjustments.append(
                {
                    "action": "background_objective_preserved",
                    "topic_key": key,
                    "reason": "OBJECTIVE_CHANGED"
                    if objective_changed
                    else "CURRENT_GOAL_PROTECTED",
                }
            )
            item.objective_change = "preserve"
        if previous and item.objective_change == "preserve":
            item.objective = previous.objective
            item.completion_criteria = previous.completion_criteria
        selected.append(item)
    if not selected:
        raise PlanConstraintError("NO_VALID_BACKGROUND_TOPICS")
    # Omission must not retire the goal of an outstanding/current technical question.
    if (
        active_key in eligible
        and active_key in existing
        and active_key not in {item.topic_key for item in selected}
    ):
        active = existing[active_key]
        prior = context.plan_history[-1].proposal if context.plan_history else None
        preference = (
            next((p for p in prior.topics if p.topic_key == active_key), None) if prior else None
        )
        selected.insert(
            0,
            TopicPreference(
                project_id=active.project_id,
                topic_key=active_key,
                objective=active.objective,
                completion_criteria=active.completion_criteria,
                relative_weight=preference.relative_weight if preference else 1,
                depth=preference.depth if preference else "standard",
            ),
        )
        adjustments.append(
            {
                "action": "background_objective_preserved",
                "topic_key": active_key,
                "reason": "ACTIVE_GOAL_OMITTED",
            }
        )
    result.topics = selected
    return result, adjustments
