"""Responsibilities: Run the ASGI WebSocket lifecycle and dispatch echo protocol operations.
Implementation: Validate the handshake, send bounded control responses, and echo each binary message
after its ACK.
Related Modules: streaming.protocol owns deterministic state validation; access.websocket_allowed
enforces peer and origin policy.

Declaration Index:
- send_json: Encode a response dictionary as compact JSON and send it over ASGI.
- dispatch_control: Map a validated control message to a protocol response.
- echo_socket: Run one connection's receive/echo loop with explicit error and close behavior.

Variable Index:
- logger: Module-level logger for connection lifecycle and protocol failures.
"""

import asyncio
import json
import logging
import time

from ..access import websocket_allowed
from .protocol import IDLE_TIMEOUT_SECONDS, EchoState, ProtocolError, parse_control

logger = logging.getLogger(__name__)


async def send_json(send, data):
    """Encode a response dictionary as compact JSON and send it through ASGI without caching the
    body.
    """
    await send({"type": "websocket.send", "text": json.dumps(data, separators=(",", ":"))})


def dispatch_control(state, message):
    """Map a JSON-validated control dictionary and current connection state to a response
    dictionary.

    Validate ping IDs and delegate start/finish to EchoState. Unknown message types raise
    ProtocolError.
    The caller decides whether to close the connection based on the returned response type.
    """
    kind = message.get("type")
    if kind == "ping":
        ident = message.get("id")
        if not isinstance(ident, str) or not 1 <= len(ident) <= 64:
            raise ProtocolError("invalid_ping", "ping.id must be a string of 1-64 characters.")
        return {"type": "pong", "id": ident, "server_time_ms": time.time_ns() // 1000000}
    if kind == "start":
        response = state.start(message)
        logger.info("Stream started connection=%s mode=%s", state.connection_id, state.mode)
        return response
    if kind == "finish":
        return state.finish(message)
    raise ProtocolError("unknown_type", "Supported control types: ping, start, finish.")


async def echo_socket(scope, receive, send):
    """Run one connection's ASGI receive/send loop, emitting each ACK before its original binary
    message.

    Inputs are ASGI scope and async receive/send callbacks. Validate the handshake, announce limits,
    process control or
    binary messages, then complete or close explicitly. Protocol errors produce an error response
    and matching close code;
    unexpected failures log context and close with 1011. Side effects are network sends and console
    logs only; logs exclude media bodies.
    """
    event = await receive()
    if event["type"] != "websocket.connect":
        return
    if not websocket_allowed(scope):
        logger.warning("Stream handshake rejected: non-local peer or foreign origin")
        await send({"type": "websocket.close", "code": 1008})
        return

    state = EchoState()
    try:
        await send({"type": "websocket.accept"})
        logger.info("Stream opened connection=%s", state.connection_id)
        await send_json(send, state.hello())
        while True:
            try:
                event = await asyncio.wait_for(receive(), timeout=IDLE_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise ProtocolError(
                    "idle_timeout",
                    "No messages received for 30 seconds.",
                ) from exc
            if event["type"] == "websocket.disconnect":
                break
            if event["type"] != "websocket.receive":
                continue
            binary = event.get("bytes")
            if binary is not None:
                await send_json(send, state.accept_chunk(binary))
                await send({"type": "websocket.send", "bytes": binary})
                continue
            response = dispatch_control(state, parse_control(event.get("text", "")))
            await send_json(send, response)
            if response["type"] == "finished":
                await send({"type": "websocket.close", "code": 1000})
                break
    except ProtocolError as exc:
        state.status, state.error_code = "error", exc.code
        logger.warning(
            "Stream protocol rejected connection=%s code=%s chunks=%d bytes=%d",
            state.connection_id,
            exc.code,
            state.chunk_count,
            state.byte_count,
        )
        await send_json(send, {"type": "error", "code": exc.code, "detail": exc.detail})
        await send({"type": "websocket.close", "code": exc.close_code})
    except (OSError, asyncio.CancelledError):
        logger.info("Stream transport closed connection=%s", state.connection_id)
        raise
    except Exception:
        state.status, state.error_code = "error", "internal_error"
        logger.exception("Stream failure connection=%s; inspect ASGI server", state.connection_id)
        await send({"type": "websocket.close", "code": 1011})
    finally:
        logger.info(
            "Stream closed connection=%s status=%s chunks=%d bytes=%d",
            state.connection_id,
            state.status,
            state.chunk_count,
            state.byte_count,
        )
