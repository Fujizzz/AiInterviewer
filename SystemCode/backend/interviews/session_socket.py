"""Responsibilities: Apply Django database-session identity to WebSocket connections.
Implementation: Read and validate the session cookie before capacity admission, then recheck
authentication before each message.
Related Modules: config.asgi installs this wrapper; agent_socket reads scope.user when persisting
ownership.

Declaration Index:
- session_user: Resolve the Django user from a WebSocket scope in a synchronous database thread.
- authenticated_socket: Reject production anonymous connections and pass the authenticated user
  downstream.
- authenticated_socket.checked_receive: Revalidate the session before delivering each message and
  close expired connections.

Variable Index:
- logger: Records rejection reasons and user IDs without cookies or authentication secrets.

Constraints:
No cookie means no database read. Database errors fail explicitly without anonymous fallback or
retry.
Synchronous model requests already sent retain their existing cancellation boundary; disconnect
prevents further messages.
"""

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.db import close_old_connections
from django.http import HttpRequest, parse_cookie

logger = logging.getLogger(__name__)


@sync_to_async
def session_user(scope):
    """Resolve and return the user from ASGI scope using Django session rules, never a
    client-declared user ID.

    Perform synchronous database work in a dedicated thread and close stale connections before and
    after it.
    Invalid or expired sessions return an anonymous user. Propagate database errors and never log
    cookie contents.
    """
    close_old_connections()
    try:
        cookies = b"; ".join(
            value for key, value in scope.get("headers", []) if key.lower() == b"cookie"
        )
        request = HttpRequest()
        request.session = SessionStore(
            session_key=parse_cookie(cookies.decode("latin1")).get(settings.SESSION_COOKIE_NAME)
        )
        return get_user(request)
    finally:
        close_old_connections()


async def authenticated_socket(app, scope, receive, send):
    """Wrap an ASGI application; delegate non-WebSocket scopes unchanged and authenticate WebSockets
    before admission.

    Reject anonymous production handshakes with 1008; local development may use unowned data without
    inventing a user.
    Log the exception class and reject storage failures with 1011, never returning exception text
    that may contain credentials.
    """
    if scope["type"] != "websocket":
        return await app(scope, receive, send)
    try:
        user = await session_user(scope)
    except Exception as exc:
        logger.error("WebSocket session lookup failed exception=%s", type(exc).__name__)
        if (await receive())["type"] == "websocket.connect":
            await send({"type": "websocket.close", "code": 1011})
        return
    if settings.INTERVIEW_REQUIRE_LOGIN and not user.is_authenticated:
        logger.info("Anonymous WebSocket handshake rejected")
        if (await receive())["type"] == "websocket.connect":
            await send({"type": "websocket.close", "code": 1008})
        return

    async def checked_receive():
        """Read the next event and reauthenticate logged-in users before delivery; expired sessions
        return disconnect for cleanup.
        """
        event = await receive()
        if event["type"] == "websocket.receive" and user.is_authenticated:
            try:
                current = await session_user(scope)
            except Exception as exc:
                logger.error("WebSocket session recheck failed exception=%s", type(exc).__name__)
                await send({"type": "websocket.close", "code": 1011})
                return {"type": "websocket.disconnect", "code": 1011}
            if not current.is_authenticated or current.pk != user.pk:
                logger.info("WebSocket session ended user_id=%s", user.pk)
                await send({"type": "websocket.close", "code": 1008})
                return {"type": "websocket.disconnect", "code": 1008}
        return event

    await app({**scope, "user": user}, checked_receive, send)
