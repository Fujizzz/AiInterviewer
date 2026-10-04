"""Responsibilities: Compose page, health-check, speech, presentation, and REST routes.
Implementation: Bind Django paths to account, demo, health, speech, presentation, and API handlers.
Related Modules: interviews.accounts, interviews.api, interviews.demo, interviews.speech,
interviews.presentation, and
interviews.api.urls implement the routed behavior.
Declaration Index:
None
Variable Index:
- urlpatterns: Django routes for accounts, pages, diagnostics, health checks, speech, presentation,
  and business APIs.
"""

from django.urls import include, path
from interviews.accounts import account_page, sign_out
from interviews.api.views import health
from interviews.demo import demo_asset
from interviews.presentation.views import plan as presentation_plan
from interviews.speech.views import audio, tts

urlpatterns = [
    path("login/", account_page, {"mode": "login"}),
    path("register/", account_page, {"mode": "register"}),
    path("logout/", sign_out),
    path("", demo_asset, {"name": "home.html"}),
    path("agent/", demo_asset, {"name": "agent.html"}),
    path("resumes/", demo_asset, {"name": "resumes.html"}),
    path("interview-review/", demo_asset, {"name": "interview-review.html"}),
    path("stream-demo/", demo_asset, {"name": "index.html"}),
    path("stream-demo/<str:name>", demo_asset),
    path("api/health/", health),
    path("api/speech/tts/", tts),
    path("api/speech/audio/<uuid:utterance_id>/", audio),
    path("api/presentation/plan/", presentation_plan),
    path("api/", include("interviews.api.urls")),
]
