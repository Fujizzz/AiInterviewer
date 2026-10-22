"""Responsibilities: Verify canonical resume revisions remain isolated from interview snapshots.
Implementation: Exercise actual upload, lease claims, interview persistence, queued evaluation and
history APIs with explicit offline model/safety fixtures and controlled revision interleavings.
Related Modules: resume_profile, question_preparation, agent_session and evaluation_worker.
Declaration Index:
- ResumeDownstreamTests: Cover revision races and a complete bound interview through final history.
- ResumeDownstreamTests.setUp: Upload a canonical source with an owned offline API client.
- ResumeDownstreamTests.test_revised_profile_cannot_be_claimed_by_stale_question_worker:
  Interleave a canonical update between profile read and lease claim and forbid model work.
- ResumeDownstreamTests.test_revised_profile_cannot_be_claimed_by_stale_question_worker.revise:
  Return the old projection after advancing only the persisted canonical revision timestamp.
- ResumeDownstreamTests.test_start_rejects_questions_for_a_different_profile:
  Refuse a start snapshot that differs from the current canonical profile.
- ResumeDownstreamTests.test_preparation_progress_excludes_previous_profile_rows:
  Keep old-generation questions out of the owner-facing progress API without deleting them.
- ResumeDownstreamTests.test_bound_interview_evaluation_and_history_keep_original_snapshot:
  Complete queued scoring after an edition/current switch and bound cache revision.
- ResumeDownstreamTests.test_bound_interview_evaluation_and_history_keep_original_snapshot.edit:
  Save and select a real new edition without changing the existing interview binding.
Variable Index:
None
Constraints:
Database and provider responses are isolated fixtures. These tests verify data flow and revision
fencing, not live model quality, microphone operation, or provider availability.
"""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agents.question.prepared import review_version
from interviews.agent_models import AgentInterview
from interviews.agent_records import reserve_request
from interviews.agent_repository import DjangoInterviewRepository
from interviews.agent_session import AgentSession
from interviews.agent_socket import Answer, Start
from interviews.api.resume_versions import resolve_resume_version
from interviews.evaluation_models import AgentEvaluation, AgentEvaluationRun
from interviews.evaluation_worker import process_one, verified_report
from interviews.question_preparation import load_bound_questions, prepare_questions
from interviews.resume_models import PreparedResumeQuestion, ResumeVersion
from interviews.resume_profile import read_profile
from shared.resume_contract import ResumeExtraction

from .agent_fixtures import (
    ANSWER,
    RESUME,
    FixtureLLM,
    SafetyTestMixin,
    complete_fixture_request,
    enter_main_flow,
)


@override_settings(QUESTION_PREPARATION_EXECUTION="disabled", EVALUATION_FINAL_WAIT_SECONDS=0)
class ResumeDownstreamTests(SafetyTestMixin, TransactionTestCase):
    """Functionality: Test actual downstream consumers against isolated canonical revisions.
    Logic: Use real SQL and schemas; substitute only external provider/safety responses.
    Constraints: use_durable_worker enables the same delayed scoring path as deployment; no
    production record or service is accessed, and all test database changes are discarded.
    """

    use_durable_worker = True

    def setUp(self):
        """Functionality: Upload a canonical source through the authenticated API.
        Inputs: Isolated test database. Outputs: owner, API client and ready version state.
        Logic: Install safety fixtures then patch only the upload provider for one request.
        Constraints: Background question preparation is disabled to make interleavings explicit.
        """
        super().setUp()
        self.owner = get_user_model().objects.create_user(username="resume-downstream")
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        with patch("interviews.resume_profile.OpenAILLM", return_value=FixtureLLM()):
            response = self.api.post("/api/resume-versions/", {"text": RESUME}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["can_interview"])
        self.version = ResumeVersion.objects.get(pk=response.data["id"])

    def test_revised_profile_cannot_be_claimed_by_stale_question_worker(self):
        """Functionality: Reject an ABA race where question lease fields return to pending/None.
        Inputs: Old canonical projection and a newer timestamp with the same source/schema hash.
        Outputs: Pending current state, no model provider, no question rows and no lease owner.
        Logic: Advance the canonical revision exactly after read_profile, before the real CAS.
        Constraints: Provider factory is observed, not replaced by an alternative generation path.
        """

        def revise(version, **kwargs):
            """Return the observed profile after a simulated canonical republish.
            Inputs: Production version/candidate keyword arguments. Outputs: Old profile tuple.
            Logic: Move updated_at forward while source/schema/lease fields remain identical.
            Constraints: Mutation is confined to this test database; no provider is invoked.
            """
            profile = read_profile(version, **kwargs)
            ResumeVersion.objects.filter(pk=version.pk).update(
                updated_at=version.updated_at + timedelta(seconds=1)
            )
            return profile

        with (
            patch("interviews.question_preparation.read_profile", side_effect=revise),
            patch("interviews.question_preparation.OpenAILLM") as provider,
        ):
            result = prepare_questions(self.version.pk, self.owner.pk)
        provider.assert_not_called()
        self.assertEqual(result.question_preparation_status, "pending")
        self.assertIsNone(result.question_preparation_run_id)
        self.assertFalse(result.prepared_questions.exists())

    def test_start_rejects_questions_for_a_different_profile(self):
        """Functionality: Prevent profile A from receiving questions selected using profile B.
        Inputs: Owned binding, unchanged source text and two distinct canonical skill lists.
        Outputs: Explicit mismatch error; matching snapshots still load the empty reviewed bank.
        Logic: Republish the bound canonical cache between session projection and question load.
        Constraints: This targets the start boundary; established interview snapshots are immutable.
        """
        profile, _ = read_profile(self.version, candidate_id="session-snapshot")
        interview = AgentInterview.objects.create(
            owner=self.owner, resume_version=self.version, resume_text_snapshot=self.version.text
        )
        self.assertEqual(
            async_to_sync(load_bound_questions)(interview.pk, expected_profile=profile), []
        )
        changed = deepcopy(self.version.interview_profile)
        changed["document"]["skills"] = ["Revised skill"]
        ResumeVersion.objects.filter(pk=self.version.pk).update(
            interview_profile=changed, updated_at=timezone.now()
        )
        with self.assertRaisesRegex(ValueError, "profile changed"):
            async_to_sync(load_bound_questions)(interview.pk, expected_profile=profile)

    def test_preparation_progress_excludes_previous_profile_rows(self):
        """Functionality: Exclude obsolete cached content from the private progress endpoint.
        Inputs: Ready canonical source and an explicitly stale question fingerprint.
        Outputs: Empty current question list while the old row remains stored; obsolete canonical
        metadata also reports pending rather than ready. Logic: Exercise actual owner-scoped GETs.
        Constraints: No provider construction; synthetic content must never be returned or used.
        """
        PreparedResumeQuestion.objects.create(
            resume_version=self.version,
            project_id="old-project",
            topic_key="old-topic",
            kind="opening",
            status="ready",
            profile_hash="previous-profile",
            review_version=review_version(),
            content={"text": "Obsolete question"},
        )
        url = f"/api/resume-versions/{self.version.pk}/prepared-questions/"
        with patch("interviews.question_preparation.OpenAILLM") as provider:
            self.assertEqual(self.api.get(url).data["questions"], [])
            ResumeVersion.objects.filter(pk=self.version.pk).update(
                interview_profile_version="obsolete",
                question_preparation_status="ready",
                question_preparation_version=review_version(),
            )
            result = self.api.get(url)
        provider.assert_not_called()
        self.assertEqual(result.data["status"], "pending")
        self.assertEqual(result.data["questions"], [])
        self.assertEqual(self.version.prepared_questions.count(), 1)

    async def test_bound_interview_evaluation_and_history_keep_original_snapshot(self):
        """Functionality: Verify the whole bound lifecycle survives later resume changes.
        Inputs: Canonical upload, explicit new edition, changed cache, intro and technical answer.
        Outputs: Original profile/claims/binding in context and history; a verified queued report.
        Logic: Start from the version, change current edition/cache, answer, close the session,
        then run the independent worker and reload history through the owner-scoped API.
        Constraints: External models and output review are fixtures; no re-extraction is allowed
        in the interview or scoring worker, and numeric scoring rules remain unchanged.
        """
        llm = FixtureLLM()
        session = AgentSession(llm=llm)
        source = Start(
            type="start", request_id=uuid4(), resume_version_id=self.version.pk, max_questions=1
        )
        try:
            command = await resolve_resume_version(source, self.owner.pk)
            await reserve_request(
                session.interview_id,
                command,
                owner_id=self.owner.pk,
                resume_version_id=self.version.pk,
            )
            first = await session.start(command)
            await complete_fixture_request(session.interview_id, command.request_id, first)
            snapshot = session.profile.model_dump(mode="json")

            def edit():
                """Save/select a different edition using real APIs with one offline extraction.
                Inputs: Owned API client and original version. Outputs: New edition UUID.
                Logic: Confirm SQL skills, save and set current, then inspect its canonical fact.
                Constraints: Existing interviews retain their original version relationship.
                """
                with patch("interviews.resume_profile.OpenAILLM", return_value=FixtureLLM()):
                    result = self.api.post(
                        f"/api/resume-versions/{self.version.pk}/editions/",
                        {"units": {"projects": RESUME}, "slots": {"skills": ["SQL"]}},
                        format="json",
                    )
                self.assertEqual(result.status_code, 201)
                edition_id = result.data["id"]
                self.assertEqual(
                    self.api.post(f"/api/resume-versions/{edition_id}/current/").status_code, 200
                )
                edition = ResumeVersion.objects.get(pk=edition_id)
                self.assertEqual(read_profile(edition, candidate_id="edition")[0].skills, ["SQL"])
                return edition_id

            edition_id = await sync_to_async(edit)()
            changed = deepcopy(self.version.interview_profile)
            changed["document"]["skills"] = ["Revised cache"]
            await ResumeVersion.objects.filter(pk=self.version.pk).aupdate(
                interview_profile=changed, updated_at=timezone.now()
            )
            technical = await enter_main_flow(session, first, owner_id=self.owner.pk)
            answer = Answer(
                type="answer",
                request_id=uuid4(),
                question_id=technical["question"]["question_id"],
                answer_text=ANSWER,
            )
            await reserve_request(session.interview_id, answer, owner_id=self.owner.pk)
            finished = await session.answer(answer)
            await complete_fixture_request(session.interview_id, answer.request_id, finished)
            self.assertEqual(finished["type"], "finished")
            self.assertEqual(finished["result"]["evaluation_status"], "pending")
            self.assertEqual(finished["result"]["candidate_profile"], snapshot)
            await session.close_background()
            self.assertEqual(await AgentEvaluation.objects.acount(), 0)
            self.assertTrue(await process_one(llm_factory=lambda **_: llm))
            run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
            self.assertEqual(run.status, "completed")
            self.assertIsNotNone(await sync_to_async(verified_report)(run))
            context = await DjangoInterviewRepository(session.interview_id).get_interview_context(
                session.interview_id
            )
            self.assertEqual(context.candidate_profile.model_dump(mode="json"), snapshot)
            interview = await AgentInterview.objects.aget(pk=session.interview_id)
            self.assertEqual(interview.resume_version_id, self.version.pk)
            self.assertEqual(interview.resume_text_snapshot, RESUME)
            self.assertNotEqual(str(interview.resume_version_id), str(edition_id))
            detail = await sync_to_async(self.api.get)(
                f"/api/agent-interviews/{session.interview_id}/"
            )
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.data["candidate_profile"], snapshot)
            self.assertEqual(detail.data["evaluation_status"], "completed")
            self.assertEqual(detail.data["final_report"]["scoring_source"], "formal_evaluation")
            self.assertEqual(detail.data["final_report"]["overall_score"], 3.0)
            self.assertNotIn(ResumeExtraction, llm.calls)
        finally:
            await session.close_background()
            session.close()
