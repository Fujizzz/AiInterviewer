"""Responsibilities: enforces service capacity and upload transmission byte limits before resume
text reading and Agent handshake.

Implementation: ASGI wrapper acquires cross-process lease, releases after completion or
cancellation; synchronous models may delay returning connection slot.
Related Modules:
- config.asgi wraps original route;
- agent_socket leases private scope to backend model adapter.

Declaration Index:
- reject_http: sends fixed JSON error with no-cache header, no request echo.
- limited_application: limits PDF/resume version upload & parsing POST and Agent WebSocket.
- limited_application.bounded_receive: accumulates ASGI chunks, stops delivering body upon exceeding
  transmission limit.
- limited_application.observed_send: records HTTP response start, prevents sending second set of
  headers.
- UploadTooLarge: interrupts downstream upload parsing when cumulative body exceeds limit.

Variable Index:
- logger: logs rejection category, without file name, body, Origin, or key.

Configuration Notes:
SERVICE_AGENT_CONNECTIONS defaults to 4, SERVICE_PDF_UPLOADS defaults to 2; both are new service
safety caps.
PDF_BODY_BYTES sets transmission limit at 10 MiB file plus 64 KiB multipart packaging; does not
modify file size cap itself.
Full capacity does not queue, trigger model, or execute implicit rollback. Connection cap includes
idle interviews and canceled ongoing synchronous calls.
"""

import json
import logging
import os

from .capacity import CapacityExceeded, take_slot
from .resume_pdf import MAX_BYTES

logger = logging.getLogger(__name__)


class UploadTooLarge(Exception):
    """Interrupts downstream upload parsing when cumulative body exceeds limit; carries only fixed
    error, no body.
    """


async def reject_http(send, status, code):
    """Sends fixed JSON error with no-cache header, no request echo; returns None, no retry send.
    """
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": json.dumps({"error": code}).encode()})


async def limited_application(app, scope, receive, send):
    """Limits existing PDF POST, version upload/parsing POST, and Agent WebSocket; other interfaces
    run via original routing.

    Inputs are downstream ASGI application and standard event function. Lease acquired before body
    reading to prevent Django from caching entire upload.
    Content length used for early rejection; actual delivery still accumulates chunks; configuration
    and lock file errors return 503/1013 explicitly, no degradation.
    """
    is_pdf = (
        scope["type"] == "http"
        and (
            scope.get("path") in {"/api/resume/parse/", "/api/resume-versions/"}
            or (
                scope.get("path", "").startswith("/api/resume-versions/")
                and scope.get("path", "").endswith("/parse/")
            )
        )
        and scope.get("method") == "POST"
    )
    is_agent = scope["type"] == "websocket" and scope.get("path") == "/ws/agent/"
    if not (is_pdf or is_agent):
        return await app(scope, receive, send)
    body_limit = MAX_BYTES + 65536
    resource = "pdf" if is_pdf else "agent"
    option = "SERVICE_PDF_UPLOADS" if is_pdf else "SERVICE_AGENT_CONNECTIONS"
    try:
        lease = take_slot(resource, int(os.getenv(option, "2" if is_pdf else "4")))
    except (CapacityExceeded, OSError, ValueError):
        logger.warning("Resource admission rejected resource=%s", resource)
        if is_pdf:
            await reject_http(send, 503, "service_capacity_unavailable")
        elif (await receive())["type"] == "websocket.connect":
            await send({"type": "websocket.close", "code": 1013})
        return
    received = 0
    response_started = False

    async def bounded_receive():
        """Accumulates uploaded ASGI chunks, stops delivering body when transmission limit exceeded;
        disconnect events passed through unchanged.
        """
        nonlocal received
        event = await receive()
        if event["type"] == "http.request":
            received += len(event.get("body", b""))
            if received > body_limit:
                raise UploadTooLarge("PDF upload body exceeded its limit")
        return event

    async def observed_send(event):
        """Records HTTP response start, avoids sending second set of headers; other events and
        exceptions passed through unchanged.
        """
        nonlocal response_started
        if event["type"] == "http.response.start":
            response_started = True
        await send(event)

    try:
        if is_pdf:
            lengths = [v for k, v in scope.get("headers", []) if k.lower() == b"content-length"]
            if lengths and (len(lengths) != 1 or not lengths[0].isdigit()):
                return await reject_http(send, 400, "invalid_content_length")
            if lengths:
                # Compare decimal byte strings to avoid long numbers triggering Python integer
                # conversion exceptions.
                length = lengths[0].lstrip(b"0") or b"0"
                maximum = str(body_limit).encode()
                if len(length) > len(maximum) or (len(length) == len(maximum) and length > maximum):
                    return await reject_http(send, 413, "pdf_upload_too_large")
        await app(
            {**scope, "interview.capacity_lease": lease},
            bounded_receive if is_pdf else receive,
            observed_send,
        )
    except UploadTooLarge:
        if response_started:
            raise
        await reject_http(send, 413, "pdf_upload_too_large")
    finally:
        lease.release()
