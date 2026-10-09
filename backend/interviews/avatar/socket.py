"""Responsibilities: Proxy bounded signalling after session and same-origin admission.
Implementation: Open one configured loopback upstream, forward text with bounded queues and send
deadlines, then cancel both receive pumps and release the upstream on closure or failure.
Related Modules: avatar/configuration.py validates the upstream; interviews/access.py checks
Host/origin admission; interviews/session_socket.py authenticates the ASGI route.

Declaration Index:
- InvalidFrame: Carry a fixed protocol close code without retaining signalling payloads.
- InvalidFrame.__init__: Store the close code for a rejected frame.
- bounded_text: Reject binary or oversized UTF-8 signalling messages.
- avatar_socket: Admit and bridge one browser connection with bounded transport lifetime.
- avatar_socket.from_browser: Forward browser frames serially and observe browser disconnects.
- avatar_socket.from_signalling: Forward upstream frames serially and map server close codes.

Variable Index:
- logger: Report exception types and admission state without signalling or credential values.
- MAX_MESSAGE_BYTES: Maximum UTF-8 signalling frame size in bytes, shared with the upstream client.
- SEND_TIMEOUT_SECONDS: Maximum wait in seconds for each browser or upstream frame send.

Constraints:
Only SDP/ICE signalling passes through the proxy. UE audio/video and UI interactions remain on
WebRTC; the upstream address is server configuration, never client input.
"""

import asyncio
import contextlib
import logging

from django.conf import settings
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from interviews.access import websocket_allowed

from .configuration import signalling_upstream

logger = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 128 * 1024
SEND_TIMEOUT_SECONDS = 15


class InvalidFrame(Exception):
    """Carry a fixed close code without retaining a browser message."""

    def __init__(self, code):
        """Store the protocol close code without keeping the rejected frame or transport data."""
        self.code = code


def bounded_text(value):
    """Accept signalling text only and bound its actual UTF-8 payload size."""
    if not isinstance(value, str):
        raise InvalidFrame(1003)
    if len(value) > MAX_MESSAGE_BYTES or len(value.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise InvalidFrame(1009)
    return value


async def avatar_socket(scope, receive, send):
    """Bridge one admitted ASGI player using its scope, receive and send callables.
    Reject disabled, invalid-origin or unauthenticated production requests before connecting.
    Forward signalling only; bound sends and close both pumps/upstream on disconnect, cancellation
    or error. Protocol failures send fixed close codes; logs contain exception types, not payloads.
    """
    if (await receive())["type"] != "websocket.connect":
        return
    user = scope.get("user")
    origins = [value for key, value in scope.get("headers", []) if key.lower() == b"origin"]
    origin = origins[0].decode("latin1") if len(origins) == 1 else None
    try:
        allowed = (
            len(origins) <= 1
            and not (origin and any(character.isspace() for character in origin))
            and websocket_allowed(scope)
        )
    except (KeyError, UnicodeError, ValueError):
        allowed = False
    if (
        not settings.AVATAR_REMOTE_ENABLED
        or not allowed
        or (
            settings.INTERVIEW_REQUIRE_LOGIN
            and (not origin or not getattr(user, "is_authenticated", False))
        )
    ):
        await send({"type": "websocket.close", "code": 1008})
        return
    if origin is None:
        host = next(
            value.decode("latin1") for key, value in scope["headers"] if key.lower() == b"host"
        )
        protocol = "https" if scope.get("scheme") == "wss" else "http"
        origin = f"{protocol}://{host}"

    # accepted selects failure reporting; disconnected prevents sending after browser closure.
    # tasks owns exactly the two pumps and is drained before releasing the upstream context.
    accepted = False
    disconnected = False
    tasks = []
    try:
        async with connect(
            signalling_upstream(),
            origin=origin,
            open_timeout=5,
            close_timeout=3,
            max_size=MAX_MESSAGE_BYTES,
            max_queue=8,
            write_limit=32768,
            compression=None,
            proxy=None,
            ping_interval=20,
            ping_timeout=20,
        ) as upstream:
            await send({"type": "websocket.accept"})
            accepted = True

            async def from_browser():
                """Await each upstream send so a slow peer cannot create an unbounded queue."""
                nonlocal disconnected
                while True:
                    event = await receive()
                    if event["type"] == "websocket.disconnect":
                        disconnected = True
                        return
                    if event["type"] != "websocket.receive":
                        raise InvalidFrame(1003)
                    text = bounded_text(event.get("text"))
                    await asyncio.wait_for(upstream.send(text), SEND_TIMEOUT_SECONDS)

            async def from_signalling():
                """Forward one bounded text frame at a time; propagate an ordinary server close."""
                while True:
                    try:
                        text = bounded_text(await upstream.recv())
                    except ConnectionClosed as exc:
                        return 1000 if exc.code in {1000, 1001} else 1011
                    await asyncio.wait_for(
                        send({"type": "websocket.send", "text": text}), SEND_TIMEOUT_SECONDS
                    )

            tasks = [asyncio.create_task(from_browser()), asyncio.create_task(from_signalling())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                code = 1000
                for task in done:
                    result = task.result()
                    if result is not None:
                        code = result
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            if not disconnected:
                await send({"type": "websocket.close", "code": code})
    except InvalidFrame as exc:
        if not disconnected:
            await send({"type": "websocket.close", "code": exc.code})
    except Exception as exc:
        logger.warning(
            "Avatar signalling failed exception=%s accepted=%s", type(exc).__name__, accepted
        )
        if not disconnected:
            await send({"type": "websocket.close", "code": 1011})
