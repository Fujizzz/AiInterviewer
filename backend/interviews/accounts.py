"""Responsibilities: Provide registration, session login, logout, and HTTP login gatekeeping.

Implementation: Reuses Django user, password hashing, and database session; forms undergo CSRF
validation, redirects allowed only to same site.
Related Modules: account.html, config.urls, SessionMiddleware; production enables gatekeeping, local
development keeps loopback mode.

Declaration Index:
- safe_next: filters same-site redirect targets, rejects external sites or account page loops.
- account_page: handles registration/login, rotates session and redirects on success, displays fixed
  error text on failure.
- sign_out: clears current database session via POST with valid CSRF token, returns to homepage;
  does not delete account or interview records.
- AccountRequiredMiddleware: protects pages and APIs based on deployment settings.
- AccountRequiredMiddleware.__init__: saves downstream handler.
- AccountRequiredMiddleware.__call__: allows public endpoints, returns 401 for anonymous API,
  redirects anonymous pages to login.

Variable Index:
- logger: logs operation type and user ID, does not record username, password, or session token.
- PUBLIC_PATHS: paths requiring no login: homepage, account page, and logout entry.
- PUBLIC_ASSETS: precise paths to shared styles and language resources needed for homepage/account
  page.

Constraints:
Username non-empty, max 150 characters; password non-empty, max 128 characters, no complexity,
email, or confirm password validation.
No retry or anonymous fallback here; database exceptions remain failed. CSRF provides request
protection without adding user input fields.
"""

import logging
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

logger = logging.getLogger(__name__)
PUBLIC_PATHS = {"/", "/login/", "/register/", "/logout/"}
PUBLIC_ASSETS = {
    "/stream-demo/style.css",
    "/stream-demo/home.css",
    "/stream-demo/workspace.css",
    "/stream-demo/account.css",
    "/stream-demo/i18n.js",
}


def safe_next(request):
    """Reads GET/POST next; returns only same-site HTTP(S) target, defaults to homepage if missing
    or invalid, no I/O.
    """
    target = request.POST.get("next", request.GET.get("next", "/"))
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ) or urlsplit(target).path in {"/login/", "/register/", "/logout/"}:
        return "/"
    return target


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def account_page(request, mode):
    """Processes requests with fixed login/register routing pattern, returns form or successful
    redirect.

    Registration atomically writes user; unique conflict only maps to form error when username
    already exists; other exceptions propagate unchanged.
    Login uses Django backend validation, does not call password complexity validator; never fills
    or logs password.
    On successful login, rotates session and CSRF token; logged-in users directly enter secure next
    destination.
    """
    target = safe_next(request)
    if request.user.is_authenticated:
        return redirect(target)
    username = ""
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        if not username or len(username) > 150 or not password or len(password) > 128:
            error = "auth_error_fields"
        elif mode == "register":
            users = get_user_model().objects
            try:
                with transaction.atomic():
                    user = users.create_user(username=username, password=password)
            except IntegrityError:
                if not users.filter(username=username).exists():
                    raise
                error = "auth_error_duplicate"
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                logger.info("Account registered user_id=%s", user.pk)
                return redirect(target)
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                error = "auth_error_credentials"
            else:
                login(request, user)
                logger.info("Account logged in user_id=%s", user.pk)
                return redirect(target)
        logger.warning("Account form rejected action=%s reason=%s", mode, error)
    return render(
        request,
        "account.html",
        {
            "mode": mode,
            "next": target,
            "username": username,
            "error_key": error,
        },
        status=400 if error else 200,
    )


@never_cache
@csrf_protect
@require_POST
def sign_out(request):
    """Closes current database session via POST with valid CSRF token, returns to homepage; does not
    delete account or interview records.
    """
    user_id = request.user.pk
    logout(request)
    logger.info("Account logged out user_id=%s", user_id)
    return redirect("/")


class AccountRequiredMiddleware:
    """Function: Produce HTTP login gate; allow anonymous access only for public paths, independent
    of UUID obscurity.
    """

    def __init__(self, get_response):
        """Store downstream processor; instance has no database or network side effects.
        """
        self.get_response = get_response

    def __call__(self, request):
        """Read deployment switches and user identity set by
        SessionMiddleware/AuthenticationMiddleware.

        Anonymous APIs explicitly return 401 JSON; page redirects to login while preserving current
        path; authentication failure prevents entry into business layer.
        Default local loopback development mode does not enforce login; data queries still filter by
        user or unassigned data.
        """
        if (
            not settings.INTERVIEW_REQUIRE_LOGIN
            or request.user.is_authenticated
            or request.path in PUBLIC_PATHS
            or request.path in PUBLIC_ASSETS
        ):
            return self.get_response(request)
        if request.path.startswith("/api/"):
            response = JsonResponse(
                {"error": {"code": "authentication_required", "detail": "Please sign in."}},
                status=401,
            )
            response["Cache-Control"] = "no-store"
            return response
        return redirect("/login/?" + urlencode({"next": request.get_full_path()}))
