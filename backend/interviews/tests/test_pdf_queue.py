"""Responsibilities: Verify PDF queue failure, consumption isolation, and cancellation boundaries,
without accessing Redis or real models.

Implementation: Replace the message-transport boundary and execute the production queue generator
and task entry. These tests validate local behavior with stubs and do not establish Redis or Celery
availability.
Related Modules: pdf_queue, tasks, and resume_api's NDJSON protocol.
Declaration Index:
- PdfQueueTests: Queue lifecycle and one-time execution regression.
- PdfQueueTests.test_success_stream_and_cleanup: Return original terminal state and delete temporary
  body/consumption marker.
- PdfQueueTests.test_advanced_mode_reaches_worker:
  Queue and worker both transparently pass explicit advanced mode, no real Redis/model calls.
- PdfQueueTests.test_publish_failure_has_no_inline_fallback: Publishing failure does not invoke
  original pipeline or retry.
- PdfQueueTests.test_close_cancels_consumer: Browser closing stream notifies worker, does not retain
  consumption marker.
- PdfQueueTests.test_duplicate_task_does_not_call_pipeline: Already consumed input not reprocessed.
- PdfQueueTests.test_disconnected_task_does_not_consume: Consumed marker invalid tasks do not read
  PDF.
Variable Index:
None
"""

from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from interviews.pdf_queue import queued_resume_events
from interviews.tasks import execute_pdf


class PdfQueueTests(SimpleTestCase):
    """Simulate Redis/Celery port validation for state transition, do not treat stub passing as
    proof of external service availability.
    """

    async def test_success_stream_and_cleanup(self):
        """Single publish receives result; sent Celery parameters do not include body, client-side
        temporary key deleted.
        """
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
        self.assertEqual(send.call_args.kwargs["kwargs"], {"mode": "traditional"})
        self.assertGreaterEqual(redis.delete.await_count, 2)
        redis.aclose.assert_awaited_once()

    async def test_publish_failure_has_no_inline_fallback(self):
        """Publish failure produces only fixed error, exception body not leaked, no invocation of
        local model pipeline.
        """
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

    async def test_advanced_mode_reaches_worker(self):
        """Messages and Redis both stubbed; verify explicit advanced mode passed with publish
        parameters and worker pipeline.
        Does not prove external service availability.
        """
        redis = AsyncMock()
        line = b'{"type":"result"}\n'
        redis.xread.return_value = [(b"stream", [(b"1-0", {b"line": line, b"terminal": b"1"})])]
        with (
            patch("interviews.pdf_queue.redis_client", return_value=redis),
            patch("config.celery.app.send_task") as send,
        ):
            stream = queued_resume_events(b"synthetic", mode="advanced")
            await anext(stream)
            await anext(stream)
            await stream.aclose()
        self.assertEqual(send.call_args.kwargs["kwargs"], {"mode": "advanced"})
        redis.exists.return_value = 1
        redis.eval.return_value = b"synthetic"
        with (
            patch("interviews.tasks.redis_client", return_value=redis),
            patch("interviews.tasks.publish_events", new_callable=AsyncMock) as producer,
            patch("interviews.tasks.watch_client", new_callable=AsyncMock),
        ):
            await execute_pdf("test-job", mode="advanced")
        producer.assert_awaited_once_with(redis, "test-job", b"synthetic", mode="advanced")

    async def test_close_cancels_consumer(self):
        """Stream continues executing finally after queued close, worker observes consumer marker
        deletion.
        """
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
        """Atomically consuming empty input ends process, even if Celery retries, no repeated
        billing.
        """
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
        """Disconnected consumers do not read input or send model requests, even if task remains in
        queue.
        """
        redis = AsyncMock()
        redis.exists.return_value = 0
        with patch("interviews.tasks.redis_client", return_value=redis):
            await execute_pdf("test-job")
        redis.eval.assert_not_called()
        redis.aclose.assert_awaited_once()
