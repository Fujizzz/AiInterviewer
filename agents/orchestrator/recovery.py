"""Recover a failed target without treating missing evidence as completion."""

from dataclasses import dataclass

from agents.domain.models import ProbeDecision, TopicSelection
from agents.planning.needs import open_needs, sync_needs
from agents.planning.pace import admission_cost, final_question_window
from agents.policies.dialogue_controller import DialogueController
from agents.policies.topic_selector import TopicSelector
from agents.question.fallback import _resume_label
from agents.tracing import emit_trace
from shared.contracts.planning import PlanRevision, TopicAllocation, TopicProgress

FAILURE_REASON = "QUESTION_GENERATION_FAILED"


class QuestionUnavailable(Exception):
    """An uncommitted local question failure, never a repository failure."""

    def __init__(self, plan=None, *, reason="NO_EXECUTABLE_PLAN_TARGET"):
        self.plan, self.reason = plan, reason
        super().__init__(reason)


@dataclass
class RecoveryTarget:
    project: object
    topic: TopicSelection
    probe: ProbeDecision
    goal: str


def quarantine(context, failure):
    plan = failure.plan
    if plan is None:
        return
    progress = context.topic_progress.setdefault(plan.topic_key, TopicProgress())
    # A rejected question is not an answer, and does not consume a question count.
    progress.status, progress.reason = "skipped", FAILURE_REASON
    sync_needs(progress, plan.topic_key)
    for need in progress.information_needs:
        if need.status == "open":
            need.status = "blocked"
    progress.next_need_id = None
    if context.active_thread and context.active_thread.topic_key == plan.topic_key:
        context.active_thread.closed_reason = FAILURE_REASON
        context.closed_threads.append(context.active_thread)
        context.active_thread = None
    emit_trace(
        "question.target_skipped",
        topic_key=plan.topic_key,
        need_id=getattr(plan, "need_id", ""),
        reason=failure.reason,
        coverage_status=progress.coverage_status,
    )


def recovery_target(context, settings):
    """Prefer fresh resume facts, then known gaps; reserve hypothetical goals for failures."""
    controller = DialogueController(context, settings)
    projects = context.candidate_profile.projects
    if projects and all(controller.project_exhausted(p.project_id) for p in projects):
        return None
    ordered = {item.topic_key: index for index, item in enumerate(context.plan.topics)}
    fresh, gaps = [], []
    for project in projects:
        if controller.project_exhausted(project.project_id):
            continue
        for topic in TopicSelector().candidates(project):
            progress = context.topic_progress.get(topic.topic_key, TopicProgress())
            if (
                progress.status in {"completed", "skipped"}
                or progress.coverage_status == "sufficient"
            ):
                continue
            if controller.topic_questions(topic.topic_key) >= context.plan.max_questions_per_topic:
                continue
            if topic.topic_key not in context.used_topic_keys:
                label = _resume_label(topic.topic, words=25, chars=160)
                fresh.append(
                    (project, topic, f"Describe one concrete implementation step for {label}")
                )
            elif progress.coverage_status == "partial":
                needs = open_needs(progress, topic.topic_key)
                if needs:
                    gaps.append((project, topic, needs[0].target))
    if not projects and "general:experience" not in context.used_topic_keys:
        fresh.append(
            (
                None,
                TopicSelection(
                    topic="your technical experience",
                    topic_key="general:experience",
                    reason_code="GENERAL_EXPERIENCE",
                ),
                "Describe one concrete technical implementation you personally worked on",
            )
        )
    choices = sorted(fresh or gaps, key=lambda item: ordered.get(item[1].topic_key, 10**6))
    if choices:
        project, topic, goal = choices[0]
    else:
        failures = (
            any(p.reason == FAILURE_REASON for p in context.topic_progress.values())
            or any(h.feedback.analysis_status != "valid" for h in context.question_history)
            or context.pending_replan_trigger == "RECOVERABLE_PLANNER_FAILURE"
        )
        if not failures:
            return None
        # These are explicitly hypothetical tasks, not invented resume experiences.
        # Distinct indexed failure cases prevent repeatedly asking a rejected goal.
        base_cases = (
            "an operation timing out",
            "duplicate request submission",
            "an invalid input",
            "a downstream service failing",
            "memory pressure",
            "a stale asynchronous result",
            "out-of-order outputs",
            "a partial write",
            "a malformed response",
            "a sudden increase in latency",
            "missing telemetry",
            "a dependency version change",
            "an empty result",
            "a resource leak",
            "a process restart",
            "an unexpected load spike",
        )
        cases = tuple(
            (phase, case)
            for case in base_cases
            for phase in (
                "first diagnostic step",
                "test you would use to verify the fix",
                "recovery trade-off you would consider",
            )
        )
        selected = None
        for owner in projects or [None]:
            if owner and controller.project_exhausted(owner.project_id):
                continue
            for index, (phase, case) in enumerate(cases):
                key = f"recovery:{owner.project_id if owner else 'general'}:{index}"
                if key not in context.used_topic_keys:
                    selected = (owner, key, phase, case)
                    break
            if selected:
                break
        if selected is None:
            # Do not publish FINISH merely because every recovery scope was attempted.
            raise RuntimeError(
                "No safe recovery target is currently available; session remains active"
            )
        project, key, phase, case = selected
        topic = TopicSelection(
            topic=f"Hypothetical handling of {case}",
            topic_key=key,
            reason_code="HYPOTHETICAL_RECOVERY_SCOPE",
        )
        goal = (
            f"Describe the {phase} if {case} occurred; "
            "this is a hypothetical scenario, not a claim about your experience"
        )
    active = context.active_thread
    action = (
        "new_project"
        if active and project and project.project_id != active.project_id
        else "new_topic"
    )
    return RecoveryTarget(
        project,
        topic,
        ProbeDecision(
            should_probe=False,
            next_probe_depth=1,
            reason_code="RECOVERED_NEXT_TOPIC",
            dialogue_action=action,
            information_goal=goal,
        ),
        goal,
    )


def activate_recovery(context, target, settings):
    """Allocate existing remaining time locally; do not ask a failing planner again."""
    key = target.topic.topic_key
    progress = context.topic_progress.setdefault(key, TopicProgress())
    progress.status = "pending"
    progress.reason = (
        "RESUMED_BY_PLAN" if key in context.used_topic_keys else "RECOVERED_BY_CONTROLLER"
    )
    progress.objective_id = progress.objective_id or key
    # Keep prior evidence/criteria intact. Only create a need for a fresh scope.
    if not any(n.target == target.goal for n in open_needs(progress, key)):
        progress.missing_information = list(
            dict.fromkeys(
                [
                    *progress.missing_information,
                    target.goal,
                ]
            )
        )
        sync_needs(progress, key)
    progress.next_need_id = next(
        n.need_id for n in open_needs(progress, key) if n.target == target.goal
    )
    if final_question_window(context, settings):
        context.plan.closing_seconds = 0
    remaining = max(1, context.state.remaining_seconds - context.plan.closing_seconds)
    seconds = min(remaining, admission_cost(context, settings))
    prior = next((t for t in context.plan.topics if t.topic_key == key), None)
    item = TopicAllocation(
        project_id=target.project.project_id if target.project else None,
        topic_key=key,
        objective=prior.objective if prior else target.goal,
        completion_criteria=prior.completion_criteria
        if prior
        else "One concrete diagnostic or implementation step with its rationale",
        budget_seconds=seconds + progress.elapsed_seconds,
        expected_questions=1 + progress.questions_asked,
    )
    # Previous remaining allocations become deferred, rather than over-allocating time.
    retained = []
    for old in context.plan.topics:
        if old.topic_key == key:
            continue
        old_progress = context.topic_progress.get(old.topic_key)
        if old_progress and old_progress.status in {"pending", "active"}:
            old_progress.status, old_progress.reason = "deferred", "RECOVERY_REALLOCATION"
        retained.append(old)
    context.plan.topics = [item, *retained]
    context.plan.reserve_seconds = max(0, remaining - seconds)
    context.plan.version += 1
    context.pending_replan_trigger = None
    revision = PlanRevision(
        version=context.plan.version,
        elapsed_seconds=context.state.elapsed_seconds,
        trigger="QUESTION_RECOVERY",
        reason="Continue another safe target after a local system failure",
        fallback_used=True,
        topics=[item],
        reserve_seconds=context.plan.reserve_seconds,
        closing_seconds=context.plan.closing_seconds,
        remaining_at_compile=context.state.remaining_seconds,
        source="local_compilation",
    )
    context.plan_history.append(revision)
    emit_trace("planning.recovery", revision=revision.model_dump(mode="json"))
