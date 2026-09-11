"""ASGI 回传协议回归测试。SimpleTestCase 禁止数据库访问，模拟接收与发送事件。

目录：
- StreamingTests
- StreamingTests.connect
- StreamingTests.accepted
- StreamingTests.message
- StreamingTests.test_ping_stream_and_in_memory_summary
- StreamingTests.test_foreign_origin_and_remote_peer_denied
- StreamingTests.test_protocol_errors_are_explicit
- StreamingTests.test_binary_before_start_rejected
- StreamingTests.test_out_of_order_frame_rejected
- StreamingTests.test_oversize_frame_rejected
- StreamingTests.test_incorrect_finish_counts_rejected
- StreamingTests.test_disconnect_is_not_success
- StreamingTests.test_stream_works_with_files_and_database_access_forbidden
"""

import hashlib
import json
import struct
from unittest.mock import patch

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase

from interviews.streaming.protocol import MAX_CHUNK_BYTES
from interviews.streaming.websocket import echo_socket


class StreamingTests(SimpleTestCase):
    # SimpleTestCase forbids database access: streaming has no persistence dependency.
    """禁止数据库访问的 ASGI 测试集合，用事件队列验证协议及无存储边界。"""

    async def connect(self, origin="http://localhost", client="127.0.0.1"):
        """构造带来源和客户端地址的 ASGI scope，发送连接事件并返回通信器。"""
        comm = ApplicationCommunicator(
            echo_socket,
            {
                "type": "websocket",
                "path": "/ws/echo/",
                "scheme": "ws",
                "client": (client, 12345),
                "headers": [(b"host", b"localhost"), (b"origin", origin.encode())],
            },
        )
        await comm.send_input({"type": "websocket.connect"})
        return comm

    async def accepted(self):
        """要求握手成功及 hello 公告，返回通信器和临时连接 ID。"""
        comm = await self.connect()
        self.assertEqual((await comm.receive_output())["type"], "websocket.accept")
        hello = json.loads((await comm.receive_output())["text"])
        self.assertEqual(hello["type"], "hello")
        return comm, hello["connection_id"]

    async def message(self, comm, data):
        """编码一条 JSON 控制消息并读取下一条 JSON 响应，复用协议测试步骤。"""
        await comm.send_input({"type": "websocket.receive", "text": json.dumps(data)})
        return json.loads((await comm.receive_output())["text"])

    async def test_ping_stream_and_in_memory_summary(self):
        """验证 ping、多个二进制哈希及原样回传，最后检查内存统计和正常关闭。"""
        comm, connection_id = await self.accepted()
        pong = await self.message(comm, {"type": "ping", "id": "test-1"})
        self.assertEqual((pong["type"], pong["id"]), ("pong", "test-1"))
        self.assertEqual(
            (
                await self.message(
                    comm, {"type": "start", "mode": "audio", "mime_type": "audio/wav"}
                )
            )["type"],
            "started",
        )
        payloads = [b"RIFF\x00\xffWAVE", bytes(range(256)) * 100, b"final"]
        for sequence, payload in enumerate(payloads, 1):
            frame = struct.pack("!I", sequence) + payload
            await comm.send_input({"type": "websocket.receive", "bytes": frame})
            ack = json.loads((await comm.receive_output())["text"])
            self.assertEqual(ack["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual((await comm.receive_output())["bytes"], frame)
        summary = await self.message(
            comm,
            {"type": "finish", "verified_chunks": 3, "verified_bytes": sum(map(len, payloads))},
        )
        self.assertEqual(summary["status"], "finished")
        self.assertEqual((await comm.receive_output())["code"], 1000)
        await comm.wait()
        self.assertEqual(summary["connection_id"], connection_id)
        self.assertEqual(summary["chunk_count"], 3)
        self.assertEqual(summary["byte_count"], sum(map(len, payloads)))
        self.assertEqual(summary["verified_chunks"], 3)

    async def test_foreign_origin_and_remote_peer_denied(self):
        """构造跨源或非本机握手，验证连接在接受前被拒绝。"""
        for kwargs in [{"origin": "https://example.com"}, {"client": "192.0.2.1"}]:
            comm = await self.connect(**kwargs)
            self.assertEqual((await comm.receive_output())["type"], "websocket.close")
            await comm.wait()

    async def test_protocol_errors_are_explicit(self):
        """逐一传入损坏 JSON、数组和未知类型，验证稳定错误码及 1008 关闭。"""
        for raw, code in [
            ("{", "invalid_json"),
            ("[]", "invalid_message"),
            ('{"type":"missing"}', "unknown_type"),
        ]:
            comm, _ = await self.accepted()
            await comm.send_input({"type": "websocket.receive", "text": raw})
            self.assertEqual(json.loads((await comm.receive_output())["text"])["code"], code)
            self.assertEqual((await comm.receive_output())["code"], 1008)
            await comm.wait()

    async def test_binary_before_start_rejected(self):
        """未声明模式即发送分片，要求 not_started 错误而非隐式选择模式。"""
        comm, _ = await self.accepted()
        await comm.send_input({"type": "websocket.receive", "bytes": b"\0\0\0\1x"})
        self.assertEqual(json.loads((await comm.receive_output())["text"])["code"], "not_started")
        await comm.receive_output()
        await comm.wait()

    async def test_out_of_order_frame_rejected(self):
        """首帧使用序号 2，验证严格顺序约束且不自动重排。"""
        comm, _ = await self.accepted()
        await self.message(
            comm, {"type": "start", "mode": "binary", "mime_type": "application/octet-stream"}
        )
        await comm.send_input({"type": "websocket.receive", "bytes": struct.pack("!I", 2) + b"x"})
        self.assertEqual(
            json.loads((await comm.receive_output())["text"])["code"], "sequence_mismatch"
        )
        await comm.receive_output()
        await comm.wait()

    async def test_oversize_frame_rejected(self):
        """构造刚超过既定上限的载荷，要求 size_limit 及 1009 关闭。"""
        comm, _ = await self.accepted()
        await self.message(
            comm, {"type": "start", "mode": "binary", "mime_type": "application/octet-stream"}
        )
        await comm.send_input(
            {
                "type": "websocket.receive",
                "bytes": struct.pack("!I", 1) + b"x" * (MAX_CHUNK_BYTES + 1),
            }
        )
        self.assertEqual(json.loads((await comm.receive_output())["text"])["code"], "size_limit")
        self.assertEqual((await comm.receive_output())["code"], 1009)
        await comm.wait()

    async def test_incorrect_finish_counts_rejected(self):
        """提交不匹配计数，验证连接不会报告虚假完成。"""
        comm, _ = await self.accepted()
        error = await self.message(
            comm, {"type": "finish", "verified_chunks": 1, "verified_bytes": 10}
        )
        self.assertEqual(error["code"], "verification_mismatch")
        await comm.receive_output()
        await comm.wait()

    async def test_disconnect_is_not_success(self):
        """模拟异常断线，验证任务退出且不额外发送成功消息。"""
        comm, _ = await self.accepted()
        await comm.send_input({"type": "websocket.disconnect", "code": 1006})
        await comm.wait()
        self.assertTrue(await comm.receive_nothing())

    async def test_stream_works_with_files_and_database_access_forbidden(self):
        """禁止文件打开和 SQLite 连接后完成分片回传，证明数据路径不依赖存储。"""
        with (
            patch("builtins.open", side_effect=AssertionError("Streaming must not open files")),
            patch(
                "sqlite3.connect",
                side_effect=AssertionError("Streaming must not connect to SQLite"),
            ),
        ):
            comm, _ = await self.accepted()
            await self.message(
                comm, {"type": "start", "mode": "binary", "mime_type": "application/octet-stream"}
            )
            await comm.send_input({"type": "websocket.receive", "bytes": b"\0\0\0\1x"})
            await comm.receive_output()
            self.assertEqual((await comm.receive_output())["bytes"], b"\0\0\0\1x")
            summary = await self.message(
                comm, {"type": "finish", "verified_chunks": 1, "verified_bytes": 1}
            )
            self.assertEqual(summary["status"], "finished")
            self.assertEqual((await comm.receive_output())["code"], 1000)
            await comm.wait()
