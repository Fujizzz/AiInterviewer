"""职责：验证 PDF 队列失败、消费隔离和取消边界，不访问 Redis 或真实模型。

实现：只替换消息传输端口，真实执行队列生成器及任务入口；外部集成另由部署探针验证。
关联：pdf_queue、tasks 和 resume_api 的 NDJSON 协议。
目录：
- PdfQueueTests：队列生命周期和一次性执行回归。
- PdfQueueTests.test_success_stream_and_cleanup：返回原终态并删除临时正文/消费标记。
- PdfQueueTests.test_publish_failure_has_no_inline_fallback：发布失败不调用原管线或重发。
- PdfQueueTests.test_close_cancels_consumer：浏览器关闭流会通知 worker，不保留消费标记。
- PdfQueueTests.test_duplicate_task_does_not_call_pipeline：已被取走的输入不重复处理。
- PdfQueueTests.test_disconnected_task_does_not_consume：消费标记失效的任务不读取 PDF。
关键变量：
（无模块级变量。）
"""

from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from interviews.pdf_queue import queued_resume_events
from interviews.tasks import execute_pdf


class PdfQueueTests(SimpleTestCase):
    """模拟 Redis/Celery 端口验证状态转换，不把替身通过解释为外部服务可用。"""

    async def test_success_stream_and_cleanup(self):
        """单次发布后收到 result；发送的 Celery 参数不包含正文，客户端临时键被删除。"""
        redis = AsyncMock()
        line = b'{"type":"result","text":"synthetic"}\n'
        redis.xread.return_value = [(b"stream", [(b"1-0", {b"line": line, b"terminal": b"1"})])]
        with (
            patch("interviews.pdf_queue.redis_client", return_value=redis),
            patch("config.celery.app.send_task") as send,
        ):
            stream = queued_resume_events(b"%PDF-synthetic")
            self.assertIn(b'"queued"', await anext(stream))
            self.assertEqual(await anext(stream), line)
            await stream.aclose()
        send.assert_called_once()
        self.assertNotIn(b"%PDF-synthetic", send.call_args.kwargs["args"])
        self.assertFalse(send.call_args.kwargs["retry"])
        self.assertGreaterEqual(redis.delete.await_count, 2)
        redis.aclose.assert_awaited_once()

    async def test_publish_failure_has_no_inline_fallback(self):
        """发布失败只产生固定 error，异常正文不泄漏，不调用本机模型管线。"""
        redis = AsyncMock()
        with (
            patch("interviews.pdf_queue.redis_client", return_value=redis),
            patch(
                "config.celery.app.send_task", side_effect=RuntimeError("private detail")
            ) as send,
            patch("interviews.resume_api.resume_events") as inline,
        ):
            lines = [line async for line in queued_resume_events(b"pdf")]
        self.assertEqual(len(lines), 1)
        self.assertIn(b'"error"', lines[0])
        self.assertNotIn(b"private detail", lines[0])
        send.assert_called_once()
        inline.assert_not_called()

    async def test_close_cancels_consumer(self):
        """流在 queued 后关闭仍执行 finally，worker 将观察到消费者标记删除。"""
        redis = AsyncMock()
        with (
            patch("interviews.pdf_queue.redis_client", return_value=redis),
            patch("config.celery.app.send_task"),
        ):
            stream = queued_resume_events(b"pdf")
            await anext(stream)
            await stream.aclose()
        deleted = [key for call in redis.delete.call_args_list for key in call.args]
        self.assertTrue(any(key.endswith(":client") for key in deleted))
        self.assertTrue(any(key.endswith(":input") for key in deleted))

    async def test_duplicate_task_does_not_call_pipeline(self):
        """原子取走输入返回空时结束，即使 Celery 重投也不能重复计费。"""
        redis = AsyncMock()
        redis.exists.return_value = 1
        redis.eval.return_value = None
        with (
            patch("interviews.tasks.redis_client", return_value=redis),
            patch("interviews.tasks.publish_events") as producer,
        ):
            await execute_pdf("test-job")
        producer.assert_not_called()
        redis.eval.assert_awaited_once()

    async def test_disconnected_task_does_not_consume(self):
        """已经断开的消费者即使任务尚在队列，也不读取输入或发送模型请求。"""
        redis = AsyncMock()
        redis.exists.return_value = 0
        with patch("interviews.tasks.redis_client", return_value=redis):
            await execute_pdf("test-job")
        redis.eval.assert_not_called()
        redis.aclose.assert_awaited_once()
