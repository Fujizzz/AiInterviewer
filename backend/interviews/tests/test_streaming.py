"""Responsibilities: Verify the streaming WebSocket protocol, frame validation, and storage-free
echo path.

Implementation: Use SimpleTestCase and ApplicationCommunicator to drive the ASGI handler in process;
the suite does not use a database or external service.
Related Modules: interviews.streaming.protocol, interviews.streaming.websocket, and
asgiref.testing.ApplicationCommunicator.

Declaration Index:
- StreamingTests:
  ASGI test suite without database access, validates protocol and storage-free boundaries using
  event queues.
- StreamingTests.connect:
  Construct ASGI scope with source and client address, send connect event, return communicator.
- StreamingTests.accepted:
  Require successful handshake and hello announcement, return communicator and temporary connection
  ID.
- StreamingTests.message:
  Encode one JSON control message and read next JSON response, reusing protocol test steps.
- StreamingTests.test_ping_stream_and_in_memory_summary:
  Validate ping, multiple binary hashes, and exact echo-back, finally check memory stats and normal
  shutdown.
- StreamingTests.test_foreign_origin_and_remote_peer_denied:
  Construct cross-origin or non-local handshake, verify connection rejected before acceptance.
- StreamingTests.test_protocol_errors_are_explicit:
  Pass damaged JSON, arrays, and unknown types one by one, validate stable error codes and 1008
  closure.
- StreamingTests.test_binary_before_start_rejected:
  Sending chunks without declaring mode requires not_started error, not implicit mode selection.
- StreamingTests.test_out_of_order_frame_rejected:
  First frame uses sequence number 2, validate strict ordering constraint without automatic
  reordering.
- StreamingTests.test_oversize_frame_rejected:
  Construct payload just exceeding preset limit, require size_limit and 1009 closure.
- StreamingTests.test_incorrect_finish_counts_rejected:
  Submit mismatched counts, validate connection does not report false completion.
- StreamingTests.test_disconnect_is_not_success:
  Simulate abnormal disconnection, validate task exit without extra success message.
- StreamingTests.test_stream_works_with_files_and_database_access_forbidden:
  Echo chunks while file opening and SQLite connections are forbidden, checking that this path does
  not access storage.

Variable Index:
None
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
    """ASGI test suite without database access, validates protocol and storage-free boundaries using
    event queues.
    """

    async def connect(self, origin="http://localhost", client="127.0.0.1"):
        """Construct ASGI scope with source and client address, send connect event, return
        communicator.
        """
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
        """Require successful handshake and hello announcement, return communicator and temporary
        connection ID.
        """
        comm = await self.connect()
        self.assertEqual((await comm.receive_output())["type"], "websocket.accept")
        hello = json.loads((await comm.receive_output())["text"])
        self.assertEqual(hello["type"], "hello")
        return comm, hello["connection_id"]

    async def message(self, comm, data):
        """Encode one JSON control message and read next JSON response, reusing protocol test steps.
        """
        await comm.send_input({"type": "websocket.receive", "text": json.dumps(data)})
        return json.loads((await comm.receive_output())["text"])

    async def test_ping_stream_and_in_memory_summary(self):
        """Validate ping, multiple binary hashes, and exact echo-back, finally check memory stats
        and normal shutdown.
        """
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
        """Construct cross-origin or non-local handshake, verify connection rejected before
        acceptance.
        """
        for kwargs in [{"origin": "https://example.com"}, {"client": "192.0.2.1"}]:
            comm = await self.connect(**kwargs)
            self.assertEqual((await comm.receive_output())["type"], "websocket.close")
            await comm.wait()

    async def test_protocol_errors_are_explicit(self):
        """Pass damaged JSON, arrays, and unknown types one by one, validate stable error codes and
        1008 closure.
        """
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
        """Sending chunks without declaring mode requires not_started error, not implicit mode
        selection.
        """
        comm, _ = await self.accepted()
        await comm.send_input({"type": "websocket.receive", "bytes": b"\0\0\0\1x"})
        self.assertEqual(json.loads((await comm.receive_output())["text"])["code"], "not_started")
        await comm.receive_output()
        await comm.wait()

    async def test_out_of_order_frame_rejected(self):
        """First frame uses sequence number 2, validate strict ordering constraint without automatic
        reordering.
        """
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
        """Construct payload just exceeding preset limit, require size_limit and 1009 closure.
        """
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
        """Submit mismatch count; validation connection will not report false completion.
        """
        comm, _ = await self.accepted()
        error = await self.message(
            comm, {"type": "finish", "verified_chunks": 1, "verified_bytes": 10}
        )
        self.assertEqual(error["code"], "verification_mismatch")
        await comm.receive_output()
        await comm.wait()

    async def test_disconnect_is_not_success(self):
        """Simulate abnormal disconnection; verify task exits without sending extra success
        messages.
        """
        comm, _ = await self.accepted()
        await comm.send_input({"type": "websocket.disconnect", "code": 1006})
        await comm.wait()
        self.assertTrue(await comm.receive_nothing())

    async def test_stream_works_with_files_and_database_access_forbidden(self):
        """Prohibit file opening and SQLite connection after shard return completion, proving data
        path does not depend on storage.
        """
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
