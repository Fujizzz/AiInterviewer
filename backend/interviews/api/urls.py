"""Responsibilities: Register REST and auxiliary API routes.
Implementation: Use DefaultRouter for resource endpoints and explicit routes for PDF stage
streaming, profile data, and recommendations.
Related Modules: api.views handles request adapters; resume_api provides PDF parsing;
recommendation.api provides recommendation sorting.

Declaration Index:
None

Variable Index:
- router:
  Router registering questions, sessions, read-only agent-interviews, and user's resume-versions.
- urlpatterns:
  Django route list, including user profile, PDF parsing, and bidirectional recommendation entry
  points; resource routes appended at end.
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from ..recommendation.api import recommend_candidates, recommend_jobs
from ..resume_api import parse_resume_pdf
from .agent_history import AgentHistoryViewSet
from .profile import profile
from .resume_versions import ResumeVersionViewSet
from .views import QuestionViewSet, SessionViewSet

router = DefaultRouter()
router.register("questions", QuestionViewSet)
router.register("sessions", SessionViewSet)
router.register("agent-interviews", AgentHistoryViewSet, basename="agent-interview")
router.register("resume-versions", ResumeVersionViewSet, basename="resume-version")
urlpatterns = [
    path("profile/", profile),
    path("resume/parse/", parse_resume_pdf),
    path("recommendations/jobs/", recommend_jobs, name="recommend-jobs"),
    path("recommendations/candidates/", recommend_candidates, name="recommend-candidates"),
] + router.urls
