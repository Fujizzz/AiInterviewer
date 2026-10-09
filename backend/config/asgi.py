"""Responsibilities: Start the Django ASGI application and dispatch HTTP and WebSocket protocols.
Implementation: Authenticate WebSocket sessions, apply resource admission, then route diagnostics,
interviews, speech recognition, and presentation signalling.
Related Modules: config.settings initializes Django; interviews.session_socket, resource_gate,
streaming, agent_socket, speech.socket, and avatar.socket handle protocol stages.
Declaration Index:
- handle_lifespan: Acknowledge ASGI startup and shutdown events; no persistent resources are held.
- application: Apply WebSocket session authentication before service admission.
- capacity_application: Apply resource admission after identity checks without changing limits.
- route_application: Dispatch HTTP, WebSocket, and lifespan scopes to their existing handlers.
Variable Index:
- django_application: Initialized Django ASGI HTTP application used by the protocol dispatcher.
"""

import os

from django.core.asgi import get_asgi_application
from interviews.streaming.websocket import echo_socket

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_application = get_asgi_application()


async def handle_lifespan(receive, send):
    """Functionality: Acknowledge ASGI startup and shutdown events.
    Inputs: ASGI receive and send callables.
    Outputs: Completes after sending the shutdown acknowledgement.
    Logic: Read lifespan events and send the corresponding completion event.
    Constraints: Holds no persistent resources and follows the ASGI event contract.
    """
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def application(scope, receive, send):
    """Functionality: Authenticate WebSocket sessions before resource admission.
    Inputs: Standard ASGI scope, receive, and send values.
    Outputs: Completes the delegated ASGI request.
    Logic: Delegate through the session socket gateway.
    Constraints: HTTP identity remains checked by Django middleware.
    """
    from interviews.session_socket import authenticated_socket

    await authenticated_socket(capacity_application, scope, receive, send)


async def capacity_application(scope, receive, send):
    """Functionality: Apply service resource admission after identity checks.
    Inputs: Standard ASGI scope, receive, and send values.
    Outputs: Completes the delegated ASGI request.
    Logic: Import the resource gate after settings initialize repository paths and environment.
    Constraints: Preserve existing limits and failure behavior; lazy import supports backend-local
    startup.
    """
    # Import the resource layer after settings initialize repository paths and environment.
    from interviews.resource_gate import limited_application

    await limited_application(route_application, scope, receive, send)


async def route_application(scope, receive, send):
    """Functionality: Dispatch an ASGI scope to its protocol handler.
    Inputs: Standard ASGI scope, receive, and send values.
    Outputs: Completes when the selected handler finishes.
    Logic: Route HTTP to Django, supported WebSocket paths to their handlers, and lifespan events to
    handle_lifespan.
    Constraints: Unknown WebSocket paths are explicitly closed; downstream layers retain their error
    semantics.
    """
    if scope["type"] == "http":
        await django_application(scope, receive, send)
    elif scope["type"] == "websocket":
        if scope["path"] == "/ws/echo/":
            await echo_socket(scope, receive, send)
        elif scope["path"] == "/ws/agent/":
            from interviews.agent_socket import agent_socket

            await agent_socket(scope, receive, send)
        elif scope["path"] == "/ws/speech/stt/":
            from interviews.speech.socket import stt_socket

            await stt_socket(scope, receive, send)
        elif scope["path"] == "/ws/avatar/":
            from interviews.avatar.socket import avatar_socket

            await avatar_socket(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})
    elif scope["type"] == "lifespan":
        await handle_lifespan(receive, send)
