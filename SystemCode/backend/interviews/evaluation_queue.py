"""ASGI uses persisted jobs; only the independent worker dispatches scoring calls."""

import asyncio
from time import perf_counter

from django.conf import settings


class QueuedEvaluationAdapter:
    def __init__(self, adapter):
        self.adapter = adapter
        self.formal, self.legacy = adapter.formal, adapter.legacy

    async def evaluate(self, request):
        return await self.adapter.evaluate(request)

    def start_background(self, interview_id):
        # commit_turn already enqueued atomically with the immutable answer.
        return None

    async def drain(self, interview_id, *, timeout_seconds=None):
        deadline = perf_counter() + (
            settings.EVALUATION_FINAL_WAIT_SECONDS if timeout_seconds is None else timeout_seconds
        )
        repo = self.adapter.repository
        while True:
            jobs = await repo.get_shadow_jobs(interview_id)
            records = {r.input.request_id for r in await repo.get_evaluation_records(interview_id)}
            assessments = {r.request_id for r in await repo.get_assessment_records(interview_id)}
            if all(
                (not j.shadow_enabled or j.request.request_id in records)
                and (not j.assessment_requested or j.request.request_id in assessments)
                for j in jobs
            ):
                return True
            if perf_counter() >= deadline:
                return False
            await asyncio.sleep(min(0.1, max(0, deadline - perf_counter())))

    async def close(self):
        # No worker or provider in this process owns the queued scoring work.
        await self.adapter.close()
