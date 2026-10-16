"""Real database/API and Agent integration; external LLM calls are explicit offline fixtures."""

import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from app.parsing.resume import ResumeExtraction
from interviews.agent_models import AgentInterview
from interviews.agent_records import reserve_request
from interviews.agent_session import AgentSession
from interviews.agent_socket import Start
from interviews.api.resume_versions import resolve_resume_version
from interviews.resume_models import ResumeVersion
from interviews.resume_profile import prepare_profile, read_profile

from .agent_fixtures import RESUME, FixtureLLM


class ResumeProfileTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="profile-owner")
        self.other = get_user_model().objects.create_user(username="profile-other")
        self.client.force_authenticate(self.owner)
        self.llm = FixtureLLM()
        factory = patch("interviews.resume_profile.OpenAILLM", return_value=self.llm)
        self.factory = factory.start()
        self.addCleanup(factory.stop)

    def upload(self):
        response = self.client.post("/api/resume-versions/", {"text": RESUME}, format="json")
        self.assertEqual(response.status_code, 201)
        return ResumeVersion.objects.get(pk=response.data["id"]), response

    def test_upload_persists_canonical_profile_and_retry_reuses_it(self):
        version, response = self.upload()
        self.assertTrue(response.data["can_interview"])
        self.assertEqual(response.data["interview_profile_status"], "ready")
        self.assertNotIn("interview_profile", response.data)
        profile, name = read_profile(version, candidate_id="candidate-new")
        self.assertEqual(profile.candidate_id, "candidate-new")
        self.assertEqual(name, "Alex")
        self.assertTrue(profile.projects[0].claims)
        self.assertEqual(
            version.interview_profile["candidate_profile"]["projects"],
            profile.model_dump(mode="json")["projects"],
        )
        calls = len(self.llm.calls)
        retry = self.client.post(f"/api/resume-versions/{version.pk}/prepare-interview/")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(len(self.llm.calls), calls)
        self.assertTrue(
            self.client.get("/api/resume-versions/").data["results"][0]["can_interview"]
        )

    def test_edition_gets_fresh_profile_without_overwriting_original(self):
        version, _ = self.upload()
        before = self.llm.calls.count(ResumeExtraction)
        response = self.client.post(
            f"/api/resume-versions/{version.pk}/editions/",
            {
                "units": {"projects": RESUME + " Added a cache."},
                "slots": {"skills": ["Python"]},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["can_interview"])
        edition = ResumeVersion.objects.get(pk=response.data["id"])
        self.assertNotEqual(edition.interview_profile_hash, version.interview_profile_hash)
        self.assertGreater(self.llm.calls.count(ResumeExtraction), before)
        version.refresh_from_db()
        self.assertEqual(version.text, RESUME)

    def test_failure_preserves_text_and_retry_recovers(self):
        with patch(
            "interviews.resume_profile.parse_resume_profile", side_effect=RuntimeError("test-only")
        ):
            version, response = self.upload()
        self.assertEqual(version.text, RESUME)
        self.assertEqual(version.status, "ready")
        self.assertEqual(response.data["interview_profile_error"], "resume_profile_failed")
        self.assertFalse(response.data["can_interview"])
        command = Start(type="start", request_id=uuid4(), resume_version_id=version.pk)
        with self.assertRaises(ValueError):
            async_to_sync(resolve_resume_version)(command, self.owner.pk)
        retried = self.client.post(f"/api/resume-versions/{version.pk}/prepare-interview/")
        self.assertEqual(retried.status_code, 200)
        self.assertTrue(retried.data["can_interview"])

    def test_old_version_backfill_is_explicit_and_owned(self):
        version = ResumeVersion.objects.create(owner=self.owner, text=RESUME, status="ready")
        url = f"/api/resume-versions/{version.pk}/prepare-interview/"
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.factory.assert_not_called()
        self.client.force_authenticate(self.owner)
        self.assertFalse(
            self.client.get(f"/api/resume-versions/{version.pk}/").data["can_interview"]
        )
        self.assertEqual(
            self.client.post(url, {"candidate_profile": {}}, format="json").status_code, 400
        )
        self.factory.assert_not_called()
        self.assertEqual(self.client.post(url).status_code, 200)

    def test_processing_blocks_duplicate_work_and_delete_but_can_recover_crash(self):
        version = ResumeVersion.objects.create(
            owner=self.owner, text=RESUME, status="ready", interview_profile_status="processing"
        )
        url = f"/api/resume-versions/{version.pk}/"
        self.assertEqual(self.client.post(url + "prepare-interview/").status_code, 409)
        self.assertEqual(self.client.delete(url).status_code, 409)
        self.factory.assert_not_called()
        ResumeVersion.objects.filter(pk=version.pk).update(
            updated_at=timezone.now() - timedelta(minutes=11)
        )
        self.assertEqual(self.client.post(url + "prepare-interview/").status_code, 200)

    def test_reentrant_preparation_cannot_claim_same_work(self):
        version = ResumeVersion.objects.create(owner=self.owner, text=RESUME, status="ready")
        from app.parsing.resume import parse_resume_profile as real_parser

        async def during_parse(text, **kwargs):
            from asgiref.sync import sync_to_async

            pending = await sync_to_async(prepare_profile)(version.pk, self.owner.pk)
            self.assertEqual(pending.interview_profile_status, "processing")
            return await real_parser(text, **kwargs)

        with patch("interviews.resume_profile.parse_resume_profile", side_effect=during_parse):
            result = prepare_profile(version.pk, self.owner.pk)
        self.assertTrue(result.can_interview)
        self.assertEqual(self.factory.call_count, 1)

    def test_stale_or_invalid_cache_requires_explicit_repair(self):
        version, _ = self.upload()
        for changes in [
            {"interview_profile_version": "old"},
            {"interview_profile_hash": "wrong"},
            {"interview_profile": {"candidate_profile": {}}},
        ]:
            with self.subTest(changes=list(changes)):
                ResumeVersion.objects.filter(pk=version.pk).update(**changes)
                command = Start(type="start", request_id=uuid4(), resume_version_id=version.pk)
                before = self.factory.call_count
                with self.assertRaises(ValueError):
                    async_to_sync(resolve_resume_version)(command, self.owner.pk)
                self.assertEqual(self.factory.call_count, before)
                self.assertTrue(prepare_profile(version.pk, self.owner.pk).can_interview)

    def test_two_interviews_load_same_database_profile_without_parsing(self):
        version, _ = self.upload()

        async def run():
            profiles = []
            for _ in range(2):
                session = AgentSession(llm=FixtureLLM())
                command = Start(type="start", request_id=uuid4(), resume_version_id=version.pk)
                resolved = await resolve_resume_version(command, self.owner.pk)
                await reserve_request(
                    session.interview_id,
                    resolved,
                    owner_id=self.owner.pk,
                    resume_version_id=version.pk,
                )
                result = await session.start(resolved)
                self.assertEqual(result["type"], "question")
                self.assertNotIn(ResumeExtraction, session.llm.calls)
                self.assertEqual(session.profile.candidate_id, f"candidate-{session.interview_id}")
                profiles.append(session.profile)
                session.close()
            self.assertNotEqual(profiles[0].candidate_id, profiles[1].candidate_id)
            self.assertEqual(profiles[0].projects, profiles[1].projects)

        with patch(
            "interviews.agent_session.parse_resume_profile",
            side_effect=AssertionError("Must load DB"),
        ):
            async_to_sync(run)()
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(AgentInterview.objects.filter(resume_version=version).count(), 2)

    def test_bound_bad_cache_never_falls_back_to_raw_parser(self):
        version, _ = self.upload()
        session = AgentSession(llm=FixtureLLM())
        AgentInterview.objects.create(
            id=session.interview_id,
            owner=self.owner,
            resume_version=version,
            resume_text_snapshot=RESUME,
        )
        ResumeVersion.objects.filter(pk=version.pk).update(interview_profile_status="failed")
        command = Start(type="start", request_id=uuid4(), resume_text=RESUME)
        with patch("interviews.agent_session.parse_resume_profile") as parser:
            with self.assertRaises(ValueError):
                async_to_sync(session.prepare)(command)
            parser.assert_not_called()
        session.close()

    def test_unready_pdf_cannot_prepare(self):
        version = ResumeVersion.objects.create(owner=self.owner, original_pdf=b"%PDF-test")
        self.assertEqual(
            self.client.post(f"/api/resume-versions/{version.pk}/prepare-interview/").status_code,
            409,
        )
        self.factory.assert_not_called()

    @override_settings(PDF_TASK_EXECUTION="inline")
    def test_pdf_profile_failure_keeps_successful_extraction_and_emits_readiness(self):
        version = ResumeVersion.objects.create(owner=self.owner, original_pdf=b"%PDF-test")

        async def extracted(data, *, mode="traditional"):
            yield json.dumps({"type": "result", "text": RESUME, "pages": []}).encode()

        with (
            patch("interviews.resume_api.resume_events", extracted),
            patch(
                "interviews.resume_profile.parse_resume_profile", side_effect=RuntimeError("test")
            ),
        ):
            response = self.client.post(f"/api/resume-versions/{version.pk}/parse/")
            events = [json.loads(line) for line in response]
        self.assertEqual(events[0], {"type": "progress", "stage": "interview_profile"})
        self.assertEqual(events[-1]["type"], "result")
        self.assertFalse(events[-1]["can_interview"])
        self.assertEqual(events[-1]["interview_profile_status"], "failed")
        version.refresh_from_db()
        self.assertEqual(version.text, RESUME)
        self.assertEqual(version.status, "ready")
        self.assertEqual(
            self.client.post(f"/api/resume-versions/{version.pk}/prepare-interview/").status_code,
            200,
        )
