"""Responsibilities: Run PDF sandbox and optional visual proofreading in isolated Celery process,
preserving model parameters.

Implementation: Atomic consumption of input; short-term Redis stream holds progress, canceling
entire async pipeline when consumer vanishes.
Related Modules: pdf_queue submits tasks, resume_api.resume_events maintains page count,
concurrency, and failure semantics.

Declaration Index:
- publish_events: Consume existing pipeline and write ordered events to private stream.
- watch_client: Resume worker heartbeat and monitor consumer liveness; cancel pipeline on
  disconnection.
- execute_pdf: Validate one-time input, coordinate producer/cancellation monitoring, and clean up
  resources.
- parse_pdf_task: Celery synchronous task entry point, returns no body, does not retry.
- worker_probe: Return empty result and write short-lived probe key, for deployment validation of
  real task consumption.

Variable Index:
- logger: Logs only task ID, status, and exception type.
"""

import asyncio
import json
import logging
from contextlib import aclosing

from config.celery import app

from .pdf_queue import JOB_TTL, queue_key, redis_client

logger = logging.getLogger(__name__)


async def publish_events(redis, job_id, data, *, mode="traditional"):
    """Input: Redis, task ID, PDF, and mode; fully consume selected pipeline, write events
    sequentially, propagate exceptions.
    """
    from .resume_api import resume_events

    async with aclosing(resume_events(data, mode=mode)) as stream:
        async for line in stream:
            terminal = json.loads(line)["type"] in {"result", "error"}
            async with redis.pipeline(transaction=True) as pipe:
                pipe.xadd(queue_key(job_id, "events"), {"line": line, "terminal": int(terminal)})
                pipe.expire(queue_key(job_id, "events"), JOB_TTL)
                await pipe.execute()


async def watch_client(redis, job_id):
    """Resume worker heartbeat every five seconds; return on client exit or expiration, causing
    caller to cancel unfinished pipeline.
    """
    while await redis.exists(queue_key(job_id, "client")):
        await redis.set(queue_key(job_id, "worker"), "1", ex=5)
        await asyncio.sleep(1)


async def execute_pdf(job_id, *, mode="traditional"):
    """Input: task ID and mode; obtain PDF once, monitor disconnection, always wait for cancellation
    and cleanup.

    Repeated delivery or expired tasks are not executed; Redis failure logs contain only category
    and propagate, without triggering local fallback handling.
    """
    redis = redis_client()
    tasks = []
    try:
        if not await redis.exists(queue_key(job_id, "client")):
            return
        data = await redis.eval(
            "local v=redis.call('GET',KEYS[1]); redis.call('DEL',KEYS[1]); return v",
            1,
            queue_key(job_id, "input"),
        )
        if data is None:
            logger.warning("PDF task has no unconsumed input job=%s", job_id)
            return
        logger.info("PDF worker started job=%s", job_id)
        await redis.set(queue_key(job_id, "worker"), "1", ex=5)
        producer = asyncio.create_task(publish_events(redis, job_id, data, mode=mode))
        watcher = asyncio.create_task(watch_client(redis, job_id))
        tasks = [producer, watcher]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if watcher in done and not producer.done():
            logger.info("PDF worker cancelled by consumer job=%s", job_id)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not await redis.exists(queue_key(job_id, "client")):
            await redis.delete(queue_key(job_id, "events"))
        await redis.aclose()
        logger.info("PDF worker ended job=%s", job_id)


@app.task(name="interviews.parse_pdf", max_retries=0, ignore_result=True)
def parse_pdf_task(job_id, *, mode="traditional"):
    """Input: server UUID and selected mode; execute one event loop, log exception category then
    raise fixed sanitized exception.
    """
    try:
        asyncio.run(execute_pdf(job_id, mode=mode))
    except Exception as exc:
        logger.error("PDF task failed job=%s exception=%s", job_id, type(exc).__name__)
        raise RuntimeError("PDF background task failed; inspect worker logs") from None


@app.task(name="interviews.worker_probe", max_retries=0, ignore_result=True)
def worker_probe(probe_id):
    """Input: random deployment probe ID; write short-lived success marker; do not invoke model, do
    not read user data.
    """
    from django.conf import settings
    from redis import Redis

    with Redis.from_url(settings.PDF_TASK_REDIS_URL, socket_timeout=5) as redis:
        redis.set(f"ai-interviewer:probe:{probe_id}", "ok", ex=30)
