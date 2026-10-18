"""Responsibilities: Expose autonomous facial behavior planning before and during an interview.
Implementation: Bound raw request size before parsing, validate the independent strict contract,
scope cache entries to the current session owner, and retain existing session/CSRF access checks.
Related Modules: presentation.schemas validates input; presentation.service isolates provider work.

Declaration Index:
- plan: Return a bounded visual plan without altering question, answer or assessment processing.

Variable Index:
- MAX_REQUEST_BYTES: Maximum raw JSON size accepted before expression planning.
"""

from pydantic import ValidationError
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .schemas import PresentationRequest
from .service import build_plan

MAX_REQUEST_BYTES = 8192


@api_view(["POST"])
def plan(request):
    """Validate nullable approved-question JSON and return a private four-state behavior plan.

    Existing account middleware gates production requests and DRF SessionAuthentication enforces
    CSRF for authenticated writes. Default loopback development follows the same anonymous access
    policy as speech. Validation errors contain fixed messages, never question or provider content.
    """
    if len(request.body) > MAX_REQUEST_BYTES:
        return Response(
            {
                "error": {
                    "code": "presentation_request_too_large",
                    "detail": "Presentation JSON exceeds 8 KiB.",
                }
            },
            status=413,
        )
    try:
        payload = PresentationRequest.model_validate(request.data)
    except ValidationError:
        return Response(
            {
                "error": {
                    "code": "invalid_presentation_request",
                    "detail": (
                        "Provide version 2, presentation identity, generation "
                        "and nullable approved question."
                    ),
                }
            },
            status=400,
        )
    user = request.user
    owner_key = str(user.pk) if user is not None and user.is_authenticated else "anonymous"
    response = Response(build_plan(payload, owner_key))
    response["Cache-Control"] = "no-store"
    return response
