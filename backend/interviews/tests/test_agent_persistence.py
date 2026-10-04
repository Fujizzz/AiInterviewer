"""Responsibilities: Validate Agent persistence, database constraints, failure atomicity, and local
history interface.

Implementation: Use real Agent with explicit FixtureLLM and isolated database; inject failures at
transaction end to verify complete rollback.
Related Modules: Covers agent_records, agent_repository, agent_socket, and read-only agent_history.

Declaration Index:
- PersistenceTests:
  Validate observable database state after commit and cleanup using TransactionTestCase.
- PersistenceTests.test_custom_project_and_topic_budgets_are_persisted:
  Verify duration, plan, timing start point, and safety ceiling are persisted together with context.
- PersistenceTests.test_start_rejects_conflicting_topic_options:
  Reject simultaneous presence of old and new topic ceilings.
- PersistenceTests.start_session:
  Reserve start request and run real Agent initialization, return session and first question.
- PersistenceTests.answer_command:
  Construct unique answer command for current question without performing I/O.
- PersistenceTests.test_saved_context_can_be_loaded_by_new_repository:
  New adapter reads same context; out-of-bound read fails.
- PersistenceTests.test_answer_evidence_and_report_are_persisted:
  Successful round saves evidence, request-response, and final report.
- PersistenceTests.test_failed_commit_rolls_back_state_question_log_and_evidence:
  Transaction-end failure must not leave partial round state.
- PersistenceTests.test_version_conflict_does_not_publish_another_turn:
  Same old version cannot publish another action.
- PersistenceTests.test_duplicate_request_across_connections_never_calls_model:
  Duplicate requests across connections are deduplicated by database, no orphaned interviews
  created.
- PersistenceTests.test_pending_answer_survives_evaluation_failure:
  Evaluation failure preserves pending answer; no score record created.
- PersistenceTests.test_connection_interrupt_preserves_success_and_marks_pending:
  Disconnection only terminates incomplete requests.
- PersistenceTests.test_history_is_read_only_scoped_and_not_cached:
  Paginated list excludes body content; cross-interview query fails.
- PersistenceTests.test_storage_failure_prevents_model_execution:
  Reservation failure prevents sending started signal and calling model.
- PersistenceTests.test_response_is_saved_before_transport_delivery:
  Failed delivery cannot undo successfully saved response.
- PersistenceTests.test_response_is_saved_before_transport_delivery.fail_delivery:
  Simulate disconnection only when question has been generated and sent.
- PersistenceTests.test_schema_rejects_partial_evidence_and_invalid_status:
  Database directly rejects unversioned evidence and invalid status.
- PersistenceTests.test_completed_report_survives_cleanup:
  Cleanup after completion does not change successful report to interrupted.
- PersistenceTests.test_one_pending_request_per_interview:
  Different UUIDs cannot bypass one-request-per-interview constraint in database.
- PersistenceTests.test_foreign_request_cannot_receive_an_answer:
  Accepted request cannot be used by another interview to save answer.
- PersistenceTests.test_foreign_question_collision_rolls_back_commit:
  Another interview’s question ID cannot be overwritten; version occupancy triggers rollback
  simultaneously.

- PersistenceTests.test_review_progress_snapshots: Retain internal plan/progress records while
  exposing the completed report, public time state, and own feedback without orchestration fields.
- PersistenceTests.test_review_legacy_and_tampered_snapshots:
  Old records missing fields do not load internal context; tampered output remains hidden.
- PersistenceTests.test_review_failed_answer:
  Failed request retains own answer; unapproved evaluation remains empty.

Variable Index:
None
Constraints:
Tests do not invoke actual models, do not prove supplier cancellation, real load performance, or
multi-user authentication implementation.
"""

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.db import IntegrityError, OperationalError, transaction
from django.test import TransactionTestCase

from agents.domain.errors import InvalidAgentState, StateConflictError
from agents.domain.models import AgentDecisionLog, CommitTurnRequest
from interviews.agent_models import (
    AgentAnswer,
    AgentInterview,
    AgentQuestion,
    AgentRequest,
    AgentTurn,
)
from interviews.agent_records import (
    PendingRequest,
    fail_request,
    interrupt_interview,
    reserve_request,
)
from interviews.agent_repository import DjangoInterviewRepository
from interviews.agent_safety import verified_response
from interviews.agent_session import AgentSession
from interviews.agent_socket import Answer, Start, agent_socket
from shared.contracts import CandidateAnswer, InterviewAction

from .agent_fixtures import ANSWER, RESUME, FixtureLLM, SafetyTestMixin, complete_fixture_request
from .test_agent_progress import connect, disconnect, read, send_command


class PersistenceTests(SafetyTestMixin, TransactionTestCase):
    """Use TransactionTestCase to validate observable database state after commit and cleanup."""

    async def test_custom_project_and_topic_budgets_are_persisted(self):
        """Verify duration, plan version, actual timing start point, and question count safety
        ceiling are persisted alongside context.
        """
        session = AgentSession(llm=FixtureLLM())
        command = Start(
            request_id=uuid4(),
            type="start",
            resume_text=RESUME,
            max_questions=8,
            duration_minutes=15,
            max_questions_per_project=3,
            max_questions_per_topic=2,
        )
        await reserve_request(session.interview_id, command)
        result = await session.start(command)
        await complete_fixture_request(session.interview_id, command.request_id, result)
        context = await DjangoInterviewRepository(session.interview_id).get_interview_context(
            session.interview_id
        )
        self.assertEqual(context.plan.max_questions_per_project, 3)
        self.assertEqual(context.plan.max_questions_per_topic, 2)
        self.assertEqual(context.plan.max_questions, 8)
        self.assertEqual(context.plan.duration_seconds, 900)
        self.assertEqual(context.plan.version, 1)
        self.assertIsNotNone(context.state.clock_started_at)
        self.assertTrue(context.plan_history)
        self.assertNotIn("max_consecutive_probes", context.plan.model_dump())

    def test_start_rejects_conflicting_topic_options(self):
        """New and old topic ceilings are mutually exclusive; rejection occurs before model
        invocation.
        """
        with self.assertRaises(ValueError):
            Start(
                request_id=uuid4(),
                type="start",
                resume_text=RESUME,
                max_questions_per_topic=2,
                max_follow_up_per_topic=2,
            )

    async def start_session(self, count=2):
        """Reserve start request and run real Agent initialization, return session and first
        question; use only offline model.
        """
        session = AgentSession(llm=FixtureLLM())
        command = Start(request_id=uuid4(), type="start", resume_text=RESUME, max_questions=count)
        await reserve_request(session.interview_id, command)
        result = await session.start(command)
        await complete_fixture_request(session.interview_id, command.request_id, result)
        return session, result

    def answer_command(self, first):
        """Construct unique answer command for current question without performing I/O; input is
        server-side question response.
        """
        return Answer(
            request_id=uuid4(),
            type="answer",
            answer_text=ANSWER,
            question_id=first["question"]["question_id"],
        )

    async def test_saved_context_can_be_loaded_by_new_repository(self):
        """New adapter reads same context, fails on out-of-bound read; no original session cache
        required.
        """
        session, first = await self.start_session()
        fresh = DjangoInterviewRepository(session.interview_id)
        context = await fresh.get_interview_context(session.interview_id)
        self.assertEqual(
            context, await session.app.repository.get_interview_context(session.interview_id)
        )
        self.assertEqual(
            (await fresh.get_question(first["question"]["question_id"])).text,
            first["question"]["text"],
        )
        other, _ = await self.start_session()
        with self.assertRaises(InvalidAgentState):
            await fresh.get_interview_context(other.interview_id)
        with self.assertRaises(InvalidAgentState):
            await other.app.repository.get_question(first["question"]["question_id"])

    async def test_answer_evidence_and_report_are_persisted(self):
        """Successful round saves evaluation, request-response, and final report, maintaining
        original MVP budget and numeric scoring.
        """
        session, first = await self.start_session(count=1)
        command = self.answer_command(first)
        await reserve_request(session.interview_id, command)
        result = await session.answer(command)
        await complete_fixture_request(session.interview_id, command.request_id, result)
        answer = await AgentAnswer.objects.aget(request_id=command.request_id)
        record = await AgentInterview.objects.aget(id=session.interview_id)
        saved_request = await AgentRequest.objects.aget(id=command.request_id)
        turn = await AgentTurn.objects.aget(feedback_request_id=command.request_id)
        self.assertEqual(record.status, "completed")
        self.assertEqual(verified_response(saved_request), result)
        self.assertEqual(answer.committed_state_version, turn.state_version)
        self.assertEqual(answer.evaluation["request_id"], str(command.request_id))
        self.assertLess(result["result"]["interview_state"]["elapsed_seconds"], 120)
        self.assertAlmostEqual(result["result"]["final_report"]["overall_score"], 3.0)
        fresh = DjangoInterviewRepository(session.interview_id)
        context = await fresh.get_interview_context(session.interview_id)
        self.assertEqual(len(context.question_history), 1)
        entry = context.question_history[0]
        self.assertEqual(entry.question.question_id, first["question"]["question_id"])
        self.assertEqual(entry.answer.answer_id, str(answer.id))
        self.assertEqual(entry.answer.text, ANSWER)
        self.assertEqual(entry.feedback.request_id, str(command.request_id))

    async def test_failed_commit_rolls_back_state_question_log_and_evidence(self):
        """Transaction-end failure must not leave partial round state; original answer preserved but
        evaluation and display history not published prematurely.
        """
        session, first = await self.start_session()
        repository = session.app.repository
        before = await repository.get_interview_context(session.interview_id)
        question_count = await AgentQuestion.objects.acount()
        turn_count = await AgentTurn.objects.acount()
        command = self.answer_command(first)
        await reserve_request(session.interview_id, command)
        with patch(
            "interviews.agent_repository.AgentTurn.objects.create",
            side_effect=IntegrityError("injected"),
        ):
            with self.assertRaises(StateConflictError):
                await session.answer(command)
        self.assertEqual(await repository.get_interview_context(session.interview_id), before)
        self.assertEqual(await AgentQuestion.objects.acount(), question_count)
        self.assertEqual(await AgentTurn.objects.acount(), turn_count)
        answer = await AgentAnswer.objects.aget(request_id=command.request_id)
        self.assertIsNone(answer.evaluation)
        self.assertIsNone(answer.committed_state_version)
        self.assertEqual(session.history, [])

    async def test_version_conflict_does_not_publish_another_turn(self):
        """Same old version cannot publish another action; reuse existing questions in test, no
        model generation requested.
        """
        session, _ = await self.start_session()
        repository = session.app.repository
        context = await repository.get_interview_context(session.interview_id)
        last = await AgentTurn.objects.filter(interview_id=session.interview_id).alast()
        action = InterviewAction.model_validate(last.action)
        log = AgentDecisionLog.model_validate(last.decision_log).model_copy(
            update={
                "state_version": context.state.state_version + 1,
            }
        )
        request = CommitTurnRequest(
            interview_id=session.interview_id,
            expected_state_version=context.state.state_version,
            new_state=context.state,
            question=action.question,
            decision_log=log,
            resulting_action=action,
        )
        await repository.commit_turn(request)
        with self.assertRaises(StateConflictError):
            await repository.commit_turn(request)
        saved = await repository.get_interview_context(session.interview_id)
        self.assertEqual(saved.state.state_version, context.state.state_version + 1)

    async def test_duplicate_request_across_connections_never_calls_model(self):
        """Duplicate requests across connections are deduplicated by database, no orphaned
        interviews created; response does not leak original session content or ID.
        """
        fixture = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=fixture):
            first = await connect()
            rid = await send_command(first, "start", resume_text=RESUME, progress_events=False)
            self.assertEqual((await read(first))["type"], "started")
            self.assertEqual((await read(first))["type"], "question")
            await disconnect(first)
            calls = len(fixture.calls)
            second = await connect()
            await send_command(
                second, "start", request_id=rid, resume_text="changed", progress_events=False
            )
            reply = await read(second)
            self.assertEqual(reply["code"], "duplicate_request")
            self.assertNotIn("interview_id", reply)
            self.assertEqual(len(fixture.calls), calls)
            self.assertEqual(await AgentInterview.objects.acount(), 1)
            await disconnect(second)

    async def test_pending_answer_survives_evaluation_failure(self):
        """Evaluation failure preserves pending answer, no score record created; error storage does
        not include original exception text.
        """
        session, first = await self.start_session()
        command = self.answer_command(first)
        await reserve_request(session.interview_id, command)
        with patch.object(
            session.app.evaluation, "evaluate", side_effect=RuntimeError("private-value")
        ):
            with self.assertRaises(RuntimeError):
                await session.answer(command)
        await fail_request(session.interview_id, command.request_id)
        answer = await AgentAnswer.objects.aget(request_id=command.request_id)
        request = await AgentRequest.objects.aget(id=command.request_id)
        self.assertEqual(answer.text, ANSWER)
        self.assertIsNone(answer.evaluation)
        self.assertEqual(request.error_code, "agent_failed")
        self.assertIsNone(request.response)
        self.assertEqual(
            (await AgentInterview.objects.aget(id=session.interview_id)).status, "failed"
        )

    async def test_connection_interrupt_preserves_success_and_marks_pending(self):
        """Disconnection only terminates incomplete requests; successful first question and existing
        context remain readable, no further state submission allowed.
        """
        session, first = await self.start_session()
        command = self.answer_command(first)
        await reserve_request(session.interview_id, command)
        await interrupt_interview(session.interview_id)
        request = await AgentRequest.objects.aget(id=command.request_id)
        self.assertEqual(request.status, "interrupted")
        self.assertEqual(await AgentRequest.objects.filter(status="succeeded").acount(), 1)
        self.assertEqual(
            (await AgentInterview.objects.aget(id=session.interview_id)).status, "interrupted"
        )
        self.assertIsNotNone(
            await session.app.repository.get_interview_context(session.interview_id)
        )

    async def test_history_is_read_only_scoped_and_not_cached(self):
        """Paginated list excludes body content; cross-interview query fails; local policy still
        rejects remote and cross-origin requests.
        """
        session, first = await self.start_session()
        other, _ = await self.start_session()
        request = await AgentRequest.objects.filter(interview_id=other.interview_id).afirst()
        base = f"/api/agent-interviews/{session.interview_id}/"
        headers = {"origin": "http://testserver"}
        listing = await self.async_client.get("/api/agent-interviews/", headers=headers)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["count"], 2)
        self.assertNotIn("context", listing.json()["results"][0])
        self.assertEqual(listing["Cache-Control"], "no-store, private")
        detail = await self.async_client.get(base, headers=headers)
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.json()["can_resume"])
        self.assertEqual(detail.json()["questions"][0]["question"], first["question"])
        self.assertEqual(
            (
                await self.async_client.get(base + f"requests/{request.id}/", headers=headers)
            ).status_code,
            404,
        )
        self.assertEqual((await self.async_client.post(base, {}, headers=headers)).status_code, 405)
        self.assertEqual(
            (
                await self.async_client.get(base, headers={"origin": "https://foreign.invalid"})
            ).status_code,
            403,
        )
        self.assertEqual(
            (await self.async_client.get(base, headers={"host": "foreign.invalid"})).status_code,
            400,
        )

    async def test_storage_failure_prevents_model_execution(self):
        """Reservation failure prevents sending started signal and calling model; do not use memory
        as database substitute.
        """
        fixture = FixtureLLM()
        with (
            patch("interviews.agent_session.BackendLLM", return_value=fixture),
            patch(
                "interviews.agent_socket.reserve_request",
                side_effect=OperationalError("private-database"),
            ),
        ):
            comm = await connect()
            await send_command(comm, "start", resume_text=RESUME)
            reply = await read(comm)
            self.assertEqual(reply["code"], "storage_unavailable")
            self.assertNotIn("private", json.dumps(reply))
            self.assertEqual((await comm.receive_output())["code"], 1011)
            await comm.wait()
        self.assertEqual(fixture.calls, [])

    async def test_response_is_saved_before_transport_delivery(self):
        """Failed delivery cannot undo successfully saved response, nor cause repeated model
        execution.
        """
        queue = []

        async def fail_delivery(message):
            """Simulate disconnection only when question has been generated and sent; other events
            are validated by test, no database behavior simulated.
            """
            if message["type"] == "websocket.send":
                body = json.loads(message["text"])
                if body["type"] == "question":
                    raise OSError("transport closed")
            queue.append(message)

        scope = {
            "type": "websocket",
            "scheme": "ws",
            "client": ("127.0.0.1", 1),
            "headers": [(b"host", b"localhost")],
        }
        # Directly drive ASGI receive/send boundary; send exceptions occur after real database
        # response commit.
        incoming = asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})
        command = Start(request_id=uuid4(), type="start", resume_text=RESUME)
        await incoming.put({"type": "websocket.receive", "text": command.model_dump_json()})
        with patch("interviews.agent_session.BackendLLM", return_value=FixtureLLM()):
            with self.assertRaises(OSError):
                await agent_socket(scope, incoming.get, fail_delivery)
        request = await AgentRequest.objects.aget(id=command.request_id)
        self.assertEqual(request.status, "succeeded")
        self.assertEqual(request.response["type"], "question")
        self.assertEqual(
            (await AgentInterview.objects.aget(id=request.interview_id)).status, "interrupted"
        )

    def test_schema_rejects_partial_evidence_and_invalid_status(self):
        """Database directly rejects unversioned evidence and invalid status; test bypasses
        repository to verify real CHECK constraints.
        """
        session, first = async_to_sync(self.start_session)()
        command = self.answer_command(first)
        async_to_sync(reserve_request)(session.interview_id, command)
        question = AgentQuestion.objects.get(id=first["question"]["question_id"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentAnswer.objects.create(
                question=question,
                request_id=command.request_id,
                text=ANSWER,
                evaluation={"rubric_level": 3},
                committed_state_version=None,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentInterview.objects.filter(id=session.interview_id).update(status="unknown")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRequest.objects.filter(id=command.request_id).update(status="succeeded")

    async def test_completed_report_survives_cleanup(self):
        """Cleanup after completion does not change successful report to interrupted; historical
        details are readable and match result-sent report.
        """
        session, first = await self.start_session(count=1)
        command = self.answer_command(first)
        await reserve_request(session.interview_id, command)
        result = await session.answer(command)
        await complete_fixture_request(session.interview_id, command.request_id, result)
        await interrupt_interview(session.interview_id)
        response = await self.async_client.get(f"/api/agent-interviews/{session.interview_id}/")
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(response.json()["final_report"], result["result"]["final_report"])

    async def test_one_pending_request_per_interview(self):
        """Different UUIDs cannot bypass one-request-per-interview constraint in database; second
        reservation does not create additional record.
        """
        session, first = await self.start_session()
        one = self.answer_command(first)
        two = self.answer_command(first)
        await reserve_request(session.interview_id, one)
        with self.assertRaises(PendingRequest):
            await reserve_request(session.interview_id, two)
        self.assertFalse(await AgentRequest.objects.filter(id=two.request_id).aexists())
        self.assertEqual(await AgentRequest.objects.filter(status="running").acount(), 1)

    async def test_foreign_request_cannot_receive_an_answer(self):
        """Accepted request cannot be used by another interview to save answer; no cross-interview
        answer record after failure.
        """
        session, first = await self.start_session()
        other, other_first = await self.start_session()
        command = self.answer_command(other_first)
        await reserve_request(other.interview_id, command)
        answer = CandidateAnswer(
            interview_id=session.interview_id,
            question_id=first["question"]["question_id"],
            answer_id=str(uuid4()),
            text=ANSWER,
        )
        with self.assertRaises(AgentRequest.DoesNotExist):
            await session.app.repository.accept_answer(command.request_id, answer)
        self.assertEqual(await AgentAnswer.objects.acount(), 0)

    async def test_foreign_question_collision_rolls_back_commit(self):
        """Another interview’s question ID cannot be overwritten; version occupancy triggers
        rollback simultaneously; no model invoked to generate extra questions.
        """
        session, _ = await self.start_session()
        other, other_first = await self.start_session()
        context = await session.app.repository.get_interview_context(session.interview_id)
        last = await AgentTurn.objects.filter(interview_id=session.interview_id).alast()
        action = InterviewAction.model_validate(last.action)
        foreign = await other.app.repository.get_question(other_first["question"]["question_id"])
        action.question = foreign
        log = AgentDecisionLog.model_validate(last.decision_log).model_copy(
            update={
                "state_version": context.state.state_version + 1,
            }
        )
        request = CommitTurnRequest(
            interview_id=session.interview_id,
            expected_state_version=context.state.state_version,
            new_state=context.state,
            question=foreign,
            decision_log=log,
            resulting_action=action,
        )
        with self.assertRaises(StateConflictError):
            await session.app.repository.commit_turn(request)
        self.assertEqual(
            await session.app.repository.get_interview_context(session.interview_id), context
        )
        self.assertEqual(await other.app.repository.get_question(foreign.question_id), foreign)

    async def test_review_progress_snapshots(self):
        """Offline model and real database: active progress remains available until completion.
        Completion keeps internal plans, progress and logs intact, while final and history responses
        expose only public time state, own conversation and feedback, and the checked report.
        """
        session, first = await self.start_session(count=1)
        for key in ("interview_plan", "plan_history", "topic_progress", "decision_logs"):
            self.assertTrue(first[key])
        response = await self.async_client.get(f"/api/agent-interviews/{session.interview_id}/")
        initial = response.json()
        self.assertEqual(initial["schema_version"], 2)
        for key in ("interview_plan", "plan_history", "topic_progress", "decision_logs"):
            self.assertEqual(initial[key], first[key])
        command = self.answer_command(first)
        await reserve_request(session.interview_id, command)
        final = await session.answer(command)
        await complete_fixture_request(session.interview_id, command.request_id, final)
        context = await session.app.repository.get_interview_context(session.interview_id)
        self.assertEqual(context.plan.interview_id, session.interview_id)
        self.assertEqual(context.plan.max_questions, 1)
        self.assertTrue(context.plan.topics)
        self.assertTrue(context.plan_history)
        self.assertTrue(context.topic_progress)
        logs = await session.app.repository.decision_logs_for(session.interview_id)
        self.assertGreaterEqual(len(logs), len(first["decision_logs"]))
        record = await AgentInterview.objects.aget(id=session.interview_id)
        self.assertEqual(record.context["plan"], context.plan.model_dump(mode="json"))
        self.assertEqual(
            record.context["plan_history"],
            [revision.model_dump(mode="json") for revision in context.plan_history],
        )
        self.assertEqual(
            record.context["topic_progress"],
            {
                key: progress.model_dump(mode="json")
                for key, progress in context.topic_progress.items()
            },
        )
        detail = (
            await self.async_client.get(f"/api/agent-interviews/{session.interview_id}/")
        ).json()
        for key in ("interview_plan", "plan_history", "topic_progress", "decision_logs"):
            self.assertNotIn(key, final["result"])
            self.assertIsNone(detail[key])
        self.assertNotIn("topics", final["result"])
        public_state_fields = {
            "contract_version",
            "interview_id",
            "state_version",
            "status",
            "stage",
            "question_index",
            "remaining_seconds",
            "elapsed_seconds",
        }
        self.assertEqual(set(final["result"]["interview_state"]), public_state_fields)
        self.assertEqual(
            final["result"]["interview_state"],
            context.state.model_dump(mode="json", include=public_state_fields),
        )
        self.assertEqual(detail["interview_state"], final["result"]["interview_state"])
        self.assertEqual(detail["final_report"], final["result"]["final_report"])
        public_history = final["result"]["question_history"][0]
        self.assertNotIn("thread_id", public_history)
        self.assertNotIn("project_id", public_history)
        for key in ("new_information", "thread_complete", "answer_scope", "contradiction_evidence"):
            self.assertIn(key, session.history[0]["evaluation"]["analysis"])
            self.assertNotIn(key, public_history["evaluation"]["analysis"])
        self.assertEqual(
            public_history["evaluation"]["dimensions"],
            session.history[0]["evaluation"]["dimensions"],
        )
        turn = detail["questions"][0]
        self.assertEqual(turn["question"]["difficulty"], first["question"]["difficulty"])
        self.assertEqual(turn["answer"]["text"], ANSWER)
        self.assertEqual(
            turn["answer"]["evaluation"], final["result"]["question_history"][0]["evaluation"]
        )
        self.assertTrue(turn["created_at"])
        self.assertTrue(turn["answer"]["created_at"])
        self.assertEqual(detail["request_issues"], [])

    async def test_review_legacy_and_tampered_snapshots(self):
        """Explicitly reconstruct legacy approval credentials; missing fields are empty; then tamper
        with stored package, mismatched summary renders all content invisible.
        """
        from interviews.agent_safety import make_output_receipt

        session, first = await self.start_session()
        request = await AgentRequest.objects.filter(interview_id=session.interview_id).afirst()
        legacy = {
            key: value
            for key, value in first.items()
            if key not in {"plan_history", "topic_progress", "decision_logs"}
        }
        receipt = make_output_receipt(legacy, request.id, 2)
        await AgentRequest.objects.filter(id=request.id).aupdate(
            response={**legacy, "_security": receipt}
        )
        url = f"/api/agent-interviews/{session.interview_id}/"
        detail = (await self.async_client.get(url)).json()
        self.assertTrue(detail["security_output_available"])
        self.assertIsNone(detail["plan_history"])
        self.assertIsNone(detail["decision_logs"])
        self.assertIsNone(detail["topic_progress"])
        await AgentRequest.objects.filter(id=request.id).aupdate(
            response={**legacy, "topic_progress": {"private": "hidden"}, "_security": receipt}
        )
        detail = (await self.async_client.get(url)).json()
        self.assertFalse(detail["security_output_available"])
        self.assertIsNone(detail["interview_plan"])
        self.assertIsNone(detail["decision_logs"])
        self.assertEqual(detail["questions"], [])

    async def test_review_failed_answer(self):
        """Real repository accepts own answer then simulates evaluation request failure; review
        retains answer but does not use internally unapproved score.
        """
        session, first = await self.start_session()
        command = self.answer_command(first)
        await reserve_request(session.interview_id, command)
        answer = CandidateAnswer(
            interview_id=session.interview_id,
            question_id=first["question"]["question_id"],
            answer_id=str(uuid4()),
            text=ANSWER,
        )
        await session.app.repository.accept_answer(command.request_id, answer)
        await fail_request(session.interview_id, command.request_id, error_code="agent_failed")
        detail = (
            await self.async_client.get(f"/api/agent-interviews/{session.interview_id}/")
        ).json()
        self.assertEqual(detail["questions"][0]["answer"]["text"], ANSWER)
        self.assertIsNone(detail["questions"][0]["answer"]["evaluation"])
        self.assertEqual(detail["request_issues"][0]["error_code"], "agent_failed")
        self.assertIsNone(detail["final_report"])
