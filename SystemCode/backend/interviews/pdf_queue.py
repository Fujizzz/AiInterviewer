"""Responsibilities: deliver authenticated PDF requests to Celery, and convert Redis progress back
into the original NDJSON stream.

Implementation: store only short-term Redis keys in the body; messages contain only random ID and
selected mode; atomically fetch and delete to prevent duplicate execution.
Related Modules: resume_api selects explicitly configured execution method; tasks consume jobs,
while frontend maintains original upload and cancellation protocol.
Declaration Index:
- redis_client: establish an asynchronous Redis client without retry logic.
- queue_key: construct isolated short-term key names, not accepting user-specified keys.
- queued_resume_events: submit one task, forward progress sequentially, notify worker of
  disconnection to cancel and clean up the body.
Variable Index:
- logger: logs only task ID and exception type, not upload content or Redis credentials.
- JOB_TTL: upper limit seconds for data retention on abnormal exit, not used as model invocation
  timeout.
- CLIENT_TTL: effective seconds for consumer active marker; worker stops task after process
  disappears.
- QUEUE_WAIT: maximum wait seconds before worker starts task.
"""

import asyncio
import logging
from time import monotonic
from uuid import uuid4

from django.conf import settings
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

logger = logging.getLogger(__name__)
JOB_TTL = 3600
CLIENT_TTL = 10
QUEUE_WAIT = 30


def redis_client():
    """Read task Redis URL, return client; propagate connection failure directly, no fallback or
    retry.
    """
    return Redis.from_url(
        settings.PDF_TASK_REDIS_URL,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry=Retry(NoBackoff(), 0),
    )


def queue_key(job_id, part):
    """Input: server-generated task ID and fixed purpose, output: Redis key; no I/O involved."""
    return f"ai-interviewer:pdf:{job_id}:{part}"


async def queued_resume_events(data, *, mode="traditional"):
    """Input: bounded PDF and mode (default: traditional), output: events; mode is passed with the
    task, no business persistence.

    Publish failure returns error explicitly. Reading events is progress subscription, no task
    retry; terminal state or disconnection invalidates client.
    Worker heartbeat loss results in explicit failure; consumed input cannot be reused by another
    task, preventing duplicate billing.
    """
    from config.celery import app

    from .resume_api import event_line

    job_id = uuid4().hex
    redis = redis_client()
    started = monotonic()
    seen_worker = False
    cursor = "0-0"
    try:
        if mode not in {"traditional", "advanced"}:
            raise ValueError("invalid extraction mode")
        await redis.set(queue_key(job_id, "input"), data, ex=JOB_TTL, nx=True)
        await redis.set(queue_key(job_id, "client"), "1", ex=CLIENT_TTL)
        await asyncio.to_thread(
            app.send_task,
            "interviews.parse_pdf",
            args=[job_id],
            kwargs={"mode": mode},
            task_id=job_id,
            retry=False,
            expires=QUEUE_WAIT,
        )
        logger.info("PDF queued job=%s bytes=%d", job_id, len(data))
        yield event_line(
            "progress",
            stage="queued",
            detail=("Task submitted; waiting for background processing."),
        )
        while True:
            await redis.expire(queue_key(job_id, "client"), CLIENT_TTL)
            events = await redis.xread({queue_key(job_id, "events"): cursor}, block=1000)
            for _, records in events:
                for event_id, fields in records:
                    cursor = event_id
                    yield fields[b"line"]
                    if fields[b"terminal"] == b"1":
                        return
            alive = await redis.exists(queue_key(job_id, "worker"))
            if alive:
                seen_worker = True
            elif seen_worker:
                raise RuntimeError("PDF worker heartbeat ended before terminal event")
            elif monotonic() - started > QUEUE_WAIT:
                raise TimeoutError("PDF worker did not accept task")
    except asyncio.CancelledError:
        logger.info("PDF consumer cancelled job=%s", job_id)
        raise
    except Exception as exc:
        logger.error("PDF queue failed job=%s exception=%s", job_id, type(exc).__name__)
        yield event_line(
            "error",
            stage="queue",
            detail=("Background processing is unavailable. Submit the task again later."),
        )
    finally:
        try:
            await redis.delete(queue_key(job_id, "client"), queue_key(job_id, "input"))
            await redis.delete(queue_key(job_id, "events"))
        except Exception as exc:
            logger.error("PDF queue cleanup failed job=%s exception=%s", job_id, type(exc).__name__)
        await redis.aclose()
