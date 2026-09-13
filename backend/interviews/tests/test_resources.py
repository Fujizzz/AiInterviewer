"""职责：验证同机容量、真实 ASGI 上传边界及沙箱返回数据的拒绝语义。
实现：操作系统锁使用真实子进程；HTTP 使用实际 Django ASGI；视觉和沙箱输出显式模拟。
关联：capacity、resource_gate、pdf_sandbox；测试不调用模型、不修改生产数据库。

目录：
- ResourceTests：以独立临时目录测试资源边界。
- ResourceTests.setUp：为每个测试配置独立容量目录与单名额测试上限。
- ResourceTests.test_process_lock_and_death：跨进程满额且进程死亡后自动释放。
- ResourceTests.test_retained_lease：请求结束后借用引用仍占名额。
- ResourceTests.test_http_capacity_before_body：满额在读取上传前明确拒绝。
- ResourceTests.test_websocket_capacity：握手前满额不启动 Agent。
- ResourceTests.test_cancel_releases_slot：下游取消后归还请求名额。
- ResourceTests.test_cancel_releases_slot.blocked：保持下游在可控等待点。
- ResourceTests.test_header_limit：超大 Content-Length 不读取正文。
- ResourceTests.test_actual_asgi_chunked_limit：实际 Django 入口累计无长度或虚假长度上传。
- ResourceTests.test_invalid_sandbox_output：拒绝错序、非法编码和尺寸超限。
- ResourceTests.test_missing_sandbox_configuration：缺配置直接失败，不启动非隔离解析。
- ResourceTests.test_output_limit：输出超过上限时明确失败，不交付部分数据。

关键变量：
（无模块级变量。）

约束：
测试中的容量 1 与微型读取上限仅用于构造边界，不修改生产默认值。
"""

import asyncio
import base64
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase

from interviews.capacity import CapacityExceeded, take_slot
from interviews.pdf_sandbox import parse_pdf, read_bounded, run_sandbox
from interviews.resource_gate import limited_application
from interviews.resume_pdf import MAX_BYTES, PdfInputError


class ResourceTests(SimpleTestCase):
    """以独立临时目录测试资源边界；不需要数据库或外部模型凭据。"""

    def setUp(self):
        """为每个测试配置独立容量目录与单名额测试上限；清理顺序先恢复环境再删目录。"""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = patch.dict(
            os.environ,
            {
                "SERVICE_CAPACITY_DIR": self.temp.name,
                "SERVICE_PDF_UPLOADS": "1",
                "SERVICE_AGENT_CONNECTIONS": "1",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.scope = {"type": "http", "method": "POST", "path": "/api/resume/parse/", "headers": []}

    def test_process_lock_and_death(self):
        """真实子进程持锁时父进程满额，强制结束子进程后内核释放锁，无需清理锁文件。"""
        source = (
            "from interviews.capacity import take_slot; "
            "import sys; lease=take_slot('pdf',1); print('ready',flush=True); sys.stdin.read()"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", source],
            cwd=Path(__file__).resolve().parents[2],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            self.assertEqual(
                child.stdout.readline(), b"ready\r\n" if os.name == "nt" else b"ready\n"
            )
            with self.assertRaises(CapacityExceeded):
                take_slot("pdf", 1)
        finally:
            # sys.executable 是实际解释器，避免 Windows venv 启动器派生进程树。
            child.kill()
            child.communicate(timeout=10)
        take_slot("pdf", 1).release()

    def test_retained_lease(self):
        """模拟同步调用借用引用；请求结束后仍拒绝新请求，实际调用完成才释放。"""
        lease = take_slot("agent", 1)
        lease.retain()
        lease.release()
        try:
            with self.assertRaises(CapacityExceeded):
                take_slot("agent", 1)
        finally:
            lease.release()
        take_slot("agent", 1).release()

    async def test_http_capacity_before_body(self):
        """上传名额耗尽时返回 503，不读取正文、不调用后续解析或模型。"""
        lease = take_slot("pdf", 1)
        app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
        try:
            await limited_application(app, self.scope, receive, send)
        finally:
            lease.release()
        self.assertEqual(send.call_args_list[0].args[0]["status"], 503)
        app.assert_not_called()
        receive.assert_not_called()

    async def test_websocket_capacity(self):
        """握手前满额只读取 connect 后关闭，不运行 Agent；实际 HTTP 握手状态由服务器决定。"""
        lease = take_slot("agent", 1)
        app, send = AsyncMock(), AsyncMock()
        receive = AsyncMock(return_value={"type": "websocket.connect"})
        try:
            await limited_application(
                app, {"type": "websocket", "path": "/ws/agent/"}, receive, send
            )
        finally:
            lease.release()
        send.assert_awaited_once_with({"type": "websocket.close", "code": 1013})
        app.assert_not_called()

    async def test_cancel_releases_slot(self):
        """请求被取消时执行 finally 并归还名额，不将取消改成成功响应。"""
        entered = asyncio.Event()

        async def blocked(*args):
            """保持下游在可控等待点；外部取消是本测试的唯一退出方式。"""
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(
            limited_application(blocked, self.scope, AsyncMock(), AsyncMock())
        )
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        take_slot("pdf", 1).release()

    async def test_header_limit(self):
        """超大 Content-Length 在 Django 正文暂存前被拒绝，不读取任何请求分片。"""
        app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
        self.scope["headers"] = [(b"content-length", str(MAX_BYTES + 65537).encode())]
        await limited_application(app, self.scope, receive, send)
        self.assertEqual(send.call_args_list[0].args[0]["status"], 413)
        app.assert_not_called()
        receive.assert_not_called()

    async def test_actual_asgi_chunked_limit(self):
        """实际 Django ASGI 正文暂存遇到累计超限返回 413，验证不是被框架转换成 500。"""
        from config.asgi import application

        for lengths in ([], [(b"content-length", b"1")]):
            scope = {
                **self.scope,
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "scheme": "http",
                "query_string": b"",
                "server": ("127.0.0.1", 80),
                "client": ("127.0.0.1", 50000),
                "headers": [(b"host", b"127.0.0.1"), *lengths],
            }
            communicator = ApplicationCommunicator(application, scope)
            try:
                await communicator.send_input(
                    {"type": "http.request", "body": b"x" * MAX_BYTES, "more_body": True}
                )
                await communicator.send_input(
                    {"type": "http.request", "body": b"x" * 65537, "more_body": False}
                )
                event = await communicator.receive_output(timeout=5)
                self.assertEqual(event["status"], 413)
                await communicator.receive_output(timeout=5)
                await communicator.wait(timeout=5)
            finally:
                communicator.stop()
            take_slot("pdf", 1).release()

    async def test_invalid_sandbox_output(self):
        """模拟恶意工作进程输出，拒绝错序、非法编码和过大 PNG 头，不在父进程解码图像。"""
        header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 1801, 1) + b"\0" * 9
        page = {"number": 1, "raw_text": "x", "text": "x", "warnings": [], "image_png": "!"}
        for changes in ({"number": 2}, {}, {"image_png": base64.b64encode(header).decode()}):
            with patch(
                "interviews.pdf_sandbox.run_sandbox", return_value={"pages": [{**page, **changes}]}
            ):
                with self.assertRaises(PdfInputError):
                    await parse_pdf(b"test")

    async def test_missing_sandbox_configuration(self):
        """缺沙箱专用配置时直接失败，明确没有启动本机解析或备用进程。"""
        with (
            patch.dict(os.environ, {"PDF_SANDBOX_RUNTIME": ""}),
            patch("interviews.pdf_sandbox.asyncio.create_subprocess_exec") as launch,
        ):
            with self.assertRaises(PdfInputError):
                await run_sandbox(b"test")
            launch.assert_not_called()

    async def test_output_limit(self):
        """读取超过指定上限时不返回部分输出，保护父进程缓冲。"""
        reader = asyncio.StreamReader()
        reader.feed_data(b"12345")
        reader.feed_eof()
        with self.assertRaises(PdfInputError):
            await read_bounded(reader, 4)
