"""Responsibilities: Verify access-boundary behavior and job-recommendation probe outcomes against
an isolated local service.

Implementation: Construct proxy ASGI scopes and verify proxy protocol; use a local LiveServer and
frozen models to exercise Session/CSRF handling and probe cleanup. Stub only the precision-ranking
API; no production database or external service is accessed.
Related Modules: deploy.smoke validates the released avatar bundle/archive; interviews.access,
interviews.middleware,
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
- DeploymentFrontendTests: Verify bundle contract and release safety offline.
- DeploymentFrontendTests.setUp: Create a synthetic ESM fixture in an isolated temporary directory.
- DeploymentFrontendTests.archive: Write only explicitly provided synthetic tar members.
- DeploymentFrontendTests.test_missing_invalid_and_required_exports: Reject invalid ESM contracts.
- DeploymentFrontendTests.test_verified_archive: Accept one exact artifact with safe source files.
- DeploymentFrontendTests.test_missing_duplicate_and_stale_artifact: Reject incomplete or mismatched
  archive additions.
- DeploymentFrontendTests.test_unsafe_archive_members: Reject forbidden assets, secrets, and links.
- DeploymentAvatarTests: Require production login for actual local HTTP player checks.
- DeploymentAvatarTests.setUp: Create separate expected/player route assets in a temporary base dir.
- DeploymentAvatarTests.test_authenticated_asset_and_cleanup: Anonymous redirect, authenticated
  exact module and probe cleanup with login required.
- DeploymentAvatarTests.test_missing_or_stale_asset_cleans_up: Missing/stale route still revokes
  temporary account and Session.
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

import io
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.http import HttpResponse
from django.test import LiveServerTestCase, RequestFactory, SimpleTestCase, override_settings

from deploy.smoke import (
    verify_avatar_player,
    verify_frontend_bundle,
    verify_recommendations,
    verify_release_bundle,
)
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


class DeploymentFrontendTests(SimpleTestCase):
    """Verify synthetic release artifacts without services, models, or secrets."""

    def setUp(self):
        """Create one valid module fixture; the test runner cleans only its temporary directory."""
        temporary = TemporaryDirectory(prefix="avatar-release-probe-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.bundle = self.directory / "pixel-player.js"
        self.content = (
            b"const AvatarPlayer = class {}; const PresentationController = class {}; "
            b"const loadAvatarConfiguration = async () => {}; "
            b"export { AvatarPlayer, PresentationController, loadAvatarConfiguration };\n"
        )
        self.bundle.write_bytes(self.content)
        self.bundle_path = "backend/frontend/digital-human/dist/pixel-player.js"

    def archive(self, entries):
        """Write name/content pairs; a None payload creates a forbidden symlink."""
        archive = self.directory / "source.tar"
        with tarfile.open(archive, "w") as output:
            for name, data in entries:
                member = tarfile.TarInfo(name)
                if data is None:
                    member.type = tarfile.SYMTYPE
                    member.linkname = "outside"
                    output.addfile(member)
                else:
                    member.size = len(data)
                    output.addfile(member, io.BytesIO(data))
        return archive

    def test_missing_invalid_and_required_exports(self):
        """Reject missing, empty, invalid UTF-8, or incomplete exports; accept the contract."""
        self.assertEqual(verify_frontend_bundle(self.bundle), self.content)
        self.bundle.unlink()
        with self.assertRaisesRegex(RuntimeError, "missing, empty"):
            verify_frontend_bundle(self.bundle)
        for content in (b"", b"\xff", b"export { AvatarPlayer };"):
            with self.subTest(content=content):
                self.bundle.write_bytes(content)
                with self.assertRaises(RuntimeError):
                    verify_frontend_bundle(self.bundle)

    def test_verified_archive(self):
        """Accept one exact artifact with a safe source file and public environment template."""
        archive = self.archive([
            ("deploy/smoke.py", b"# synthetic release"),
            (".env.example", b"PUBLIC_EXAMPLE="),
            (self.bundle_path, self.content),
        ])
        verify_release_bundle(archive, self.bundle)

    def test_missing_duplicate_and_stale_artifact(self):
        """Do not deploy an absent, duplicate, or altered asset from another build."""
        for entries in (
            [("backend/frontend/digital-human/src/pixel-player.js", self.content)],
            [(self.bundle_path, self.content), (self.bundle_path, self.content)],
            [(self.bundle_path, self.content.replace(b"class", b"wrong"))],
        ):
            with self.subTest(entries=[name for name, _ in entries]):
                with self.assertRaises(RuntimeError):
                    verify_release_bundle(self.archive(entries), self.bundle)

    def test_unsafe_archive_members(self):
        """Exclude UE, real env, management files, caches, traversal, and links."""
        for name, content in (
            ("DigitalHuman/Content/Face.uasset", b"synthetic"),
            ("backend/.env", b"synthetic"),
            ("server_info.txt", b"synthetic"),
            ("node_modules/lib.js", b"synthetic"),
            ("deploy/pixel-streaming/.infrastructure/lib.js", b"synthetic"),
            ("../escape", b"synthetic"),
            ("/absolute", b"synthetic"),
            ("linked", None),
        ):
            with self.subTest(name=name):
                archive = self.archive([(self.bundle_path, self.content), (name, content)])
                with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                    verify_release_bundle(archive, self.bundle)


@override_settings(
    INTERVIEW_REQUIRE_LOGIN=True,
    ALLOWED_HOSTS=["47.239.50.129", "localhost"],
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    STATIC_URL="/static/",
    MEDIA_URL="/media/",
)
class DeploymentAvatarTests(LiveServerTestCase):
    """Check actual Django HTTP authentication and cleanup; no UE, microphone, or model calls."""

    def setUp(self):
        """Create expected bundle separately so a route failure still reaches HTTP/probe cleanup."""
        temporary = TemporaryDirectory(prefix="avatar-http-probe-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.bundle = self.directory / "expected-player.js"
        self.content = (
            b"const AvatarPlayer = class {}; const PresentationController = class {}; "
            b"const loadAvatarConfiguration = async () => {}; "
            b"export { AvatarPlayer, PresentationController, loadAvatarConfiguration };\n"
        )
        self.bundle.write_bytes(self.content)
        self.asset = self.directory / "frontend/digital-human/dist/pixel-player.js"
        self.asset.parent.mkdir(parents=True)
        self.asset.write_bytes(self.content)

    def test_authenticated_asset_and_cleanup(self):
        """Redirect anonymous requests; a temporary real session receives the exact module."""
        with override_settings(BASE_DIR=self.directory):
            response = self.client.get(
                "/stream-demo/pixel-player.js",
                HTTP_HOST="47.239.50.129",
                HTTP_X_FORWARDED_PROTO="https",
            )
            self.assertEqual(response.status_code, 302)
            verify_avatar_player(self.live_server_url, self.bundle)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(Session.objects.count(), 0)

    def test_missing_or_stale_asset_cleans_up(self):
        """A failed HTTP fetch or mismatching build never leaves a probe account/session."""
        with override_settings(BASE_DIR=self.directory):
            self.asset.unlink()
            with self.assertRaises(HTTPError) as error:
                verify_avatar_player(self.live_server_url, self.bundle)
            self.assertEqual(error.exception.code, 404)
            self.assertEqual(get_user_model().objects.count(), 0)
            self.assertEqual(Session.objects.count(), 0)
            self.asset.write_bytes(b"<html>wrong build</html>")
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                verify_avatar_player(self.live_server_url, self.bundle)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(Session.objects.count(), 0)


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
