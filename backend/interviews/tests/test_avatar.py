"""Responsibilities: Verify avatar configuration, admission, frame limits and transport cleanup.
Implementation: Use Django request/ASGI fixtures and a queued fake upstream for boundary tests;
one integration case opens a temporary loopback WebSocket server using the installed client.
Related Modules: interviews/avatar/ provides configuration and proxying; config/asgi.py and
interviews/session_socket.py provide routing and the account gate.

Declaration Index:
- FakeUpstream: Provide observable message queues and cleanup without an external server.
- FakeUpstream.__init__: Initialize queues and lifetime markers for a single fake connection.
- FakeUpstream.__aenter__: Expose the fake upstream through its async context manager.
- FakeUpstream.__aexit__: Record release of the fake upstream context.
- FakeUpstream.recv: Deliver queued frames/errors and record reader cancellation.
- FakeUpstream.send: Queue forwarded browser frames for assertions.
- AvatarConfigTests: Verify safe public settings with Django request and settings fixtures.
- AvatarConfigTests.test_local_default_and_production_disabled: Preserve local defaults while
  disabling remote streaming unless explicitly enabled.
- AvatarConfigTests.test_remote_endpoint_is_request_origin: Use same-origin secure routes and
  reject remote plaintext requests.
- AvatarConfigTests.test_production_config_requires_login: Enforce the HTTP session gate.
- AvatarConfigTests.test_upstream_cannot_be_selected_from_public_network: Restrict upstreams to
  explicit loopback WS endpoints without credentials, paths, queries or fragments.
- AvatarSocketTests: Verify authenticated proxy admission, transport limits and resource lifetime.
- AvatarSocketTests.scope: Build an authenticated loopback ASGI scope with explicit overrides.
- AvatarSocketTests.rejected: Assert rejection occurs before the upstream client is called.
- AvatarSocketTests.test_disabled_rejects_before_upstream: Reject disabled remote transport.
- AvatarSocketTests.test_wrong_origin_rejects_before_upstream: Reject a cross-origin player.
- AvatarSocketTests.test_malformed_origin_rejects_before_upstream: Reject an invalid Origin URL.
- AvatarSocketTests.test_session_gate_rejects_anonymous: Reject the mocked anonymous session.
- AvatarSocketTests.test_session_gate_rejects_anonymous.guarded: Exercise the session wrapper
  around the proxy under the anonymous-user fixture.
- AvatarSocketTests.test_production_requires_browser_origin: Reject a missing production Origin.
- AvatarSocketTests.test_bidirectional_frames_and_disconnect_cleanup: Forward frames both ways
  and release both pumps when the browser disconnects.
- AvatarSocketTests.test_production_origin_is_forwarded_without_browser_credentials: Preserve
  the admitted Origin while omitting the browser Cookie from the upstream handshake.
- AvatarSocketTests.test_server_close_releases_browser_receiver: Release the proxy after a normal
  upstream close.
- AvatarSocketTests.test_invalid_frames_close_and_release_upstream: Bound binary and UTF-8 frames
  and close with the expected protocol code.
- AvatarSocketTests.test_backpressure_timeout_closes_and_cancels_both_pumps: Bound a blocked send
  and release its peer pump and upstream.
- AvatarSocketTests.test_backpressure_timeout_closes_and_cancels_both_pumps.slow_send: Keep the
  mocked upstream send pending until the proxy deadline cancels it.
- AvatarSocketTests.test_application_cancellation_releases_upstream: Clean up when the ASGI
  application task is cancelled externally.
- AvatarSocketTests.test_unavailable_upstream_does_not_accept: Reject an unavailable upstream
  without accepting the browser or exposing private exception text.
- AvatarSocketTests.test_real_loopback_transport_through_asgi_route: Exercise the installed
  WebSocket client and ASGI route against a temporary local server.
- AvatarSocketTests.test_real_loopback_transport_through_asgi_route.signalling: Send config,
  echo one offer and record the temporary server connection's closure.

Variable Index:
None

Constraints:
No GPU, browser, speech provider or interview model is used; the server is loopback only.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.testing import ApplicationCommunicator
from config.asgi import application
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, SimpleTestCase, override_settings
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

from interviews.avatar.configuration import browser_config, signalling_upstream
from interviews.avatar.socket import MAX_MESSAGE_BYTES, avatar_socket
from interviews.session_socket import authenticated_socket


class FakeUpstream:
    """Provide a bounded in-process transport and observable lifetime for proxy tests."""

    def __init__(self):
        """Create queues and cleanup markers for assertions on one mocked upstream lifetime."""
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.closed = False
        self.reader_cancelled = False

    async def __aenter__(self):
        """Return this upstream fixture without opening a network connection."""
        return self

    async def __aexit__(self, *args):
        """Record context release regardless of the proxy's normal or exceptional exit."""
        self.closed = True

    async def recv(self):
        """Return a queued message or raise its error; record cancellation of a blocked reader."""
        try:
            message = await self.incoming.get()
        except asyncio.CancelledError:
            self.reader_cancelled = True
            raise
        if isinstance(message, Exception):
            raise message
        return message

    async def send(self, message):
        """Place one forwarded browser frame in the observable outgoing queue."""
        await self.outgoing.put(message)


class AvatarConfigTests(SimpleTestCase):
    """Use Django requests and settings overrides to verify endpoint and account boundaries."""

    @override_settings(AVATAR_REMOTE_ENABLED=False)
    def test_local_default_and_production_disabled(self):
        """With remote streaming disabled, retain loopback WS and disable uncached HTTPS config."""
        self.assertEqual(
            self.client.get("/api/avatar/config/", HTTP_HOST="localhost").json(),
            {"enabled": True, "signalling_url": "ws://127.0.0.1:8889"},
        )
        response = self.client.get("/api/avatar/config/", secure=True)
        self.assertEqual(response.json(), {"enabled": False, "signalling_url": ""})
        self.assertIn("no-store", response["Cache-Control"])

    @override_settings(AVATAR_REMOTE_ENABLED=True, ALLOWED_HOSTS=["interview.example", "localhost"])
    def test_remote_endpoint_is_request_origin(self):
        """With streaming enabled, use the request origin and reject non-loopback plaintext HTTP."""
        response = self.client.get(
            "/api/avatar/config/", secure=True, HTTP_HOST="interview.example"
        )
        self.assertEqual(
            response.json(),
            {"enabled": True, "signalling_url": "wss://interview.example/ws/avatar/"},
        )
        local = RequestFactory().get("/", HTTP_HOST="localhost:8765")
        self.assertEqual(browser_config(local)["signalling_url"], "ws://localhost:8765/ws/avatar/")
        remote_http = RequestFactory().get("/", HTTP_HOST="interview.example")
        self.assertEqual(browser_config(remote_http), {"enabled": False, "signalling_url": ""})

    @override_settings(INTERVIEW_REQUIRE_LOGIN=True)
    def test_production_config_requires_login(self):
        """Require the existing HTTP account gate when production login checks are enabled."""
        self.assertEqual(self.client.get("/api/avatar/config/").status_code, 401)

    def test_upstream_cannot_be_selected_from_public_network(self):
        """Reject unsafe upstream URLs while accepting explicit IPv6 loopback."""
        for value in (
            "ws://remote.example:8889",
            "wss://localhost:8889",
            "ws://localhost",
            "ws://user:secret@localhost:8889",
            "ws://127.0.0.1:8889/path",
            "ws://127.0.0.1:8889/?url=external",
            "ws://127.0.0.1:8889/#fragment",
        ):
            with self.subTest(value=value), override_settings(AVATAR_SIGNALLING_UPSTREAM=value):
                with self.assertRaises(ImproperlyConfigured):
                    signalling_upstream()
        with override_settings(AVATAR_SIGNALLING_UPSTREAM="ws://[::1]:9999"):
            self.assertEqual(signalling_upstream(), "ws://[::1]:9999")


@override_settings(AVATAR_REMOTE_ENABLED=True, AVATAR_SIGNALLING_UPSTREAM="ws://127.0.0.1:8889")
class AvatarSocketTests(SimpleTestCase):
    """Use ASGI scopes and fake upstreams to verify admission, bounds and cancellation cleanup."""

    def scope(self, **changes):
        """Return an authenticated loopback scope with caller-supplied boundary-test overrides."""
        scope = {
            "type": "websocket",
            "path": "/ws/avatar/",
            "scheme": "ws",
            "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")],
            "client": ("127.0.0.1", 12345),
            "user": SimpleNamespace(is_authenticated=True),
        }
        return {**scope, **changes}

    async def rejected(self, scope=None):
        """Patch the upstream client and assert policy closure occurs before any connection call."""
        with patch("interviews.avatar.socket.connect") as connect:
            communicator = ApplicationCommunicator(avatar_socket, scope or self.scope())
            await communicator.send_input({"type": "websocket.connect"})
            self.assertEqual(
                await communicator.receive_output(), {"type": "websocket.close", "code": 1008}
            )
            await communicator.wait()
            connect.assert_not_called()

    @override_settings(AVATAR_REMOTE_ENABLED=False)
    async def test_disabled_rejects_before_upstream(self):
        """Disable the feature and assert rejection without contacting an upstream."""
        await self.rejected()

    async def test_wrong_origin_rejects_before_upstream(self):
        """Reject an unrelated Origin before the mocked upstream client is used."""
        await self.rejected(
            self.scope(headers=[(b"host", b"localhost"), (b"origin", b"https://other.example")])
        )

    async def test_malformed_origin_rejects_before_upstream(self):
        """Reject an invalid Origin URL before attempting a connection."""
        await self.rejected(self.scope(headers=[(b"host", b"localhost"), (b"origin", b"http://[")]))

    @override_settings(INTERVIEW_REQUIRE_LOGIN=True)
    async def test_session_gate_rejects_anonymous(self):
        """Mock an anonymous session and ensure the authentication wrapper never opens a proxy."""
        async def guarded(scope, receive, send):
            """Apply the real session wrapper to the proxy under the test's mocked user lookup."""
            await authenticated_socket(avatar_socket, scope, receive, send)

        with patch(
            "interviews.session_socket.session_user",
            new=AsyncMock(return_value=SimpleNamespace(is_authenticated=False)),
        ):
            with patch("interviews.avatar.socket.connect") as connect:
                communicator = ApplicationCommunicator(guarded, self.scope())
                await communicator.send_input({"type": "websocket.connect"})
                self.assertEqual(
                    await communicator.receive_output(), {"type": "websocket.close", "code": 1008}
                )
                await communicator.wait()
                connect.assert_not_called()

    @override_settings(INTERVIEW_REQUIRE_LOGIN=True)
    async def test_production_requires_browser_origin(self):
        """Enable production login policy and reject a scope without a browser Origin header."""
        await self.rejected(self.scope(headers=[(b"host", b"localhost")]))

    async def test_bidirectional_frames_and_disconnect_cleanup(self):
        """Use queued upstream frames to verify serial forwarding and release after disconnect."""
        upstream = FakeUpstream()
        await upstream.incoming.put('{"type":"config"}')
        with patch("interviews.avatar.socket.connect", return_value=upstream) as connect:
            communicator = ApplicationCommunicator(avatar_socket, self.scope())
            await communicator.send_input({"type": "websocket.connect"})
            self.assertEqual((await communicator.receive_output())["type"], "websocket.accept")
            self.assertEqual((await communicator.receive_output())["text"], '{"type":"config"}')
            await communicator.send_input({"type": "websocket.receive", "text": '{"type":"offer"}'})
            self.assertEqual(await asyncio.wait_for(upstream.outgoing.get(), 1), '{"type":"offer"}')
            await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
            await communicator.wait()
            self.assertTrue(upstream.closed)
            self.assertTrue(upstream.reader_cancelled)
            self.assertTrue(await communicator.receive_nothing())
            connect.assert_called_once()
            self.assertEqual(connect.call_args.args[0], "ws://127.0.0.1:8889")
            self.assertEqual(connect.call_args.kwargs["max_queue"], 8)
            self.assertEqual(connect.call_args.kwargs["origin"], "http://localhost")

    @override_settings(INTERVIEW_REQUIRE_LOGIN=True, ALLOWED_HOSTS=["47.239.50.129"])
    async def test_production_origin_is_forwarded_without_browser_credentials(self):
        """Use a cookie-bearing production scope; forward its Origin without upstream Cookies."""
        upstream = FakeUpstream()
        scope = self.scope(
            scheme="wss",
            headers=[
                (b"host", b"47.239.50.129"),
                (b"origin", b"https://47.239.50.129"),
                (b"cookie", b"sessionid=private-browser-session"),
            ],
        )
        with patch("interviews.avatar.socket.connect", return_value=upstream) as connect:
            communicator = ApplicationCommunicator(avatar_socket, scope)
            await communicator.send_input({"type": "websocket.connect"})
            await communicator.receive_output()
            await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
            await communicator.wait()
            self.assertEqual(connect.call_args.kwargs["origin"], "https://47.239.50.129")
            self.assertNotIn("additional_headers", connect.call_args.kwargs)
            self.assertTrue(upstream.closed)

    async def test_server_close_releases_browser_receiver(self):
        """Queue a normal close and assert the browser receiver and context are released."""
        upstream = FakeUpstream()
        await upstream.incoming.put(ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True))
        with patch("interviews.avatar.socket.connect", return_value=upstream):
            communicator = ApplicationCommunicator(avatar_socket, self.scope())
            await communicator.send_input({"type": "websocket.connect"})
            self.assertEqual((await communicator.receive_output())["type"], "websocket.accept")
            self.assertEqual(
                await communicator.receive_output(), {"type": "websocket.close", "code": 1000}
            )
            await communicator.wait()
            self.assertTrue(upstream.closed)

    async def test_invalid_frames_close_and_release_upstream(self):
        """Verify fixed close codes and cleanup for binary and oversized UTF-8 fixtures."""
        for frame, code in (
            ({"bytes": b"binary"}, 1003),
            ({"text": "a" * (MAX_MESSAGE_BYTES + 1)}, 1009),
            ({"text": "é" * (MAX_MESSAGE_BYTES // 2 + 1)}, 1009),
        ):
            with self.subTest(code=code):
                upstream = FakeUpstream()
                with patch("interviews.avatar.socket.connect", return_value=upstream):
                    communicator = ApplicationCommunicator(avatar_socket, self.scope())
                    await communicator.send_input({"type": "websocket.connect"})
                    await communicator.receive_output()
                    await communicator.send_input({"type": "websocket.receive", **frame})
                    self.assertEqual(
                        await communicator.receive_output(),
                        {"type": "websocket.close", "code": code},
                    )
                    await communicator.wait()
                    self.assertTrue(upstream.closed)
                    self.assertTrue(upstream.reader_cancelled)

    async def test_backpressure_timeout_closes_and_cancels_both_pumps(self):
        """Use a short deadline with a blocked fake send and verify both pumps are released."""
        upstream = FakeUpstream()

        async def slow_send(message):
            """Keep this fake send pending until the proxy's send deadline cancels its wait."""
            await asyncio.Future()

        upstream.send = AsyncMock(side_effect=slow_send)
        with (
            patch("interviews.avatar.socket.connect", return_value=upstream),
            patch("interviews.avatar.socket.SEND_TIMEOUT_SECONDS", 0.01),
        ):
            communicator = ApplicationCommunicator(avatar_socket, self.scope())
            await communicator.send_input({"type": "websocket.connect"})
            await communicator.receive_output()
            await communicator.send_input({"type": "websocket.receive", "text": "{}"})
            self.assertEqual(
                await communicator.receive_output(), {"type": "websocket.close", "code": 1011}
            )
            await communicator.wait()
            self.assertTrue(upstream.closed)
            self.assertTrue(upstream.reader_cancelled)

    async def test_application_cancellation_releases_upstream(self):
        """Cancel an admitted ASGI task with a fake upstream and verify its context/pump cleanup."""
        upstream = FakeUpstream()
        with patch("interviews.avatar.socket.connect", return_value=upstream):
            communicator = ApplicationCommunicator(avatar_socket, self.scope())
            await communicator.send_input({"type": "websocket.connect"})
            await communicator.receive_output()
            await asyncio.sleep(0)
            communicator.stop()
            with self.assertRaises(asyncio.CancelledError):
                await communicator.future
            self.assertTrue(upstream.closed)
            self.assertTrue(upstream.reader_cancelled)

    async def test_unavailable_upstream_does_not_accept(self):
        """Verify a mocked connection error closes without accepting the browser."""
        with patch(
            "interviews.avatar.socket.connect", side_effect=OSError("private upstream detail")
        ):
            communicator = ApplicationCommunicator(avatar_socket, self.scope())
            await communicator.send_input({"type": "websocket.connect"})
            self.assertEqual(
                await communicator.receive_output(), {"type": "websocket.close", "code": 1011}
            )
            await communicator.wait()

    async def test_real_loopback_transport_through_asgi_route(self):
        """Exercise the installed WebSocket client and route with a temporary local server."""
        upstream_closed = asyncio.Event()

        async def signalling(connection):
            """Send config, echo one offer and signal closure of the loopback connection."""
            try:
                await connection.send('{"type":"config"}')
                await connection.send(await connection.recv())
                await connection.wait_closed()
            finally:
                upstream_closed.set()

        user = SimpleNamespace(is_authenticated=True, pk=7)
        async with serve(signalling, "127.0.0.1", 0, compression=None) as server:
            port = server.sockets[0].getsockname()[1]
            with (
                override_settings(AVATAR_SIGNALLING_UPSTREAM=f"ws://127.0.0.1:{port}"),
                patch("interviews.session_socket.session_user", new=AsyncMock(return_value=user)),
            ):
                communicator = ApplicationCommunicator(application, self.scope())
                await communicator.send_input({"type": "websocket.connect"})
                self.assertEqual((await communicator.receive_output())["type"], "websocket.accept")
                self.assertEqual((await communicator.receive_output())["text"], '{"type":"config"}')
                await communicator.send_input(
                    {"type": "websocket.receive", "text": '{"type":"offer"}'}
                )
                self.assertEqual((await communicator.receive_output())["text"], '{"type":"offer"}')
                await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
                await communicator.wait()
                await asyncio.wait_for(upstream_closed.wait(), 1)
