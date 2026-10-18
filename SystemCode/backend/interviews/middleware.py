"""Responsibilities: Reject non-local or cross-origin HTTP requests before business processing.
Implementation: Reuse the shared access policy and delegate accepted requests unchanged.
Related Modules: interviews.access implements loopback and Origin checks.

Declaration Index:
- LocalOnlyMiddleware: Apply local-origin validation to each Django HTTP request.
- LocalOnlyMiddleware.__init__: Save the downstream Django handler without network or database work.
- LocalOnlyMiddleware.__call__: Validate request address and Origin before dispatch.

Variable Index:
None
"""

from django.http import JsonResponse

from .access import is_loopback, same_origin


class LocalOnlyMiddleware:
    """Delegate requests only after local-origin checks pass, preserving the downstream response.
    """

    def __init__(self, get_response):
        """Store the downstream Django handler without performing network or database operations.
        """
        self.get_response = get_response

    def __call__(self, request):
        """Validate the remote address and Origin before business handling.

        Return a 403 JSON response with code local_only on rejection; otherwise delegate the request
        unchanged.
        """
        if not is_loopback(request.META.get("REMOTE_ADDR", "")) or not same_origin(
            request.headers.get("Origin"), request.get_host(), request.scheme
        ):
            return JsonResponse(
                {
                    "error": {
                        "code": "local_only",
                        "detail": "Use this service from the same local origin.",
                    }
                },
                status=403,
            )
        return self.get_response(request)
