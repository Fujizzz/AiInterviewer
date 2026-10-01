"""职责：把已认证 PDF 请求交付 Celery，将 Redis 中的进度转回原 NDJSON 流。

实现：正文只存短期 Redis 键，消息只含随机 ID；原子取走并删除 防止重投重复调用模型。
关联：resume_api 选择明确配置的执行方式；tasks 消费任务，前端保持原上传和取消协议。
目录：
- redis_client：建立无重试的异步 Redis 客户端。
- queue_key：构造隔离的短期键名，不接受用户指定键。
- queued_resume_events：提交一次任务、顺序转发进度，断线通知 worker 取消并清理正文。
关键变量：
- logger：只记录任务 ID 和异常类别，不记录上传内容或 Redis 凭据。
- JOB_TTL：异常退出时数据保留的上限秒数，不作为模型调用超时。
- CLIENT_TTL：消费者活跃标记有效秒数，进程消失后 worker 停止任务。
- QUEUE_WAIT：worker 开始任务前的等待上限秒数。
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
    """读取任务 Redis URL，返回客户端；连接失败直接传播，不回退或重试。"""
    return Redis.from_url(
        settings.PDF_TASK_REDIS_URL,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry=Retry(NoBackoff(), 0),
    )


def queue_key(job_id, part):
    """输入服务端生成的任务 ID 和固定用途，返回 Redis 键；无 I/O。"""
    return f"ai-interviewer:pdf:{job_id}:{part}"


async def queued_resume_events(data):
    """输入已限制大小的 PDF，输出原 progress/page/result/error 行；不持久化业务记录。

    发布失败明确返回 error。读取事件是进度订阅，不重试任务；终态或断线使客户端标记失效。
    Worker 心跳丢失明确失败；取走的输入无法被重复任务再次消费，避免重复计费。
    """
    from config.celery import app

    from .resume_api import event_line

    job_id = uuid4().hex
    redis = redis_client()
    started = monotonic()
    seen_worker = False
    cursor = "0-0"
    try:
        await redis.set(queue_key(job_id, "input"), data, ex=JOB_TTL, nx=True)
        await redis.set(queue_key(job_id, "client"), "1", ex=CLIENT_TTL)
        await asyncio.to_thread(
            app.send_task,
            "interviews.parse_pdf",
            args=[job_id],
            task_id=job_id,
            retry=False,
            expires=QUEUE_WAIT,
        )
        logger.info("PDF queued job=%s bytes=%d", job_id, len(data))
        yield event_line("progress", stage="queued", detail="任务已提交，等待后台处理")
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
        yield event_line("error", stage="queue", detail="后台处理不可用，请稍后手动重新提交。")
    finally:
        try:
            await redis.delete(queue_key(job_id, "client"), queue_key(job_id, "input"))
            await redis.delete(queue_key(job_id, "events"))
        except Exception as exc:
            logger.error("PDF queue cleanup failed job=%s exception=%s", job_id, type(exc).__name__)
        await redis.aclose()
