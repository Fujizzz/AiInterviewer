"""Responsibilities: Apply shared loopback, proxy-host, and same-origin access checks to HTTP and
WebSocket entry points.
Implementation: Require loopback peers and read the Host allowlist from Django settings; localhost
remains the default.
Related Modules: Production settings may allow configured public Hosts; session_socket verifies
identity separately.

Declaration Index:
- is_loopback: Determine whether a textual IPv4 or IPv6 address belongs to the loopback network.
- same_origin: Compare a browser Origin with the request scheme, host, and port.
- websocket_allowed: Validate the host, peer address, and Origin from an ASGI scope.

Variable Index:
None
"""

from ipaddress import ip_address
from urllib.parse import urlsplit

from django.conf import settings
from django.http.request import validate_host


def is_loopback(address):
    """Return whether address parses as an IPv4/IPv6 loopback address; invalid and non-loopback
    values return False.
    """
    try:
        return ip_address(address).is_loopback
    except ValueError:
        return False


def same_origin(origin, host, scheme):
    """Compare Origin with the request scheme, host, and port; None permits clients that omit
    Origin.

    Preserve strict equality without rewriting ports or widening the allowed origins. This function
    performs no I/O.
    """
    if origin is None:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme == scheme and parsed.netloc == host and parsed.path in ("", "/")


def websocket_allowed(scope):
    """Validate WebSocket Host, peer address, and Origin from ASGI headers/client/scheme and Django
    settings.

    Return True only when the Host allowlist, loopback-peer, and same-origin checks pass. Production
    proxies must replace
    forwarded addresses and trusted scheme headers. Missing Host is rejected; authentication is
    handled upstream by session_socket.
    """
    headers = {
        key.decode("latin1").lower(): value.decode("latin1") for key, value in scope["headers"]
    }
    host = headers.get("host", "")
    hostname = urlsplit("http://" + host).hostname
    return (
        bool(hostname)
        and validate_host("[::1]" if hostname == "::1" else hostname, settings.ALLOWED_HOSTS)
        and is_loopback((scope.get("client") or ("", 0))[0])
        and same_origin(
            headers.get("origin"),
            host,
            "https" if scope.get("scheme") == "wss" else "http",
        )
    )
