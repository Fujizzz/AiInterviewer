"""Responsibilities: Verify user isolation, parsing lifecycle, and interview binding in version API
without accessing real model services.
Implementation: Use real isolated test database and HTTP requests; explicit stubs for parsing
streams; maintain production pipeline call boundaries.
Related Modules: resume_versions, agent_records, agent_history, and their post-migration
relationship constraints.
Declaration Index:
- parsing_fixture: Provides a deterministic successful parsing event.
- failed_fixture: Provides explicitly failed parsing events.
- pending_fixture: Provides non-terminal parsing progress.
- ResumeVersionTests: Version and permission integration verification.
- ResumeVersionTests.setUp: Establishes two accounts and an authenticated client.
- ResumeVersionTests.test_versions_current_and_permissions: Validates immutable versions, current
  selection, and cross-user rejection.
- ResumeVersionTests.test_pdf_parse_and_private_download: Validates upload/parsing separation,
  persistent text, and original file
  download.
- ResumeVersionTests.test_interview_binding_and_deletion: Validates bound snapshots, historical
  ownership, and reference deletion
  restrictions.
- ResumeVersionTests.test_invalid_source_and_unready_version: Validates mutually exclusive inputs
  and unready versions not triggering
  models.
- ResumeVersionTests.test_failed_and_interrupted_parse: Validates failed and interrupted stream
  states without claiming successful parsing.
- VersionSocketTests: Real protocol and database version access regression.
- VersionSocketTests.test_prepare_start_binds_same_version: Validates authorized version passing
  through secure gateway and fixed
  data/history.
Variable Index:
None
"""

import json
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from interviews.agent_models import AgentInterview
from interviews.agent_records import reserve_request
from interviews.agent_socket import Start, agent_socket, parse_command
from interviews.api.resume_versions import resolve_resume_version, version_events
from interviews.resume_models import ResumeVersion

from .agent_fixtures import RESUME, FixtureLLM, SafetyTestMixin
from .test_agent_progress import collect_until, disconnect, read, send_command


async def parsing_fixture(data, *, mode="traditional"):
    """Input mock PDF and mode (default traditional), output deterministic result; simulate external
    parsing without validating PDF or visual service.
    """
    yield b'{"type":"result","text":"Built Python APIs","pages":[]}'


async def failed_fixture(data, *, mode="traditional"):
    """Input mock bytes and mode, output fixed error event; simulate pipeline reporting failure,
    without swallowing real exceptions.
    """
    yield b'{"type":"error","detail":"test-only"}'


async def pending_fixture(data, *, mode="traditional"):
    """Input mock bytes and mode, output non-terminal progress; used to consume and close stream,
    verifying interrupted state.
    """
    yield b'{"type":"progress","stage":"rules"}'


class ResumeVersionTests(APITestCase):
    """Function: Validate authorization and persistence; input is isolated account and database;
    constraint: no billing model calls are made.
    """

    def setUp(self):
        """Establish two test users and authenticate the first; test framework isolates database,
        independent of local users or real secrets.
        """
        self.owner = get_user_model().objects.create_user(
            username="resume-owner", password="test-only"
        )
        self.other = get_user_model().objects.create_user(
            username="resume-other", password="test-only"
        )
        self.client.force_authenticate(self.owner)

    def test_versions_current_and_permissions(self):
        """Two uploads generate distinct versions; current selection does not overwrite text;
        cross-user queries/selection/deletion return 404, anonymous access denied.
        """
        first = self.client.post("/api/resume-versions/", {"text": "Version one"}, format="json")
        second = self.client.post("/api/resume-versions/", {"text": "Version two"}, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertNotEqual(first.data["id"], second.data["id"])
        for item in [first, second]:
            self.assertEqual(
                self.client.post(f"/api/resume-versions/{item.data['id']}/current/").status_code,
                200,
            )
        self.assertEqual(ResumeVersion.objects.filter(is_current=True).count(), 1)
        self.assertEqual(ResumeVersion.objects.get(pk=first.data["id"]).text, "Version one")
        self.client.force_authenticate(self.other)
        url = f"/api/resume-versions/{first.data['id']}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url + "current/").status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.get("/api/resume-versions/").data["count"], 0)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/resume-versions/").status_code, 403)

    def test_pdf_parse_and_private_download(self):
        """Use non-real PDF parsing stub; upload only stores file, explicit stream consumption
        completes parsing.
        Download raw bytes and prevent re-parsing.
        """
        data = b"%PDF-test-only"
        response = self.client.post(
            "/api/resume-versions/",
            {"file": SimpleUploadedFile("resume.pdf", data)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        url = f"/api/resume-versions/{response.data['id']}/"
        self.assertEqual(response.data["status"], "uploaded")
        self.assertEqual(self.client.post(url + "current/").status_code, 400)
        downloaded = self.client.get(url + "download/")
        self.assertEqual(downloaded.content, data)
        self.assertEqual(downloaded["Cache-Control"], "no-store, private")
        with (
            patch("interviews.resume_api.resume_events", parsing_fixture),
            patch("interviews.api.resume_versions.settings.PDF_TASK_EXECUTION", "inline"),
        ):
            stream = self.client.post(url + "parse/")
            self.assertEqual(stream.status_code, 200)
            list(stream)
        version = ResumeVersion.objects.get(pk=response.data["id"])
        self.assertEqual(version.status, "ready")
        self.assertEqual(version.extraction_mode, "traditional")
        self.assertEqual(version.text, "Built Python APIs")
        self.assertEqual(self.client.post(url + "parse/").status_code, 409)
        advanced = ResumeVersion.objects.create(owner=self.owner, original_pdf=data)
        with (
            patch("interviews.resume_api.resume_events", parsing_fixture),
            patch("interviews.api.resume_versions.settings.PDF_TASK_EXECUTION", "inline"),
        ):
            list(
                self.client.post(
                    f"/api/resume-versions/{advanced.pk}/parse/",
                    {"mode": "advanced"},
                    format="json",
                )
            )
        advanced.refresh_from_db()
        self.assertEqual(advanced.extraction_mode, "advanced")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url + "download/").status_code, 404)

    def test_interview_binding_and_deletion(self):
        """Real transaction binds version; subsequent current selection does not alter historical
        text; referenced versions cannot be deleted, unreferenced ones may be.
        """
        version = ResumeVersion.objects.create(
            owner=self.owner, text="Original input", status="ready"
        )
        command = Start(request_id=uuid4(), type="start", resume_version_id=version.pk)
        resolved = async_to_sync(resolve_resume_version)(command, self.owner.pk)
        interview_id = uuid4()
        async_to_sync(reserve_request)(
            interview_id, resolved, owner_id=self.owner.pk, resume_version_id=version.pk
        )
        interview = AgentInterview.objects.get(pk=interview_id)
        self.assertEqual(interview.resume_text_snapshot, "Original input")
        self.assertEqual(interview.resume_version_id, version.pk)
        newer = ResumeVersion.objects.create(owner=self.owner, text="New input", status="ready")
        self.client.post(f"/api/resume-versions/{newer.pk}/current/")
        interview.refresh_from_db()
        self.assertEqual(interview.resume_text_snapshot, "Original input")
        history = self.client.get(f"/api/agent-interviews/{interview_id}/")
        self.assertEqual(str(history.data["resume_version_id"]), str(version.pk))
        self.assertEqual(history.data["processing"]["status"], "running")
        self.assertEqual(self.client.delete(f"/api/resume-versions/{version.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/resume-versions/{newer.pk}/").status_code, 204)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/agent-interviews/{interview_id}/").status_code, 404)

    def test_invalid_source_and_unready_version(self):
        """Validate mutually exclusive sources and personal ready condition; no model stub calls, as
        failure occurs before input reading.
        """
        version = ResumeVersion.objects.create(owner=self.owner)
        command = Start(request_id=uuid4(), type="start", resume_version_id=version.pk)
        for owner_id in [self.owner.pk, self.other.pk, None]:
            with self.assertRaises(ValueError):
                async_to_sync(resolve_resume_version)(command, owner_id)
        raw = {
            "type": "start",
            "request_id": str(uuid4()),
            "resume_text": "text",
            "resume_version_id": str(version.pk),
        }
        with self.assertRaises(ValueError):
            parse_command(json.dumps(raw))
        self.assertEqual(
            self.client.post(
                f"/api/resume-versions/{version.pk}/parse/", {"mode": ["advanced"]}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/resume-versions/", {"text": "x", "file": "x"}, format="json"
            ).status_code,
            400,
        )

    async def test_failed_and_interrupted_parse(self):
        """Simulate existing error events and premature closure; real database state must not become
        ready, and external service failures are not validated.
        """
        from asgiref.sync import sync_to_async

        version = await sync_to_async(ResumeVersion.objects.create)(
            owner=self.owner, status="parsing"
        )
        with (
            patch("interviews.resume_api.resume_events", failed_fixture),
            patch("interviews.api.resume_versions.settings.PDF_TASK_EXECUTION", "inline"),
        ):
            async for _ in version_events(version.pk, b"%PDF-test"):
                pass
        await sync_to_async(version.refresh_from_db)()
        self.assertEqual(version.status, "failed")
        version.status = "parsing"
        await sync_to_async(version.save)()
        with (
            patch("interviews.resume_api.resume_events", pending_fixture),
            patch("interviews.api.resume_versions.settings.PDF_TASK_EXECUTION", "inline"),
        ):
            stream = version_events(version.pk, b"%PDF-test")
            await anext(stream)
            await stream.aclose()
        await sync_to_async(version.refresh_from_db)()
        self.assertEqual(version.status, "interrupted")


class VersionSocketTests(SafetyTestMixin, TransactionTestCase):
    """Function: Validate complete version protocol path; logic: real Agent/database works with
    model and security stubs.
    Constraint: No real vendor involved.
    """

    async def test_prepare_start_binds_same_version(self):
        """Authenticate scope uses personal ready version; prepare/start both go through gateway.
        Structured data saved in context with fixed references.
        """
        from asgiref.sync import sync_to_async

        owner = await sync_to_async(get_user_model().objects.create_user)(username="version-socket")
        version = await ResumeVersion.objects.acreate(owner=owner, text=RESUME, status="ready")
        with patch("interviews.agent_session.BackendLLM", return_value=FixtureLLM()):
            comm = ApplicationCommunicator(
                agent_socket,
                {
                    "type": "websocket",
                    "path": "/ws/agent/",
                    "scheme": "ws",
                    "user": owner,
                    "client": ("127.0.0.1", 12345),
                    "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")],
                },
            )
            await comm.send_input({"type": "websocket.connect"})
            self.assertEqual((await comm.receive_output())["type"], "websocket.accept")
            await read(comm)
            try:
                rid = await send_command(comm, "prepare", resume_version_id=str(version.pk))
                prepared = (await collect_until(comm, "prepared", rid))[-1]
                rid = await send_command(comm, "start", resume_version_id=str(version.pk))
                question = (await collect_until(comm, "question", rid))[-1]
                interview = await AgentInterview.objects.aget(pk=question["interview_id"])
                self.assertEqual(interview.resume_version_id, version.pk)
                self.assertEqual(interview.resume_text_snapshot, RESUME)
                self.assertEqual(
                    interview.context["candidate_profile"], prepared["candidate_profile"]
                )
            finally:
                await disconnect(comm)
