"""Compile semantic priorities into an executable, clock-bounded agenda."""

from collections import Counter

from agents.planning.pace import final_question_window, round_cost
from shared.contracts.planning import PlanDraft, TopicAllocation, TopicProgress


class PlanConstraintError(ValueError):
    def __init__(self, code, *, topic_key=None):
        super().__init__(code)
        self.code = code
        self.topic_key = topic_key


class BudgetAllocator:
    """Budget arithmetic is deterministic; model preferences never grant extra time."""

    def __init__(self, settings):
        self.settings = settings
        self.minimum = settings.planning.minimum_question_seconds

    def compile(self, proposal, context, eligible, *, owners):
        seen = set()
        for item in proposal.topics:
            if item.topic_key in seen:
                raise PlanConstraintError("DUPLICATE_TOPIC", topic_key=item.topic_key)
            seen.add(item.topic_key)
            if item.topic_key not in eligible:
                raise PlanConstraintError(
                    "CLOSED_OR_EXHAUSTED_TOPIC" if item.topic_key in owners else "UNKNOWN_TOPIC",
                    topic_key=item.topic_key,
                )
            if eligible[item.topic_key]["project_id"] != item.project_id:
                raise PlanConstraintError("PROJECT_TOPIC_MISMATCH", topic_key=item.topic_key)
        if not proposal.topics and eligible:
            raise PlanConstraintError("EMPTY_AGENDA")

        minimum = round_cost(context, self.settings)
        remaining = max(0, context.state.remaining_seconds)
        closing = min(
            context.plan.closing_seconds if context.plan.version else min(120, remaining // 15),
            remaining,
        )
        final_question = final_question_window(
            context, self.settings, closing_seconds=closing
        )
        if final_question:
            # Duration controls whether another question starts, not how long an
            # already issued answer may take. Allocate one final scope locally.
            closing = 0
            minimum = min(minimum, remaining)
        reserve = min(
            180,
            remaining // 10,
            max(0, remaining - closing - minimum) if proposal.topics else remaining,
        )
        if final_question:
            reserve = 0
        usable = max(0, remaining - closing - reserve)
        project_counts = Counter()
        for key, progress in context.topic_progress.items():
            project_counts[owners.get(key)] += progress.questions_asked
        total_slots = max(0, context.plan.max_questions - context.state.question_index)
        selected, adjustments = [], []
        for item in proposal.topics:
            project_slots = context.plan.max_questions_per_project - project_counts[item.project_id]
            topic_slots = (
                context.plan.max_questions_per_topic
                - context.topic_progress.get(item.topic_key, TopicProgress()).questions_asked
            )
            reason = None
            if min(project_slots, topic_slots, total_slots) <= 0:
                reason = "SAFETY_CAPACITY_EXHAUSTED"
            elif (len(selected) + 1) * minimum > usable:
                reason = "INSUFFICIENT_ALLOCATION_TIME"
            if reason:
                adjustments.append(
                    {"topic_key": item.topic_key, "action": "deferred", "reason": reason}
                )
                continue
            selected.append(item)
            project_counts[item.project_id] += 1
            total_slots -= 1

        allocations = []
        if selected:
            weights = [
                t.relative_weight * {"brief": 1, "standard": 2, "deep": 3}[t.depth]
                for t in selected
            ]
            spare = usable - minimum * len(selected)
            amounts = [minimum + int(spare * w / sum(weights)) for w in weights]
            # Assign rounding remainders in declared priority order, never exceed remaining.
            for index in range(usable - sum(amounts)):
                amounts[index % len(amounts)] += 1
            pace = minimum
            for item, seconds in zip(selected, amounts, strict=True):
                # This is only a pace estimate. Actual admission enforces safety limits.
                topic_remaining = (
                    context.plan.max_questions_per_topic
                    - context.topic_progress.get(item.topic_key, TopicProgress()).questions_asked
                )
                extras = min(
                    max(0, seconds // pace - 1),
                    total_slots,
                    context.plan.max_questions_per_project - project_counts[item.project_id],
                    max(0, topic_remaining - 1),
                )
                estimate = 1 + extras
                total_slots -= extras
                project_counts[item.project_id] += extras
                allocations.append(
                    TopicAllocation(
                        project_id=item.project_id,
                        topic_key=item.topic_key,
                        objective=item.objective,
                        completion_criteria=item.completion_criteria,
                        budget_seconds=seconds,
                        expected_questions=estimate,
                    )
                )
                adjustments.append(
                    {
                        "topic_key": item.topic_key,
                        "action": "allocated",
                        "budget_seconds": seconds,
                        "expected_questions": estimate,
                    }
                )
        else:
            reserve = max(0, remaining - closing)
        return PlanDraft(
            topics=allocations,
            reserve_seconds=reserve,
            closing_seconds=closing,
            reason=proposal.reason,
        ), adjustments
