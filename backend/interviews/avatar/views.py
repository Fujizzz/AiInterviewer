"""Responsibilities: Expose browser avatar transport settings through the existing account gate.
Implementation: Accept GET only and return a non-cacheable JSON configuration for the request.
Related Modules: avatar/configuration.py selects the public endpoint; config/urls.py routes the
view; interviews/middleware.py supplies the existing HTTP access and session checks.

Declaration Index:
- config: Return browser connection settings without server-side transport credentials.

Variable Index:
None
"""

from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .configuration import browser_config


@never_cache
@require_GET
def config(request):
    """Return the browser endpoint only; never return upstream addresses or credentials."""
    return JsonResponse(browser_config(request))
