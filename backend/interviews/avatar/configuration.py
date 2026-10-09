"""Responsibilities: Select safe browser and server-side avatar signalling endpoints.
Implementation: Restrict the upstream to explicit loopback WS; enable browser proxy URLs only
on HTTPS or loopback HTTP requests, retaining the existing local demonstration default.
Related Modules: avatar/views.py exposes browser_config; avatar/socket.py uses signalling_upstream;
interviews/access.py supplies loopback address classification.

Declaration Index:
- signalling_upstream: Validate and return the configured loopback upstream WebSocket URL.
- browser_config: Select a same-origin proxy, local legacy endpoint or disabled configuration.

Variable Index:
None
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from interviews.access import is_loopback


def signalling_upstream():
    """Return the configured loopback WS URL with an explicit port and no credential or query.
    Read only AVATAR_SIGNALLING_UPSTREAM; reject unsafe or malformed values with
    ImproperlyConfigured before an upstream connection can be opened.
    """
    value = settings.AVATAR_SIGNALLING_UPSTREAM
    try:
        endpoint = urlsplit(value)
        valid = (
            isinstance(value, str)
            and not any(character.isspace() for character in value)
            and endpoint.scheme == "ws"
            and endpoint.hostname in {"127.0.0.1", "localhost", "::1"}
            and endpoint.port is not None
            and endpoint.port > 0
            and endpoint.username is None
            and endpoint.password is None
            and endpoint.path in {"", "/"}
            and not endpoint.query
            and not endpoint.fragment
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ImproperlyConfigured(
            "AVATAR_SIGNALLING_UPSTREAM must be a loopback ws URL with a port"
        )
    return value


def browser_config(request):
    """Return enabled/signalling_url for the validated request's host and transport.
    Explicit remote enablement selects the same-origin proxy on HTTPS or loopback HTTP.
    Disabled remote streaming retains the legacy local HTTP endpoint; other requests stay disabled.
    No server-side upstream URL or credential is included in the returned mapping.
    """
    hostname = urlsplit("http://" + request.get_host()).hostname or ""
    local = is_loopback(hostname) or hostname == "localhost"
    if settings.AVATAR_REMOTE_ENABLED and (request.is_secure() or local):
        protocol = "wss" if request.is_secure() else "ws"
        return {"enabled": True, "signalling_url": f"{protocol}://{request.get_host()}/ws/avatar/"}
    if not settings.AVATAR_REMOTE_ENABLED and not request.is_secure() and local:
        return {"enabled": True, "signalling_url": "ws://127.0.0.1:8889"}
    return {"enabled": False, "signalling_url": ""}
