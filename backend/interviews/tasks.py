"""职责：在独立 Celery 进程运行既有 PDF 沙箱及视觉校对，保持模型参数不变。

实现：原子消费输入；短期 Redis stream 承载进度，消费者消失时取消整条异步管线。
关联：pdf_queue 提交任务，resume_api.resume_events 保持页数、并发和失败语义。
目录：
- publish_events：消费既有管线并向私有 stream 写入有序事件。
- watch_client：续 worker 心跳并监视消费者存活，断线后取消管线。
- execute_pdf：验证一次性输入，协调生产者/取消监视并清理资源。
- parse_pdf_task：Celery 同步任务入口，不返回正文、不重试。
- worker_probe：返回空结果并写短期探针键，供部署验证真实任务消费。
关键变量：
- logger：仅记录任务 ID、状态和异常类型。
"""

import asyncio
import json
import logging
from contextlib import aclosing

from config.celery import app

from .pdf_queue import JOB_TTL, queue_key, redis_client

logger = logging.getLogger(__name__)


async def publish_events(redis, job_id, data):
    """输入 Redis、任务 ID 和 PDF；完整消费原管线，按顺序写事件，异常保持传播。"""
    from .resume_api import resume_events

    async with aclosing(resume_events(data)) as stream:
        async for line in stream:
            terminal = json.loads(line)["type"] in {"result", "error"}
            async with redis.pipeline(transaction=True) as pipe:
                pipe.xadd(queue_key(job_id, "events"), {"line": line, "terminal": int(terminal)})
                pipe.expire(queue_key(job_id, "events"), JOB_TTL)
                await pipe.execute()


async def watch_client(redis, job_id):
    """每秒续五秒 worker 心跳；客户端退出或过期时返回，调用方取消未完成管线。"""
    while await redis.exists(queue_key(job_id, "client")):
        await redis.set(queue_key(job_id, "worker"), "1", ex=5)
        await asyncio.sleep(1)


async def execute_pdf(job_id):
    """输入仅为任务 ID；一次性取得 PDF，监视断线，始终等待异步取消与客户端清理。

    重复投递或过期任务不再执行；Redis 故障日志只含类别并传播，不触发本机处理回退。
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
        producer = asyncio.create_task(publish_events(redis, job_id, data))
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
def parse_pdf_task(job_id):
    """Celery 仅接收服务端 UUID；执行一次事件循环，异常记录类别后抛固定异常以脱敏。"""
    try:
        asyncio.run(execute_pdf(job_id))
    except Exception as exc:
        logger.error("PDF task failed job=%s exception=%s", job_id, type(exc).__name__)
        raise RuntimeError("PDF background task failed; inspect worker logs") from None


@app.task(name="interviews.worker_probe", max_retries=0, ignore_result=True)
def worker_probe(probe_id):
    """输入随机部署探针 ID，写入短期成功标记；不调用模型、不读用户数据。"""
    from django.conf import settings
    from redis import Redis

    with Redis.from_url(settings.PDF_TASK_REDIS_URL, socket_timeout=5) as redis:
        redis.set(f"ai-interviewer:probe:{probe_id}", "ok", ex=30)
