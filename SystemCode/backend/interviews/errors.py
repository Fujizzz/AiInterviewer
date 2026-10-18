"""Responsibilities: Normalize expected REST failures into stable JSON and log unexpected server
errors.
Implementation: Map database outages to 503, framework API exceptions to their existing status, and
unknown failures to 500.
Related Modules: Django REST Framework calls api_exception_handler through its configured
exception-handler setting.

Declaration Index:
- Conflict: Represent a state conflict with HTTP 409 and the stable conflict error code.
- api_exception_handler: Normalize DRF exception response shape and log server failures with request
  context.

Variable Index:
- logger: Module-level console logger used by the exception handler.
"""

import logging

from django.db import OperationalError
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class Conflict(APIException):
    """Represent a state conflict with HTTP 409 and the fixed conflict error code.
    """

    status_code = 409
    default_code = "conflict"
    default_detail = "The resource state does not allow this operation."


def api_exception_handler(exc, context):
    """Wrap DRF errors in the stable API envelope and return a Response.

    Inputs are the caught exception and DRF context. Database OperationalError maps to 503,
    recognized framework errors
    retain their status, and other unhandled errors map to 500. Expected rejections log
    path/status/type; unexpected errors
    use logger.exception with traceback. The handler does not serialize request bodies or redact
    exception text in logs,
    so callers must not put secrets in exception messages. It does not retry, and 500/503 bodies use
    fixed details.
    """
    request = context.get("request")
    path = request.path if request else "unknown"
    if isinstance(exc, OperationalError):
        logger.exception(
            "Database operation failed path=%s; "
            "inspect database path, migrations and concurrent writers",
            path,
        )
        return Response(
            {
                "error": {
                    "code": "database_unavailable",
                    "detail": (
                        "Database unavailable. Inspect server logs; "
                        "no automatic retry was performed."
                    ),
                }
            },
            status=503,
        )
    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API failure path=%s", path)
        return Response(
            {"error": {"code": "internal_error", "detail": "Request failed; inspect server logs."}},
            status=500,
        )
    logger.warning(
        "API rejected path=%s status=%s exception=%s",
        path,
        response.status_code,
        type(exc).__name__,
    )
    response.data = {
        "error": {"code": getattr(exc, "default_code", "request_error"), "detail": response.data}
    }
    return response
