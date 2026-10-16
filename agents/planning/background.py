"""Speculative planning never publishes state; the next normal turn accepts preferences."""

import asyncio

from agents.planning.allocation import PlanConstraintError
from agents.planning.planner import catalog
from agents.planning.projects import project_route
from agents.planning.reconciliation import reconcile
from agents.tracing import emit_trace


class BackgroundReplanner:
    def __init__(self, planner):
        self.planner = planner
        self.tasks = {}
        self.sources = {}
        self.consumed = set()
        self.adjustments = {}

    def request(self, context, trigger):
        # Durable intent survives a lost task. One task per interview coalesces triggers.
        context.pending_replan_trigger = trigger
        task = self.tasks.get(context.interview_id)
        if task is None or task.done():
            context.last_planner_request_question_index = context.state.question_index
            context.last_planner_estimated_question_seconds = context.estimated_question_seconds
        emit_trace("planning.deferred", interview_id=context.interview_id, trigger=trigger)

    def start(self, context):
        key = context.interview_id
        # Called only after a successful turn commit. CAS recomputation must keep the proposal.
        if key in self.consumed:
            self.consumed.discard(key)
            self.tasks.pop(key, None)
            self.sources.pop(key, None)
            self.adjustments.pop(key, None)
        if (
            context.state.status == "finished"
            or not context.pending_replan_trigger
            or key in self.tasks
        ):
            return
        source = context.model_copy(deep=True)
        self.sources[key] = source
        self.tasks[key] = asyncio.create_task(self._propose(source))

    async def _propose(self, source):
        trigger = source.pending_replan_trigger
        draft = source.model_copy(deep=True)
        try:
            await self.planner.revise(draft, trigger, speculative=True)
            revision = draft.plan_history[-1]
            if revision.source != "model" or revision.fallback_used:
                return None
            self.adjustments[source.interview_id] = [
                a
                for a in revision.adjustments
                if a["action"] == "background_topic_filtered"
                or a.get("reason") == "PROJECT_ALREADY_LEFT"
            ]
            return revision.proposal
        except Exception as error:
            emit_trace(
                "planning.background_failed",
                interview_id=source.interview_id,
                error_type=type(error).__name__,
            )
            return None

    def consume(self, context):
        key = context.interview_id
        task = self.tasks.get(key)
        if task is None or not task.done():
            return
        source = self.sources[key]
        self.consumed.add(key)
        proposal = None if task.cancelled() else task.result()
        context.pending_replan_trigger = None
        if proposal is None:
            emit_trace(
                "planning.background_unavailable",
                interview_id=key,
                reason_code="MODEL_UNAVAILABLE_OR_INVALID",
            )
            return False
        # Clock and eligible goals are re-read, so completion/refusal and consumed time win.
        try:
            if context.state.status == "finished":
                raise PlanConstraintError("INTERVIEW_FINISHED")
            if proposal.base_plan_version != context.plan.version:
                raise PlanConstraintError("STALE_PLAN_VERSION")
            if proposal.revisit_projects and project_route(source) != project_route(context):
                raise PlanConstraintError("PROJECT_VISIT_CHANGED")
            proposal, adjustments = reconcile(
                proposal, source, context, catalog(context), self.planner._eligible(context)
            )
            adjustments = [*self.adjustments.get(key, []), *adjustments]
            # Failed compilation cannot leave goal evidence or allocations partially changed.
            draft = context.model_copy(deep=True)
            self.planner.accept(
                draft,
                proposal,
                "INITIAL_PLAN"
                if source.pending_replan_trigger == "INITIAL_PLAN"
                else "BACKGROUND_REPLAN",
                project_adjustments=adjustments,
            )
            if not any(a["action"] == "allocated" for a in draft.plan_history[-1].adjustments):
                raise PlanConstraintError("NO_EXECUTABLE_BACKGROUND_TOPICS")
        except PlanConstraintError as error:
            emit_trace(
                "planning.background_discarded",
                interview_id=key,
                reason_code=error.code,
            )
            return False
        for field in (
            "plan",
            "state",
            "topic_progress",
            "plan_history",
            "last_replan_question_index",
        ):
            setattr(context, field, getattr(draft, field))
        emit_trace(
            "planning.background_accepted",
            interview_id=key,
            source_state_version=source.state.state_version,
            plan_version=context.plan.version,
            adoption="partial" if adjustments else "full",
            adjustments=adjustments,
            semantic_changes=context.plan_history[-1].semantic_changes,
            allocations=[
                a for a in context.plan_history[-1].adjustments if a["action"] == "allocated"
            ],
        )
        return True

    def discard(self, interview_id):
        """A committed transition retires its speculative task without waiting for the model."""
        task = self.tasks.pop(interview_id, None)
        if task is not None and not task.done():
            task.cancel()
        self.sources.pop(interview_id, None)
        self.consumed.discard(interview_id)
        self.adjustments.pop(interview_id, None)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.sources.clear()
        self.consumed.clear()
        self.adjustments.clear()
