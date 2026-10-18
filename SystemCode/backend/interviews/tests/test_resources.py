"""Responsibilities: Verify same-machine capacity, real ASGI upload boundary, and sandbox return
data rejection semantics.
Implementation: Use real OS process locks; HTTP uses actual Django ASGI; visual and sandbox outputs
explicitly simulated.
Related Modules: capacity, resource_gate, pdf_sandbox; tests do not invoke models or modify
production database.

Declaration Index:
- ResourceTests: Test resource boundaries in independent temporary directories.
- ResourceTests.setUp: Configure independent capacity directories and single-slot test limits per
  test.
- ResourceTests.test_process_lock_and_death: Full capacity across processes, process death releases
  lock automatically.
- ResourceTests.test_retained_lease: Lease remains held after request completion, still occupying
  slot.
- ResourceTests.test_http_capacity_before_body: Full capacity rejects immediately before reading
  upload body.
- ResourceTests.test_websocket_capacity: Full capacity prevents Agent startup before handshake.
- ResourceTests.test_cancel_releases_slot: Downstream cancellation returns request slot.
- ResourceTests.test_cancel_releases_slot.blocked: Maintain downstream at controlled wait point.
- ResourceTests.test_header_limit: Excessive Content-Length rejected without reading body.
- ResourceTests.test_actual_asgi_chunked_limit: Actual Django entry rejects cumulative or
  fake-length uploads without length.
- ResourceTests.test_invalid_sandbox_output: Reject out-of-order, invalid encoding, and oversized
  output.
- ResourceTests.test_missing_sandbox_configuration: Missing configuration fails immediately, no
  non-isolated parsing initiated.
- ResourceTests.test_output_limit: Exceeding output limit causes explicit failure, no partial data
  delivered.

Variable Index:
None

Constraints:
Test capacity 1 and micro read limit are used solely for boundary construction, not modifying
production defaults.
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
    """Test resource boundaries in independent temporary directories; no database or external model
    credentials required.
    """

    def setUp(self):
        """Configure independent capacity directories and single-slot test limits per test; clean up
        by restoring environment before deleting directories.
        """
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
        """Parent process blocked at full capacity while real subprocess holds lock; force-terminate
        subprocess so kernel releases lock without cleaning lock files.
        """
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
            # sys.executable is the actual interpreter, avoiding Windows venv launcher spawning
            # derived process trees.
            child.kill()
            child.communicate(timeout=10)
        take_slot("pdf", 1).release()

    def test_retained_lease(self):
        """Simulate synchronous call holding reference; new requests still rejected after request
        ends, release only after actual call completes.
        """
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
        """Original PDF, version upload, and version parsing share original slot; full capacity
        returns 503, no body read or model invoked.
        """
        lease = take_slot("pdf", 1)
        app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
        try:
            for path in [
                "/api/resume/parse/",
                "/api/resume-versions/",
                "/api/resume-versions/00000000-0000-0000-0000-000000000001/parse/",
            ]:
                await limited_application(app, {**self.scope, "path": path}, receive, send)
        finally:
            lease.release()
        self.assertEqual(send.call_args_list[0].args[0]["status"], 503)
        app.assert_not_called()
        receive.assert_not_called()

    async def test_websocket_capacity(self):
        """Full capacity before handshake reads only connect and closes, does not run Agent; actual
        HTTP handshake state determined by server.
        """
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
        """On request cancellation, execute finally and return slot; do not convert cancellation
        into success response.
        """
        entered = asyncio.Event()

        async def blocked(*args):
            """Maintain downstream at controlled wait point; external cancellation is the only exit
            method in this test.
            """
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
        """Excessive Content-Length rejected before Django body buffering, no request chunk read.
        """
        app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
        self.scope["headers"] = [(b"content-length", str(MAX_BYTES + 65537).encode())]
        await limited_application(app, self.scope, receive, send)
        self.assertEqual(send.call_args_list[0].args[0]["status"], 413)
        app.assert_not_called()
        receive.assert_not_called()

    async def test_actual_asgi_chunked_limit(self):
        """Actual Django ASGI body buffering hits cumulative limit and returns 413; verify not
        converted to 500 by framework.
        """
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
        """Simulate malicious worker output; reject out-of-order, invalid encoding, and oversized
        PNG headers; do not decode image in parent process.
        """
        header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 1801, 1) + b"\0" * 9
        page = {"number": 1, "raw_text": "x", "text": "x", "warnings": [], "image_png": "!"}
        for changes in ({"number": 2}, {}, {"image_png": base64.b64encode(header).decode()}):
            with patch(
                "interviews.pdf_sandbox.run_sandbox", return_value={"pages": [{**page, **changes}]}
            ):
                with self.assertRaises(PdfInputError):
                    await parse_pdf(b"test")

    async def test_missing_sandbox_configuration(self):
        """Missing sandbox-specific configuration causes immediate failure; no local parsing or
        backup process started.
        """
        with (
            patch.dict(os.environ, {"PDF_SANDBOX_RUNTIME": ""}),
            patch("interviews.pdf_sandbox.asyncio.create_subprocess_exec") as launch,
        ):
            with self.assertRaises(PdfInputError):
                await run_sandbox(b"test")
            launch.assert_not_called()

    async def test_output_limit(self):
        """Do not return partial output when exceeding specified limit; protect parent process
        buffer.
        """
        reader = asyncio.StreamReader()
        reader.feed_data(b"12345")
        reader.feed_eof()
        with self.assertRaises(PdfInputError):
            await read_bounded(reader, 4)
