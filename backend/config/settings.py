"""Responsibilities: Configure Django applications, SQLite, JSON APIs, and console logging.
Implementation: Load the repository environment without overriding process variables; require the
Django secret from the environment.
Related Modules: config.urls, config.asgi, and config.production consume these settings; interviews
provides application services.
Declaration Index:
None
Variable Index:
- BASE_DIR: Absolute backend directory used to resolve application resources and the default
  database path.
- RECOMMENDATION_JOB_CATALOG: Optional explicit job JSON path; an empty value means the personal
  recommendation source is unconfigured.
- SECRET_KEY: Required Django secret loaded from the process environment after reading the
  repository .env file.
- DEBUG: Fixed Django debug-mode setting.
- ALLOWED_HOSTS: Local HTTP Host allowlist used with request middleware.
- INSTALLED_APPS: Django auth, content-type, session, REST framework, and interviews app
  registration.
- INTERVIEW_REQUIRE_LOGIN: Development login gate; production configuration enables this explicitly.
- AI_SECURITY_ENABLED: Strict true/false environment switch; true retains interview safety review,
  false explicitly disables review for algorithm development without disabling authentication.
- AVATAR_REMOTE_ENABLED: Opt-in same-origin signalling proxy for a remote rendering computer.
- AVATAR_SIGNALLING_UPSTREAM: Private loopback player connection; never returned to the browser.
- AUTH_PASSWORD_VALIDATORS: Empty registration complexity rules; Django's standard password hashing
  remains active.
- TEMPLATES: Template directories and request/identity context processors.
- MIDDLEWARE: Ordered security, source, session, authentication, login-gate, CSRF, and common HTTP
  middleware.
- ROOT_URLCONF: Django root routing module.
- ASGI_APPLICATION: ASGI protocol entry point.
- DATABASES: SQLite connection and timeout configuration, with an optional environment-selected
  path.
- DEFAULT_AUTO_FIELD: Default Django primary-key field.
- USE_TZ: Enables timezone-aware datetime handling.
- TIME_ZONE: Server time basis, retained as UTC.
- LANGUAGE_CODE: Default Django locale, set to English for server-generated framework text.
- REST_FRAMEWORK: JSON rendering/parsing, pagination, session authentication, and API exception
  handling.
- LOGGING: Console formats and levels for business, completion detection, and security decisions;
  SDK content and secrets are excluded.
- PDF_TASK_EXECUTION: Development defaults to inline; explicit Celery mode uses Redis without
  fallback.
- CELERY_BROKER_URL: Private Redis queue address for Celery.
- PDF_TASK_REDIS_URL: Private Redis address for temporary PDF text, progress, and cancellation
  state.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# Allow backend-local startup while reusing repository Agent, App, and Shared modules.
sys.path.insert(0, str(BASE_DIR.parent))
load_dotenv(BASE_DIR.parent / ".env", override=False)
RECOMMENDATION_JOB_CATALOG = os.environ.get("RECOMMENDATION_JOB_CATALOG", "")
# Stop startup when the environment lacks a secret instead of using an implicit default.
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "interviews",
]
INTERVIEW_REQUIRE_LOGIN = False
# An absent switch preserves safety; malformed values cannot accidentally disable it. This setting
# is fixed for the process lifetime and requires a backend restart after environment changes.
AI_SECURITY_ENABLED = os.environ.get("AI_SECURITY_ENABLED", "true").strip().lower()
if AI_SECURITY_ENABLED not in {"true", "false"}:
    raise ValueError("AI_SECURITY_ENABLED must be true or false")
AI_SECURITY_ENABLED = AI_SECURITY_ENABLED == "true"
AVATAR_REMOTE_ENABLED = os.environ.get("AVATAR_REMOTE_ENABLED", "false").strip().lower() == "true"
AVATAR_SIGNALLING_UPSTREAM = os.environ.get("AVATAR_SIGNALLING_UPSTREAM", "ws://127.0.0.1:8889")
AUTH_PASSWORD_VALIDATORS = []
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend", BASE_DIR / "diagnostics"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ]
        },
    }
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "interviews.middleware.LocalOnlyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "interviews.accounts.AccountRequiredMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("INTERVIEW_DB_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {"timeout": 5},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "interviews.errors.api_exception_handler",
}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"context": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "context"}},
    "loggers": {
        "interviews": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "agents.answer_completion": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "agents.completion_gate": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "ai_security": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

PDF_TASK_EXECUTION = os.environ.get("PDF_TASK_EXECUTION", "inline")
if PDF_TASK_EXECUTION not in {"inline", "celery"}:
    raise ValueError("PDF_TASK_EXECUTION must be inline or celery")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
PDF_TASK_REDIS_URL = os.environ.get("PDF_TASK_REDIS_URL", "redis://127.0.0.1:6379/1")
