"""Responsibilities: Enable PostgreSQL and HTTPS reverse-proxy deployment settings.
Implementation: Inherit development application setup and fail startup when required deployment
values are absent.
Related Modules: deploy/systemd and Nginx provide HTTPS; Django sessions authenticate HTTP and
WebSocket requests.
Declaration Index:
None
Variable Index:
- ALLOWED_HOSTS: Explicit comma-separated hostname or IP allowlist; wildcards and URLs are rejected.
- DATABASES: PostgreSQL connection; persistent connections are disabled across ASGI requests.
- SECURE_PROXY_SSL_HEADER: Trust only the protocol header rewritten by the same-host Nginx proxy.
- SECURE_SSL_REDIRECT: Redirect HTTP to HTTPS.
- SESSION_COOKIE_SECURE: Send account session cookies over HTTPS only.
- CSRF_COOKIE_SECURE: Send CSRF cookies over HTTPS only.
- SECURE_HSTS_SECONDS: Advertise one day of HSTS without subdomains or preload.
- SECURE_CONTENT_TYPE_NOSNIFF: Prevent browser response-type sniffing.
- MIDDLEWARE: Extend inherited session, login, and CSRF checks with clickjacking protection.
- X_FRAME_OPTIONS: Prevent pages from being embedded by external sites.
- INTERVIEW_REQUIRE_LOGIN: Require authentication for production APIs, interviews, and diagnostic
  WebSockets.
- PDF_TASK_EXECUTION: Select Celery for production PDF work.
- CELERY_BROKER_URL: Required private Redis queue connection.
- PDF_TASK_REDIS_URL: Required Redis connection for task content and progress.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403
from .settings import MIDDLEWARE as BASE_MIDDLEWARE

ALLOWED_HOSTS = [host.strip() for host in os.environ["DJANGO_ALLOWED_HOSTS"].split(",")]
if any(
    not host or any(char in host for char in "*/ \t\r\n") or "://" in host for host in ALLOWED_HOSTS
):
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS requires explicit hostnames or IP addresses")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ["POSTGRES_PORT"],
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"connect_timeout": 10},
    }
}
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 86400
SECURE_CONTENT_TYPE_NOSNIFF = True
MIDDLEWARE = [
    *BASE_MIDDLEWARE,
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
X_FRAME_OPTIONS = "DENY"
INTERVIEW_REQUIRE_LOGIN = True

PDF_TASK_EXECUTION = "celery"
CELERY_BROKER_URL = os.environ["CELERY_BROKER_URL"]
PDF_TASK_REDIS_URL = os.environ["PDF_TASK_REDIS_URL"]
