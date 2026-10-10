"""Speculative planning never publishes state; the next normal turn accepts preferences."""

import asyncio

from agents.tracing import emit_trace


class BackgroundReplanner:
    def __init__(self, planner):
        self.planner = planner
        self.tasks = {}
        self.sources = {}
        self.consumed = set()

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
            return
        stale = (
            proposal.base_plan_version != context.plan.version
            or any(
                key in context.topic_progress
                and progress.objective_version != context.topic_progress[key].objective_version
                for key, progress in source.topic_progress.items()
            )
            or (
                (
                    context.state.question_index != source.state.question_index
                    or context.processed_feedback_ids != source.processed_feedback_ids
                )
                and any(t.objective_change == "replace" for t in proposal.topics)
            )
        )
        if stale:
            emit_trace(
                "planning.background_discarded",
                interview_id=key,
                reason_code="STALE_PLAN_OR_OBJECTIVE",
            )
            return
        # Clock and eligible goals are re-read, so completion/refusal and consumed time win.
        self.planner.accept(context, proposal, "BACKGROUND_REPLAN")
        emit_trace(
            "planning.background_accepted",
            interview_id=key,
            source_state_version=source.state.state_version,
            plan_version=context.plan.version,
        )

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.sources.clear()
        self.consumed.clear()
