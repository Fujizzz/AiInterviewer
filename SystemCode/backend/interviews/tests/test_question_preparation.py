"""Responsibilities: Verify question preparation storage, scheduling and interview publication.
Implementation: Exercise the production preparation path with explicit isolated test
boundaries.
Related Modules: question_preparation, resume_profile and InterviewAgentService supply
production behavior.

Declaration Index:
- PreparationLLM: Provide deterministic model responses and record quality/admission calls
  without network access.
- PreparationLLM.__init__: Record calls and optionally fail full quality review for
  failure-path tests.
- PreparationLLM.__call__: Return fixture model output; semantic approval here verifies wiring
  only.
- QuestionPreparationTests: Verify upload dispatch, reviewed storage, owner isolation and
  production publication with fixture models.
- QuestionPreparationTests.setUp: Create an owned uploaded profile through the real API with
  the parser model replaced.
- QuestionPreparationTests.prepare: Run the real preparation service with fixture models and a
  deterministic RAG port.
- QuestionPreparationTests.bind: Create a real interview/version binding for owner and snapshot
  validation.
- QuestionPreparationTests.test_reviewed_rows_and_reuse: Verify every topic and one
  evidence-backed knowledge question are reviewed, persisted and reused.
- QuestionPreparationTests.test_failed_review_and_retry: Verify failed quality review cannot
  create usable content or invalidate a parsed resume.
- QuestionPreparationTests.test_missing_evidence_retry: Verify missing evidence is explicit and
  retry generates only missing knowledge content.
- QuestionPreparationTests.test_owned_current_rows: Reject foreign bindings, stale fingerprints
  and malformed or unreviewed rows without model calls.
- QuestionPreparationTests.test_owned_status_endpoint: Verify private progress reads require
  ownership and never construct a model provider.
- QuestionPreparationTests.test_backend_intro_and_cached_question: Verify the real gateway and
  repository publish an unscored introduction followed by cached content.
- QuestionPreparationTests.test_backend_intro_and_cached_question.run: Run backend handlers
  with fixture models and release owned session resources.
- QuestionPreparationTests.test_backend_intro_and_cached_question.run.command: Bind a real
  pending request, invoke its handler and publish with a disabled-review receipt.
- QuestionPreparationTests.test_backend_intro_and_cached_question.run.command.deliver: Persist
  the real publication receipt before returning the candidate-facing payload.
- QuestionPreparationTests.test_upload_dispatch_after_commit: Verify upload and explicit retry
  register work after commit with executor submission mocked.
- QuestionPreparationTests.test_processing_lease_recovery: Verify active runs suppress
  duplicate model calls while expired runs can be reclaimed.

Variable Index:
None
"""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from agents.policies.topic_selector import TopicSelector
from agents.question.prepared import review_version
from interviews.agent_models import AgentInterview
from interviews.question_preparation import (
    load_bound_questions,
    prepare_questions,
)
from interviews.resume_models import PreparedResumeQuestion, ResumeVersion
from interviews.resume_profile import read_profile
from shared.contracts import RetrievalSource
from tests.agent.integration.test_project_knowledge import evidence
from tests.agent.mocks import MockRAGAdapter

from .agent_fixtures import RESUME, FixtureLLM


class PreparationLLM(FixtureLLM):
    """Provide deterministic model responses and record quality/admission calls without network
    access.
    """

    def __init__(self, *, reject_quality=False):
        """Record calls and optionally fail full quality review for failure-path tests."""
        super().__init__()
        self.schemas = []
        self.reject_quality = reject_quality

    def __call__(self, prompt, data, schema):
        """Return fixture model output; semantic approval here verifies wiring only."""
        self.schemas.append(schema.__name__)
        if schema.__name__ == "PreparedUseDecision":
            return schema(usable=True, reason="Fixture: new unanswered goal")
        if schema.__name__ == "StandaloneQuestionDraft":
            return schema(
                text="What did you personally implement for "
                + data.get("locked_intent", data.get("question_plan"))["topic"] + "?"
            )
        if self.reject_quality and schema.__name__ in {
            "QuestionQualityReview", "CompactQualityReview"
        }:
            raise RuntimeError("Review unavailable")
        return super().__call__(prompt, data, schema)


@override_settings(QUESTION_PREPARATION_EXECUTION="disabled")
class QuestionPreparationTests(APITestCase):
    """Verify upload dispatch, reviewed storage, owner isolation and production publication with
    fixture models.
    """

    def setUp(self):
        """Create an owned uploaded profile through the real API with the parser model replaced."""
        self.owner = get_user_model().objects.create_user(username="prepared-owner")
        self.other = get_user_model().objects.create_user(username="prepared-other")
        self.client.force_authenticate(self.owner)
        with patch("interviews.resume_profile.OpenAILLM", return_value=FixtureLLM()):
            response = self.client.post("/api/resume-versions/", {"text": RESUME}, format="json")
        self.assertEqual(response.status_code, 201)
        self.version = ResumeVersion.objects.get(pk=response.data["id"])
        self.rag = MockRAGAdapter({RetrievalSource.TECHNICAL: [evidence()]})

    def prepare(self, **kwargs):
        """Run the real preparation service with fixture models and a deterministic RAG port."""
        return prepare_questions(
            self.version.pk,
            self.owner.pk,
            llm=kwargs.pop("llm", PreparationLLM()),
            rag=self.rag,
            **kwargs,
        )

    def bind(self, *, owner=None):
        """Create a real interview/version binding for owner and snapshot validation."""
        return AgentInterview.objects.create(
            id=uuid4(),
            owner=owner or self.owner,
            resume_version=self.version,
            resume_text_snapshot=self.version.text,
        )

    def test_reviewed_rows_and_reuse(self):
        """Verify every topic and one evidence-backed knowledge question are reviewed, persisted
        and reused.
        """
        llm = PreparationLLM()
        version = self.prepare(llm=llm)
        self.assertEqual(version.question_preparation_status, "ready")
        profile, _ = read_profile(version, candidate_id="synthetic")
        expected = sum(len(TopicSelector().candidates(p)) for p in profile.projects)
        rows = version.prepared_questions.all()
        self.assertEqual(rows.filter(kind="opening", status="ready").count(), expected)
        self.assertEqual(rows.filter(kind="knowledge", status="ready").count(), 1)
        self.assertGreaterEqual(llm.schemas.count("CompactQualityReview"), expected + 1)
        before = len(llm.schemas)
        self.prepare(llm=llm)
        self.assertEqual(len(llm.schemas), before)
        self.assertEqual(version.prepared_questions.count(), expected + 1)
        knowledge = rows.get(kind="knowledge").content
        self.assertEqual(knowledge["evidence"][0]["chunk_id"], evidence().chunk_id)
        self.assertNotIn("question_id", knowledge)
        self.assertNotIn("thread_id", knowledge)

    def test_previous_strict_ready_rows_can_still_be_loaded(self):
        """Changing to the relaxed gate does not discard an existing reviewed bank."""
        from agents.question.prepared import accepted_review_versions, review_version

        version = self.prepare()
        legacy = next(v for v in accepted_review_versions() if v != review_version())
        for row in version.prepared_questions.filter(status="ready"):
            row.review_version = legacy
            row.content = {**row.content, "review_version": legacy}
            row.save(update_fields=["review_version", "content"])
        loaded = async_to_sync(load_bound_questions)(self.bind().pk)
        self.assertEqual(len(loaded), version.prepared_questions.filter(status="ready").count())
        self.assertTrue(all(item.review_version == legacy for item in loaded))

    def test_failed_review_and_retry(self):
        """Verify failed quality review cannot create usable content or invalidate a parsed
        resume.
        """
        version = self.prepare(llm=PreparationLLM(reject_quality=True))
        self.assertTrue(version.can_interview)
        self.assertEqual(version.question_preparation_status, "failed")
        self.assertFalse(version.prepared_questions.filter(status="ready").exists())
        self.assertEqual(async_to_sync(load_bound_questions)(self.bind().pk), [])
        repaired = self.prepare()
        self.assertEqual(repaired.question_preparation_status, "ready")

    def test_missing_evidence_retry(self):
        """Verify missing evidence stays explicit and retry reuses ready opening questions."""
        self.rag = MockRAGAdapter({RetrievalSource.TECHNICAL: []})
        llm = PreparationLLM()
        version = self.prepare(llm=llm)
        self.assertEqual(version.question_preparation_status, "partial")
        knowledge = version.prepared_questions.get(kind="knowledge")
        self.assertEqual(knowledge.status, "unavailable")
        self.assertEqual(knowledge.error_code, "NO_USABLE_EVIDENCE")
        originals = list(
            version.prepared_questions.filter(kind="opening").values_list("id", flat=True)
        )
        self.rag = MockRAGAdapter({RetrievalSource.TECHNICAL: [evidence()]})
        llm.schemas.clear()
        version = self.prepare(llm=llm)
        self.assertEqual(llm.schemas.count("StandaloneQuestionDraft"), 1)
        self.assertEqual(version.question_preparation_status, "ready")
        self.assertEqual(
            originals,
            list(version.prepared_questions.filter(kind="opening").values_list("id", flat=True)),
        )

    def test_owned_current_rows(self):
        """Reject foreign bindings, stale fingerprints and malformed or unreviewed rows without
        model calls.
        """
        self.prepare()
        interview = self.bind()
        items = async_to_sync(load_bound_questions)(interview.pk)
        self.assertTrue(items)
        self.assertTrue(all(q.review_version == review_version() for q in items))
        with self.assertRaises(ValueError):
            async_to_sync(load_bound_questions)(self.bind(owner=self.other).pk)
        row = self.version.prepared_questions.filter(kind="opening").first()
        for changes in (
            {"review_version": "old"},
            {"profile_hash": "wrong"},
            {"content": {}},
            {"status": "failed"},
        ):
            with self.subTest(changes=changes):
                before = {
                    "review_version": row.review_version,
                    "profile_hash": row.profile_hash,
                    "content": row.content,
                    "status": row.status,
                }
                PreparedResumeQuestion.objects.filter(pk=row.pk).update(**changes)
                loaded = async_to_sync(load_bound_questions)(interview.pk)
                self.assertNotIn(row.content["cache_id"], [q.cache_id for q in loaded])
                PreparedResumeQuestion.objects.filter(pk=row.pk).update(**before)

    def test_owned_status_endpoint(self):
        """Verify private progress reads require ownership and never construct a model provider."""
        self.prepare()
        url = f"/api/resume-versions/{self.version.pk}/prepared-questions/"
        with patch("interviews.question_preparation.OpenAILLM") as factory:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["status"], "ready")
            self.assertTrue(response.data["questions"])
            factory.assert_not_called()
        ResumeVersion.objects.filter(pk=self.version.pk).update(question_preparation_version="old")
        self.assertEqual(self.client.get(url).data["status"], "pending")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)

    @override_settings(AI_SECURITY_ENABLED=False)
    def test_backend_intro_and_cached_question(self):
        """Verify the real gateway and repository publish an unscored introduction followed by
        cached content.
        """
        from interviews.agent_records import complete_request, reserve_request
        from interviews.agent_safety import InterviewIOGateway
        from interviews.agent_session import AgentSession
        from interviews.agent_socket import Answer, Start
        from interviews.api.resume_versions import resolve_resume_version

        self.prepare()
        llm = PreparationLLM()

        async def run():
            """Run backend handlers with fixture models and release owned session resources."""
            session = AgentSession(llm=llm)
            gateway = InterviewIOGateway(
                session.interview_id, owner_id=self.owner.pk, connection_id="prepared-test"
            )

            async def command(cmd):
                """Bind a real pending request, invoke its handler and publish with a
                disabled-review receipt.
                """
                source_id = getattr(cmd, "resume_version_id", None)
                resolved = await resolve_resume_version(cmd, self.owner.pk)
                await reserve_request(
                    session.interview_id,
                    resolved,
                    owner_id=self.owner.pk,
                    resume_version_id=source_id,
                )
                resolved = await gateway.bind_input(resolved)
                result = await getattr(session, cmd.type)(resolved)

                async def deliver(payload, receipt):
                    """Persist the real publication receipt before returning the candidate-facing
                    payload.
                    """
                    await complete_request(
                        session.interview_id,
                        cmd.request_id,
                        payload,
                        receipt=receipt,
                        owner_id=self.owner.pk,
                    )
                    return payload

                return await gateway.publish(result, deliver)

            try:
                first = await command(
                    Start(
                        type="start",
                        request_id=uuid4(),
                        resume_version_id=self.version.pk,
                        duration_minutes=10,
                    )
                )
                self.assertEqual(first["question"]["intent"], "SELF_INTRODUCTION")
                await command(
                    Answer(
                        type="answer",
                        request_id=uuid4(),
                        question_id=first["question"]["question_id"],
                        answer_text="I am Alex, a software engineer.",
                    )
                )
                context = await session.app.repository.get_interview_context(session.interview_id)
                self.assertEqual(len(context.used_prepared_question_ids), 1)
                saved = await session.app.repository.get_question(context.state.current_question_id)
                item = next(
                    q
                    for q in context.prepared_questions
                    if q.cache_id in context.used_prepared_question_ids
                )
                self.assertEqual(saved.text, item.text)
                self.assertNotIn("QuestionAgentDecision", llm.schemas)
                self.assertNotIn("QuestionQualityReview", llm.schemas)
                self.assertNotIn("CompactQualityReview", llm.schemas)
                self.assertIn("PreparedUseDecision", llm.schemas)
            finally:
                await session.close_background()
                await gateway.aclose()
                session.close()

        async_to_sync(run)()

    @override_settings(QUESTION_PREPARATION_EXECUTION="thread")
    def test_upload_dispatch_after_commit(self):
        """Verify upload and explicit retry register work after commit with executor submission
        mocked.
        """
        with (
            patch("interviews.resume_profile.OpenAILLM", return_value=FixtureLLM()),
            patch("interviews.question_preparation._executor.submit") as submit,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post("/api/resume-versions/", {"text": RESUME}, format="json")
            self.assertEqual(response.status_code, 201)
            submit.assert_not_called()
        submit.assert_called_once()
        with (
            patch("interviews.question_preparation._executor.submit") as retry,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(f"/api/resume-versions/{self.version.pk}/prepare-interview/")
        retry.assert_called_once()

    def test_processing_lease_recovery(self):
        """Verify active runs suppress duplicate model calls while expired runs can be reclaimed."""
        ResumeVersion.objects.filter(pk=self.version.pk).update(
            question_preparation_status="processing",
            question_preparation_run_id=uuid4(),
            question_preparation_started_at=timezone.now(),
        )
        llm = PreparationLLM()
        self.assertEqual(
            self.client.delete(f"/api/resume-versions/{self.version.pk}/").status_code, 409
        )
        self.prepare(llm=llm)
        self.assertEqual(llm.schemas, [])
        ResumeVersion.objects.filter(pk=self.version.pk).update(
            question_preparation_started_at=timezone.now() - timedelta(minutes=16)
        )
        self.assertEqual(self.prepare(llm=llm).question_preparation_status, "ready")
        self.assertTrue(llm.schemas)
