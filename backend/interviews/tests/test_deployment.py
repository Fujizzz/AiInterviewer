"""Responsibilities: Verify access-boundary behavior and job-recommendation probe outcomes against
an isolated local service.

Implementation: Construct proxy ASGI scopes and verify proxy protocol; use a local LiveServer and
frozen models to exercise Session/CSRF handling and probe cleanup. Stub only the precision-ranking
API; no production database or external service is accessed.
Related Modules: deploy.smoke, interviews.access, interviews.middleware,
interviews.recommendation.rerank, and interviews.resume_models.

Declaration Index:
- deployment_output: External API stubs only, synthesize precision ranking from actual candidates.
- DeploymentAccessTests: Deployment access policy regression suite without database access.
- DeploymentAccessTests.scope: Construct HTTPS WebSocket request from same-machine proxy.
- DeploymentAccessTests.test_proxy_origin_and_peer: Allow specified same-origin proxy, reject
  external sites, remote hosts, and forged Host.
- DeploymentAccessTests.test_http_proxy_origin: HTTP application retains same-origin and loopback
  restrictions.
- DeploymentAccessTests.test_local_defaults: Default development configuration continues to reject
  public Host.
- DeploymentRecommendationTests: Validate the probe's published-job path using an isolated database
  and local HTTP service.
- DeploymentRecommendationTests.test_live_catalog_and_cleanup: Exercise local 100-job coarse ranking
  and stubbed precision ranking, then
  clean up probe records.
- DeploymentRecommendationTests.test_missing_catalog_fails_and_cleans_up: Missing source explicitly
  fails but still cleans up.

Variable Index:
None
"""

from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.http import HttpResponse
from django.test import LiveServerTestCase, RequestFactory, SimpleTestCase, override_settings

from deploy.smoke import verify_recommendations
from interviews.access import websocket_allowed
from interviews.middleware import LocalOnlyMiddleware
from interviews.recommendation.rerank import RerankOutput
from interviews.resume_models import ResumeVersion


def deployment_output(payload):
    """Input actual coarse-ranking candidates, return top final_count jobs and synthesized bilingual
    rationale; substitute only external API.
    """
    return RerankOutput.model_validate(
        {
            "jobs": [
                {
                    "job_id": item["job_id"],
                    "reason_zh": "Python 技能已知，其他信息未知。",
                    "reason_en": "Python is known; other fields are unknown.",
                }
                for item in payload["shortlist"][: payload["final_count"]]
            ]
        }
    ), "test-api"


class DeploymentAccessTests(SimpleTestCase):
    """Function: Verify proxy access boundary; use only in-memory requests, not representative of
    Nginx authentication completion.
    """

    def scope(
        self,
        host="interview.example",
        origin="https://interview.example",
        peer="127.0.0.1",
        scheme="wss",
    ):
        """Input host, source, peer, and protocol; return ASGI check fields, no I/O side effects."""
        return {
            "headers": [(b"host", host.encode()), (b"origin", origin.encode())],
            "client": (peer, 12345),
            "scheme": scheme,
        }

    @override_settings(ALLOWED_HOSTS=["interview.example"])
    def test_proxy_origin_and_peer(self):
        """Only loopback proxy with configured Host and same origin passes; each trust condition
        change must be rejected.
        """
        self.assertTrue(websocket_allowed(self.scope()))
        for changes in (
            {"origin": "https://foreign.example"},
            {"peer": "192.0.2.10"},
            {"host": "foreign.example"},
            {"host": ""},
            {"scheme": "ws"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(websocket_allowed(self.scope(**changes)))

    @override_settings(
        ALLOWED_HOSTS=["interview.example"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    )
    def test_http_proxy_origin(self):
        """Simulate Nginx-overridden protocol header request; allow same-origin, reject cross-origin
        and non-loopback.
        """
        request = RequestFactory().get(
            "/",
            HTTP_HOST="interview.example",
            HTTP_ORIGIN="https://interview.example",
            HTTP_X_FORWARDED_PROTO="https",
            REMOTE_ADDR="127.0.0.1",
        )
        middleware = LocalOnlyMiddleware(HttpResponse)
        self.assertEqual(middleware(request).status_code, 200)
        request.META["HTTP_ORIGIN"] = "https://foreign.example"
        # Django caches request headers; clear the snapshot before simulating a new request.
        del request.headers
        self.assertEqual(middleware(request).status_code, 403)
        request.META["HTTP_ORIGIN"] = "https://interview.example"
        request.META["REMOTE_ADDR"] = "192.0.2.10"
        del request.headers
        self.assertEqual(middleware(request).status_code, 403)

    @override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "[::1]"])
    def test_local_defaults(self):
        """Retain local development Host set, verify production Host does not implicitly take effect
        in default configuration.
        """
        self.assertFalse(websocket_allowed(self.scope()))
        self.assertTrue(
            websocket_allowed(self.scope(host="localhost", origin="http://localhost", scheme="ws"))
        )


@override_settings(
    INTERVIEW_REQUIRE_LOGIN=True,
    ALLOWED_HOSTS=["47.239.50.129", "localhost"],
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    STATIC_URL="/static/",
    MEDIA_URL="/media/",
)
class DeploymentRecommendationTests(LiveServerTestCase):
    """Function: Verify real HTTP behavior and cleanup during release acceptance; logic: use
    isolated database and local thread service.

    Prerequisite: Provide static/media URLs only to LiveServer file processor, do not modify
    production routing or configuration.
    Constraint: Use Session, CSRF, and original frozen models, no authentication/coarse-rank model
    stubs, external APIs separately simulated;
    this local WSGI validation does not represent production ASGI or Nginx success; real deployment
    is verified by the same probe checking running services.
    """

    def test_live_catalog_and_cleanup(self):
        """Input isolated user database and complete experiment directory; verify real ranking
        succeeds, no account/resume/session residue.
        """
        path = Path(__file__).resolve().parents[1] / "recommendation/data/experience-jobs.json"
        with override_settings(RECOMMENDATION_JOB_CATALOG=str(path)):
            with patch("interviews.recommendation.rerank.request_rerank") as api:
                api.side_effect = deployment_output
                verify_recommendations(self.live_server_url)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(ResumeVersion.objects.count(), 0)
        self.assertEqual(Session.objects.count(), 0)

    @override_settings(RECOMMENDATION_JOB_CATALOG="")
    def test_missing_catalog_fails_and_cleans_up(self):
        """Input explicitly empty directory configuration; verify HTTP 503 propagation, failure path
        still deletes all probe records, no rollback.
        """
        with self.assertRaises(HTTPError) as error:
            verify_recommendations(self.live_server_url)
        self.assertEqual(error.exception.code, 503)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(ResumeVersion.objects.count(), 0)
        self.assertEqual(Session.objects.count(), 0)
