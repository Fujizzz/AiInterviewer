"""Responsibilities: Serve the unified homepage, development pages, and explicitly allowed frontend
assets.
Implementation: Read only ASSETS entries, render HTML through Django templates with account
navigation/CSRF context, and disable caching.
Related Modules: config.urls routes pages and assets here; frontend and diagnostics directories
provide the allowlisted files.

Declaration Index:
- demo_asset: Serve an allowlisted resource and prohibit HTTP caching.

Variable Index:
- ASSETS: Allowlist of static resource filenames; secret files such as .env are excluded.
"""

from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse
from django.shortcuts import render

ASSETS = {
    "index.html",
    "app.js",
    "view.js",
    "media.js",
    "stream-client.js",
    "style.css",
    "agent.html",
    "agent.js",
    "interview-progress.js",
    "interview-history.js",
    "interview-review.html",
    "interview-history.css",
    "agent.css",
    "home.html",
    "home.css",
    "workspace.css",
    "interview-camera.js",
    "i18n.js",
    "account.css",
    "interview-voice.js",
    "speech-capture.js",
    "speech-worklet.js",
    "pcm-resampler.js",
    "pixel-player.js",
    "digital-human-check.html",
    "digital-human-check.js",
    "resumes.html",
    "resumes.js",
    "resumes.css",
}


def demo_asset(request, name):
    """Serve an allowlisted resource without HTTP caching.

    Inputs are a Django request and one resource filename, never a traversable path. Check the
    allowlist first; redirect
    anonymous users from account, review, and interview HTML pages to /login/. Render HTML with
    escaped account context.
    Return HttpResponse or a login redirect; missing allowlisted assets/builds raise Http404. Read
    application assets only,
    without reading or persisting media data.
    """
    if name not in ASSETS:
        raise Http404
    if (
        name in {"resumes.html", "agent.html", "interview-review.html"}
        and not request.user.is_authenticated
    ):
        return redirect_to_login(request.get_full_path(), login_url="/login/")
    content_type = {
        ".html": "text/html",
        ".js": "text/javascript",
        ".css": "text/css",
    }[Path(name).suffix]
    asset = settings.BASE_DIR / "frontend" / name
    if name == "pixel-player.js":
        asset = settings.BASE_DIR / "frontend" / "digital-human" / "dist" / name
    elif name in {"digital-human-check.html", "digital-human-check.js"}:
        asset = settings.BASE_DIR / "diagnostics" / name
    if not asset.is_file():
        raise Http404("Build backend/frontend/digital-human before connecting the avatar.")
    response = (
        render(request, name)
        if Path(name).suffix == ".html"
        else HttpResponse(
            asset.read_bytes(),
            content_type=content_type,
        )
    )
    response["Cache-Control"] = "no-store"
    return response
