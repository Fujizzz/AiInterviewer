"""Responsibilities: Cover REST API lifecycle, validation, snapshot, transaction, concurrency, and
local-access contracts.

Implementation: Use Django's APIClient and isolated TestCase database to exercise the configured
endpoints; tests do not modify a development database.
Related Modules: interviews.models, interviews.api, and Django REST Framework test utilities.

Declaration Index:
- ApiTests:
  Regression suite of APIs under isolated database; prepare two questions per case to maintain
  reproducibility.
- ApiTests.setUp:
  Create APIClient and deterministic question bank; clear test database seed, do not operate on
  development database.
- ApiTests.create:
  Construct test session via public create endpoint, require HTTP 201, return response data.
- ApiTests.update:
  Construct single-question PATCH URL from session snapshot for reuse across state tests.
- ApiTests.test_health_and_question_crud:
  Verify health check reports actual database engine, pagination count, blank rejection, and
  question creation/deactivation.
- ApiTests.test_snapshot_and_defaults_survive_question_edit:
  After editing or deleting source question, query session to verify historical snapshot and fixed
  10/90 second durations unchanged.
- ApiTests.test_question_selection_order_validation:
  Verify explicit ID order preserved, reject empty, duplicate, missing, and deactivated questions.
- ApiTests.test_empty_bank_and_configuration_override_rejected:
  Verify read-only duration cannot be overridden, empty question bank creation fails, no session
  left behind.
- ApiTests.test_full_lifecycle_and_stale_version:
  Walk through start, submit, end; check old version rejected and remaining questions skipped.
- ApiTests.test_question_limit_and_explicit_subset:
  Verify default 100 questions can be created, 101 overall rejected, while explicit subset in large
  bank still usable.
- ApiTests.test_invalid_transition_rolls_back_version:
  Verify illegal transitions and concurrent answering rejected, failed requests do not consume
  version.
- ApiTests.test_payload_validation_and_cross_session_item:
  Cover missing version, negative duration, action field conflict, and cross-session single item;
  check transaction rollback.
- ApiTests.test_local_origin_restriction:
  Construct non-local and cross-origin requests, verify access policy returns 403 before business
  processing.
- ApiTests.test_database_rejects_duplicate_position:
  Directly construct duplicate session order, verify database unique constraint remains effective
  independent of API.
- ApiTests.test_demo_assets_and_missing_resource:
  Check memory resource responses against whitelist and missing session; reuse test client’s request
  cleanup to protect test transactions.

Variable Index:
None
"""

import uuid

from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from interviews.models import PracticeSession, Question, SessionQuestion


class ApiTests(TestCase):
    """Isolated database API regression suite; prepare two questions per case to maintain
    reproducibility.
    """

    def setUp(self):
        """Create APIClient and deterministic question bank; clear test database seeds, do not
        operate on development database.
        """
        self.client = APIClient()
        Question.objects.all().delete()
        self.q1 = Question.objects.create(text="First question", position=1)
        self.q2 = Question.objects.create(text="Second question", position=2)

    def create(self):
        """Construct test session via public creation endpoint, require HTTP 201 response, return
        response data.
        """
        response = self.client.post("/api/sessions/", {}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def update(self, session, index=0, **data):
        """Generate single-question PATCH URL from session snapshot for reuse across state tests.
        """
        return self.client.patch(
            f"/api/sessions/{session['id']}/items/{session['items'][index]['id']}/",
            data,
            format="json",
        )

    def test_health_and_question_crud(self):
        """Verify engine identifier and question bank write contract in isolated test database,
        compatible with SQLite and PostgreSQL.
        """
        self.assertEqual(
            self.client.get("/api/health/").data, {"status": "ok", "database": connection.vendor}
        )
        self.assertEqual(self.client.get("/api/questions/").data["count"], 2)
        invalid = self.client.post("/api/questions/", {"text": "   "}, format="json")
        self.assertEqual(invalid.status_code, 400)
        created = self.client.post("/api/questions/", {"text": "Third"}, format="json")
        self.assertEqual(created.status_code, 201)
        response = self.client.patch(
            f"/api/questions/{created.data['id']}/", {"enabled": False}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_snapshot_and_defaults_survive_question_edit(self):
        """After editing or deleting source questions, query session to verify historical snapshots
        and fixed 10/90 second duration remain unchanged.
        """
        session = self.create()
        self.assertEqual((session["prep_seconds"], session["answer_seconds"]), (10, 90))
        self.q1.text = "Edited"
        self.q1.save()
        self.q2.delete()
        response = self.client.get(f"/api/sessions/{session['id']}/")
        self.assertEqual(response.data["items"][0]["question_text"], "First question")
        self.assertEqual(response.data["items"][1]["question_text"], "Second question")
        self.assertIsNone(response.data["items"][1]["question_id"])

    def test_question_selection_order_validation(self):
        """Verify explicit ID ordering is preserved, reject empty, duplicate, missing, and disabled
        questions.
        """
        response = self.client.post(
            "/api/sessions/", {"question_ids": [str(self.q2.id), str(self.q1.id)]}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["items"][0]["question_text"], "Second question")
        for ids in [[], [str(self.q1.id)] * 2, [str(uuid.uuid4())]]:
            self.assertEqual(
                self.client.post(
                    "/api/sessions/", {"question_ids": ids}, format="json"
                ).status_code,
                400,
            )
        self.q1.enabled = False
        self.q1.save()
        self.assertEqual(
            self.client.post(
                "/api/sessions/", {"question_ids": [str(self.q1.id)]}, format="json"
            ).status_code,
            400,
        )

    def test_empty_bank_and_configuration_override_rejected(self):
        """Verify read-only duration cannot be overridden, empty question bank creation fails
        without leaving session.
        """
        self.assertEqual(
            self.client.post("/api/sessions/", {"prep_seconds": 1}, format="json").status_code, 400
        )
        Question.objects.all().delete()
        self.assertEqual(self.client.post("/api/sessions/", {}, format="json").status_code, 400)
        self.assertFalse(PracticeSession.objects.exists())

    def test_full_lifecycle_and_stale_version(self):
        """Walk through start, submit, and end; check old version rejected and remaining questions
        skipped.
        """
        session = self.create()
        started = self.update(session, action="start", version=1)
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.data["version"], 2)
        self.assertEqual(self.update(session, action="complete", version=1).status_code, 409)
        completed = self.update(
            session, action="complete", version=2, answer_text="My answer", duration_ms=90000
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.data["items"][0]["duration_ms"], 90000)
        response = self.client.post(
            f"/api/sessions/{session['id']}/finish/", {"version": 3}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["items"][1]["status"], "skipped")
        self.assertEqual(self.update(session, index=1, action="start", version=4).status_code, 409)

    def test_question_limit_and_explicit_subset(self):
        """Verify default 100 questions can be created, 101 questions rejected as a whole, while
        explicit subsets in large banks remain usable.
        """
        Question.objects.bulk_create(
            [Question(text=f"Question {index}", position=index) for index in range(3, 101)]
        )
        self.assertEqual(len(self.create()["items"]), 100)
        Question.objects.create(text="Overflow question", position=101)
        self.assertEqual(self.client.post("/api/sessions/", {}, format="json").status_code, 400)
        self.assertEqual(PracticeSession.objects.count(), 1)
        response = self.client.post(
            "/api/sessions/", {"question_ids": [str(self.q2.id)]}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data["items"]), 1)

    def test_invalid_transition_rolls_back_version(self):
        """Verify illegal transitions and concurrent answering are rejected, failed requests do not
        consume version.
        """
        session = self.create()
        self.assertEqual(self.update(session, action="complete", version=1).status_code, 409)
        self.assertEqual(PracticeSession.objects.get(pk=session["id"]).version, 1)
        self.assertEqual(self.update(session, action="start", version=1).status_code, 200)
        self.assertEqual(self.update(session, index=1, action="start", version=2).status_code, 409)
        self.assertEqual(PracticeSession.objects.get(pk=session["id"]).version, 2)

    def test_payload_validation_and_cross_session_item(self):
        """Cover missing version, negative duration, action field conflicts, and cross-session
        single-question cases; check transaction rollback.
        """
        session, other = self.create(), self.create()
        for data in [
            {"action": "start"},
            {"action": "complete", "version": 1, "duration_ms": -1},
            {"action": "start", "version": 1, "answer_text": "unexpected"},
        ]:
            self.assertEqual(self.update(session, **data).status_code, 400)
        response = self.client.patch(
            f"/api/sessions/{session['id']}/items/{other['items'][0]['id']}/",
            {"action": "start", "version": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(PracticeSession.objects.get(pk=session["id"]).version, 1)

    def test_local_origin_restriction(self):
        """Construct non-local and cross-origin requests, verify access policy returns 403 before
        business processing.
        """
        self.assertEqual(self.client.get("/api/health/", REMOTE_ADDR="192.0.2.1").status_code, 403)
        self.assertEqual(
            self.client.get("/api/health/", HTTP_ORIGIN="https://example.com").status_code, 403
        )

    def test_database_rejects_duplicate_position(self):
        """Directly construct duplicate session order, verify database unique constraint remains
        effective independent of API.
        """
        session = self.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SessionQuestion.objects.create(
                session_id=session["id"], question_text="Duplicate", position=1
            )

    def test_demo_assets_and_missing_resource(self):
        """Check resource whitelist and missing session responses to prevent test page from exposing
        arbitrary files.

        demo returns HttpResponse already loaded into memory, no unclosed file handles; client
        responsible for request end.
        Do not call response.close() extra, avoid double request_finished signal closing PostgreSQL
        test transaction.
        """
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        for name in ["app.js", "view.js", "media.js", "stream-client.js"]:
            response = self.client.get(f"/stream-demo/{name}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/stream-demo/settings.py").status_code, 404)
        self.assertEqual(self.client.get(f"/api/sessions/{uuid.uuid4()}/").status_code, 404)
        self.assertEqual(self.client.get("/api/sessions/not-a-uuid/").status_code, 404)
