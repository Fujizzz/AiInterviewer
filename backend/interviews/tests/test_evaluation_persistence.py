"""Responsibilities: Verify scoring persistence and compatibility with automatic interview ending.
Implementation: Exercise real SQLite transactions with offline model/safety fixtures.
Related Modules: agent_session, agent_repository, agent_records and evaluation replay.
Declaration Index:
- EvaluationPersistenceTests: Cover atomic receipts, failure recovery and interview lifecycle.
- EvaluationPersistenceTests.start_session: Start a persisted fixture interview.
- EvaluationPersistenceTests.command: Build an answer for the current question.
- EvaluationPersistenceTests.test_complete_ledger_replays_after_repository_restart_and_is_private:
  Replay unchanged receipts after restart and verify private payloads stay out of public state.
- EvaluationPersistenceTests.test_late_transaction_failure_rolls_back_ledger_and_preserves_answer:
  Inject a late SQL failure and verify no partial scoring commit.
- EvaluationPersistenceTests.test_failed_shadow_persists_no_snapshot_and_survives_restart:
  Retain failed scoring and block later publication across repository restart.
- EvaluationPersistenceTests.test_stale_base_version_is_rejected_even_when_turn_cas_is_fresh:
  Reject a scoring receipt based on an older state.
- EvaluationPersistenceTests.test_stale_base_version_is_rejected_even_when_turn_cas_is_fresh.stale:
  Change only the receipt base version before the real commit.
- EvaluationPersistenceTests.test_duplicate_feedback_does_not_append_or_recompute:
  Replay feedback idempotently without appending another receipt.
- EvaluationPersistenceTests.test_omitted_receipt_cannot_commit_a_partial_turn:
  Reject a missing receipt while context still carries pending scoring.
- EvaluationPersistenceTests.test_omitted_receipt_cannot_commit_a_partial_turn.omitted:
  Remove the receipt before the real commit to test integrity enforcement.
- EvaluationPersistenceTests.test_relational_payload_mismatch_is_rejected_on_reload:
  Reject a deliberately corrupted payload after a fresh repository read.
- EvaluationPersistenceTests.test_scored_source_must_match_the_already_accepted_answer:
  Reject an answer changed after scoring inputs were frozen.
- EvaluationPersistenceTests.test_scored_source_must_match_the_already_accepted_answer.corrupt:
  Alter the accepted source to simulate corruption before commit.
- EvaluationPersistenceTests.test_finish_commits_current_answer_and_preserves_prior_receipts:
  Check explicit finish with and without a final answer against existing scoring history.
- EvaluationPersistenceTests.test_skip_survives_history_pruning_without_blocking_later_scoring:
  Check durable no-answer identity after serialization, pruning and repository restart.
- EvaluationPersistenceTests.test_discard_removes_only_owned_scoring_and_rolls_back_on_failure:
  Check ownership, rollback and complete deletion of protected scoring references.
Variable Index:
None
"""

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError
from django.test import TransactionTestCase

from agents.domain.errors import InvalidAgentState, StateConflictError
from app.adapters.assessment import AnswerDecision
from app.adapters.evaluation import AnswerEvidence
from app.adapters.rubric_evaluation import ShadowEvaluationAdapter
from app.providers.llm import LLMError
from evaluation.aggregator import replay_aggregation
from evaluation.persistence import EvaluationRecord
from interviews.agent_models import AgentAnswer, AgentInterview, AgentTurn
from interviews.agent_records import discard_interview, reserve_request
from interviews.agent_repository import DjangoInterviewRepository
from interviews.agent_session import AgentSession
from interviews.agent_socket import Answer, Finish, Skip, Start
from interviews.evaluation_models import AgentAssessment, AgentEvaluation, AgentShadowJob

from .agent_fixtures import ANSWER, RESUME, FixtureLLM, SafetyTestMixin, complete_fixture_request


class EvaluationPersistenceTests(SafetyTestMixin, TransactionTestCase):
    """Exercise actual SQL/CAS boundaries with deterministic offline provider output.

    Each test uses an isolated database; assertions cover stored rows, replay and public output,
    not online model quality. Intentional storage corruption is limited to each test database.
    """

    async def start_session(self, count=3, *, inline=False):
        """Reserve, start and approve an interview; return its session and initial response."""

        class ContinuingFixtureLLM(FixtureLLM):
            def __call__(self, prompt, data, schema):
                result = super().__call__(prompt, data, schema)
                if schema in (AnswerEvidence, AnswerDecision):
                    result.analysis.thread_complete = False
                    result.analysis.missing_information = ["How the fix was validated"]
                return result

        session = AgentSession(llm=ContinuingFixtureLLM())
        if inline:
            background = session.app.evaluation
            session.app.evaluation = ShadowEvaluationAdapter(background.legacy, background.formal)
        command = Start(request_id=uuid4(), type="start", resume_text=RESUME, max_questions=count)
        await reserve_request(session.interview_id, command)
        result = await session.start(command)
        await complete_fixture_request(session.interview_id, command.request_id, result)
        return session, result

    def command(self, response):
        """Build a new answer request bound to the response's immutable question ID."""
        return Answer(
            request_id=uuid4(),
            type="answer",
            answer_text=ANSWER,
            question_id=response["question"]["question_id"],
        )

    async def test_delayed_capability_assessment_survives_restart_and_is_source_bound(self):
        session, response = await self.start_session(count=8)
        entered = asyncio.Event()
        original = session.app.evaluation.assessment.assess

        async def slow(_):
            entered.set()
            await asyncio.Event().wait()

        try:
            with patch.object(session.app.evaluation.assessment, "assess", slow):
                first = self.command(response)
                await reserve_request(session.interview_id, first)
                response = await session.answer(first)
                await complete_fixture_request(session.interview_id, first.request_id, response)
                await entered.wait()
                self.assertEqual(response["last_evaluation"]["assessment_status"], "pending")
                second = self.command(response)
                await reserve_request(session.interview_id, second)
                response = await asyncio.wait_for(session.answer(second), 3)
                await complete_fixture_request(session.interview_id, second.request_id, response)
                self.assertEqual(await AgentAssessment.objects.acount(), 0)
                await session.close_background()
            fresh = DjangoInterviewRepository(session.interview_id)
            before = await fresh.get_interview_context(session.interview_id)
            jobs = await fresh.get_shadow_jobs(session.interview_id)
            record = await original(jobs[0])
            with self.assertRaises(InvalidAgentState):
                await fresh.append_assessment_record(
                    jobs[0], record.model_copy(update={"answer_id": "wrong"})
                )
            self.assertEqual(await AgentAssessment.objects.acount(), 0)
            await fresh.append_assessment_record(jobs[0], record)
            await fresh.append_assessment_record(jobs[0], record)
            self.assertEqual(await AgentAssessment.objects.acount(), 1)
            self.assertEqual(await fresh.get_interview_context(session.interview_id), before)
            raw = await AgentAnswer.objects.aget(request_id=first.request_id)
            self.assertEqual(raw.evaluation["assessment_status"], "pending")
            from evaluation.assessment import assessed_report_context

            projected = await assessed_report_context(fresh, before)
            self.assertEqual(projected.state.competencies["ownership"].score, 3)
            self.assertNotIn(record.answer_id, projected.unassessed_answer_ids)
            self.assertEqual(projected.state.current_question_id, before.state.current_question_id)
            self.assertEqual(projected.topic_progress, before.topic_progress)
        finally:
            await session.close_background()

    async def test_background_shadow_allows_next_answer_and_resumes_after_cancellation(self):
        """Persist pending jobs, advance SQL state, then resume scoring from a fresh repository."""
        session, response = await self.start_session(count=8)
        entered, release = asyncio.Event(), asyncio.Event()
        original = session.app.evaluation.formal._score

        async def slow(*args):
            entered.set()
            await release.wait()
            return await original(*args)

        try:
            with patch.object(session.app.evaluation.formal, "_score", slow):
                first = self.command(response)
                await reserve_request(session.interview_id, first)
                response = await session.answer(first)
                await complete_fixture_request(session.interview_id, first.request_id, response)
                await asyncio.wait_for(entered.wait(), 2)
                second = self.command(response)
                await reserve_request(session.interview_id, second)
                response = await asyncio.wait_for(session.answer(second), 3)
                await complete_fixture_request(session.interview_id, second.request_id, response)
                self.assertEqual(await AgentEvaluation.objects.acount(), 0)
                self.assertEqual(await AgentShadowJob.objects.acount(), 2)
                before = await session.app.repository.get_interview_context(session.interview_id)
                await session.close_background()
            session.app.evaluation.formal.repository = DjangoInterviewRepository(
                session.interview_id
            )
            self.assertTrue(
                await session.app.evaluation.drain(session.interview_id, timeout_seconds=3)
            )
            records = await session.app.repository.get_evaluation_records(session.interview_id)
            self.assertEqual(
                [r.input.request_id for r in records],
                [str(first.request_id), str(second.request_id)],
            )
            self.assertTrue(all(r.scored.evaluation.status == "completed" for r in records))
            self.assertEqual(
                await session.app.repository.get_interview_context(session.interview_id), before
            )
            self.assertEqual(
                records[1].scored.aggregation.inputs.supersedes_snapshot_id,
                records[0].scored.evaluation.score_snapshot.snapshot_id,
            )
        finally:
            await session.close_background()

    async def test_complete_ledger_replays_after_repository_restart_and_is_private(self):
        """Commit two answers, reload/replay receipts, and assert public APIs contain no ledger."""
        session, response = await self.start_session()
        first_payload = None
        for _ in range(2):
            command = self.command(response)
            await reserve_request(session.interview_id, command)
            response = await session.answer(command)
            await session.app.evaluation.drain(session.interview_id)
            await complete_fixture_request(session.interview_id, command.request_id, response)
            fresh = DjangoInterviewRepository(session.interview_id)
            records = await fresh.get_evaluation_records(session.interview_id)
            self.assertTrue(all(r.scored.evaluation.status == "completed" for r in records))
            if first_payload is None:
                first_payload = records[0].model_dump_json()
            self.assertEqual(records[0].model_dump_json(), first_payload)
        self.assertEqual(len(records), 2)
        self.assertEqual(
            records[1].scored.aggregation.inputs.supersedes_snapshot_id,
            records[0].scored.evaluation.score_snapshot.snapshot_id,
        )
        for record in records:
            self.assertEqual(
                replay_aggregation(record.scored.aggregation),
                record.scored.evaluation.score_snapshot,
            )
        rows = [row async for row in AgentEvaluation.objects.all()]
        for row in rows:
            answer = await AgentAnswer.objects.aget(id=row.answer_id)
            turn = await AgentTurn.objects.aget(feedback_request_id=row.feedback_request_id)
            self.assertEqual(row.committed_state_version, row.base_state_version + 1)
            self.assertEqual(row.committed_state_version, turn.state_version)
            self.assertEqual(row.committed_state_version, answer.committed_state_version)
        context = await fresh.get_interview_context(session.interview_id)
        self.assertIsNone(context.pending_evaluation)
        raw = await AgentInterview.objects.aget(id=session.interview_id)
        self.assertNotIn("pending_evaluation", raw.context)
        self.assertNotIn("aggregation-trace", json.dumps(raw.context))
        self.assertNotIn("score_snapshot", json.dumps(response))
        detail = (
            await self.async_client.get(f"/api/agent-interviews/{session.interview_id}/")
        ).json()
        self.assertNotIn("aggregation-trace", json.dumps(detail))
        with self.assertRaises(InvalidAgentState):
            await fresh.get_evaluation_records(str(uuid4()))

    async def test_late_transaction_failure_rolls_back_ledger_and_preserves_answer(self):
        """Fail after receipt insertion; assert rollback preserves only the accepted raw answer."""
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        before = await session.app.repository.get_interview_context(session.interview_id)
        with (
            patch(
                "interviews.agent_repository.AgentTurn.objects.create",
                side_effect=IntegrityError("injected after ledger insert"),
            ),
            self.assertRaises(StateConflictError),
        ):
            await session.answer(command)
            await session.app.evaluation.drain(session.interview_id)
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        self.assertEqual(await AgentShadowJob.objects.acount(), 0)
        self.assertEqual(
            await session.app.repository.get_interview_context(session.interview_id), before
        )
        answer = await AgentAnswer.objects.aget(request_id=command.request_id)
        self.assertEqual(answer.text, ANSWER)
        self.assertIsNone(answer.evaluation)
        self.assertIsNone(answer.committed_state_version)

    async def test_failed_shadow_persists_no_snapshot_and_survives_restart(self):
        """Fail the judge; verify later scores retain the failure across repository restart."""
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        with patch.object(
            session.app.evaluation.formal.service._judge, "judge", side_effect=LLMError("private")
        ):
            response = await session.answer(command)
            await session.app.evaluation.drain(session.interview_id)
        await complete_fixture_request(session.interview_id, command.request_id, response)
        row = await AgentEvaluation.objects.aget(feedback_request_id=command.request_id)
        self.assertIsNone(row.snapshot_id)
        record = EvaluationRecord.model_validate(row.payload)
        self.assertEqual(record.scored.evaluation.status, "failed")
        self.assertIsNone(record.scored.aggregation)
        session.app.evaluation.formal.repository = DjangoInterviewRepository(session.interview_id)
        next_command = self.command(response)
        await reserve_request(session.interview_id, next_command)
        await session.answer(next_command)
        await session.app.evaluation.drain(session.interview_id)
        row2 = await AgentEvaluation.objects.aget(feedback_request_id=next_command.request_id)
        next_record = EvaluationRecord.model_validate(row2.payload)
        self.assertEqual(
            next_record.scored.aggregation.inputs.evaluation_failure_codes,
            (f"unassessed_feedback:{command.request_id}",),
        )
        self.assertEqual((await AgentEvaluation.objects.aget(id=row.id)).payload, row.payload)

    async def test_stale_base_version_is_rejected_even_when_turn_cas_is_fresh(self):
        """Submit a stale receipt under a fresh turn CAS; neither context nor ledger may change."""
        session, first = await self.start_session(inline=True)
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        original = session.app.repository.commit_turn
        before = await session.app.repository.get_interview_context(session.interview_id)

        async def stale(request):
            """Decrement only the receipt version, then execute the real repository transaction."""
            record = request.evaluation_record
            request.evaluation_record = record.model_copy(
                update={"base_state_version": record.base_state_version - 1}
            )
            return await original(request)

        with patch.object(session.app.repository, "commit_turn", stale):
            with self.assertRaises(StateConflictError):
                await session.answer(command)
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        self.assertEqual(await AgentShadowJob.objects.acount(), 0)
        self.assertEqual(
            await session.app.repository.get_interview_context(session.interview_id), before
        )

    async def test_duplicate_feedback_does_not_append_or_recompute(self):
        """Repeat committed feedback; assert the saved action and single receipt remain."""
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        await session.answer(command)
        await session.app.evaluation.drain(session.interview_id)
        context = await session.app.repository.get_interview_context(session.interview_id)
        entry = context.question_history[-1]
        expected = await session.app.repository.get_processed_feedback_action(
            session.interview_id, str(command.request_id)
        )
        result = await session.service.apply_evaluation_feedback(
            session.interview_id, entry.feedback, answer=entry.answer
        )
        self.assertEqual(result, expected)
        self.assertEqual(await AgentEvaluation.objects.acount(), 1)

    async def test_omitted_receipt_cannot_commit_a_partial_turn(self):
        """Drop the pending receipt before commit and assert no partial feedback is persisted."""
        session, first = await self.start_session(inline=True)
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        original = session.app.repository.commit_turn

        async def omitted(request):
            """Clear the request receipt while preserving pending context, then delegate to SQL."""
            request.evaluation_record = None
            return await original(request)

        with patch.object(session.app.repository, "commit_turn", omitted):
            with self.assertRaises(InvalidAgentState):
                await session.answer(command)
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        self.assertIsNone(
            (await AgentAnswer.objects.aget(request_id=command.request_id)).evaluation
        )

    async def test_relational_payload_mismatch_is_rejected_on_reload(self):
        """Corrupt stored receipt identity and assert a fresh repository rejects the mismatch."""
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        await session.answer(command)
        await session.app.evaluation.drain(session.interview_id)
        row = await AgentEvaluation.objects.aget(feedback_request_id=command.request_id)
        row.payload["base_state_version"] += 1
        # Deliberately bypass the append-only repository to simulate storage corruption.
        await AgentEvaluation.objects.filter(id=row.id).aupdate(payload=row.payload)
        fresh = DjangoInterviewRepository(session.interview_id)
        with self.assertRaises(InvalidAgentState):
            await fresh.get_evaluation_records(session.interview_id)

    async def test_scored_source_must_match_the_already_accepted_answer(self):
        """Alter the accepted source after input capture and reject its scoring commit."""
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        original = session.app.repository.accept_answer

        async def corrupt(request_id, answer):
            """Persist the accepted answer and mutate its text to simulate a mismatched source."""
            await original(request_id, answer)
            await AgentAnswer.objects.filter(id=answer.answer_id).aupdate(text="Different source")

        with patch.object(session.app.repository, "accept_answer", corrupt):
            with self.assertRaises(InvalidAgentState):
                await session.answer(command)
                await session.app.evaluation.drain(session.interview_id)
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        self.assertIsNone(
            (await AgentAnswer.objects.aget(request_id=command.request_id)).evaluation
        )

    async def test_finish_commits_current_answer_and_preserves_prior_receipts(self):
        """Finish after one scored answer, with/without current speech; preserve prior receipts.

        Verify the new receipt shares the final turn's version and no extra question is generated.
        Empty finish is a lifecycle transition and must not invent a scoring record.
        """
        for include_answer in (False, True):
            with self.subTest(include_answer=include_answer):
                session, response = await self.start_session(count=8)
                command = self.command(response)
                await reserve_request(session.interview_id, command)
                response = await session.answer(command)
                await session.app.evaluation.drain(session.interview_id)
                await complete_fixture_request(session.interview_id, command.request_id, response)
                before = await session.app.repository.get_evaluation_records(session.interview_id)
                finish = Finish(
                    request_id=uuid4(),
                    type="finish",
                    **(
                        {"question_id": response["question"]["question_id"], "answer_text": ANSWER}
                        if include_answer
                        else {}
                    ),
                )
                await reserve_request(session.interview_id, finish)
                finished = await session.finish(finish)
                await complete_fixture_request(session.interview_id, finish.request_id, finished)
                records = await session.app.repository.get_evaluation_records(session.interview_id)
                self.assertEqual(len(records), 1 + int(include_answer))
                self.assertEqual(records[0], before[0])
                self.assertEqual(finished["type"], "finished")
                state = finished["result"]["interview_state"]
                self.assertEqual(state["question_index"], 2)
                if include_answer:
                    self.assertEqual(records[-1].input.request_id, str(finish.request_id))
                    self.assertEqual(records[-1].base_state_version + 1, state["state_version"])

    async def test_skip_survives_history_pruning_without_blocking_later_scoring(self):
        """Skip without invoking models, prune bounded history, then score after repository restart.

        The durable marker must survive JSON storage and suppress only the intentional empty turn;
        the following real answer must still produce a replayable receipt under normal validation.
        """
        session, response = await self.start_session(count=8)
        command = Skip(
            request_id=uuid4(), type="skip", question_id=response["question"]["question_id"]
        )
        await reserve_request(session.interview_id, command)
        with patch.object(session.app.evaluation, "evaluate", side_effect=AssertionError("skip")):
            response = await session.answer(command)
            await session.app.evaluation.drain(session.interview_id)
        await complete_fixture_request(session.interview_id, command.request_id, response)
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        raw = await AgentInterview.objects.aget(id=session.interview_id)
        self.assertIn(str(command.request_id), raw.context["unobserved_feedback_ids"])
        raw.context["question_history"] = []
        await raw.asave(update_fields=["context"])
        session.app.evaluation.formal.repository = DjangoInterviewRepository(session.interview_id)
        answer = self.command(response)
        await reserve_request(session.interview_id, answer)
        await session.answer(answer)
        await session.app.evaluation.drain(session.interview_id)
        records = await session.app.repository.get_evaluation_records(session.interview_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].scored.evaluation.status, "completed")
        self.assertEqual(records[0].scored.aggregation.inputs.evaluation_failure_codes, ())

    async def test_discard_removes_only_owned_scoring_and_rolls_back_on_failure(self):
        """Exercise explicit discard after scoring, including wrong owner and mid-delete failure.

        Protected answer/request links remain enforced during normal operation. Only the owned
        interview's explicit discard may remove its receipts, atomically with the source rows.
        """
        session, response = await self.start_session()
        command = self.command(response)
        await reserve_request(session.interview_id, command)
        await session.answer(command)
        await session.app.evaluation.drain(session.interview_id)
        row = await AgentEvaluation.objects.aget(interview_id=session.interview_id)
        await discard_interview(session.interview_id, owner_id=987654)
        self.assertTrue(await AgentEvaluation.objects.filter(id=row.id).aexists())
        with (
            patch(
                "interviews.agent_records.AgentAnswer.objects.filter",
                side_effect=IntegrityError("injected after scoring delete"),
            ),
            self.assertRaises(IntegrityError),
        ):
            await discard_interview(session.interview_id, owner_id=None)
        self.assertEqual((await AgentEvaluation.objects.aget(id=row.id)).payload, row.payload)
        self.assertTrue(await AgentTurn.objects.filter(interview_id=session.interview_id).aexists())
        await discard_interview(session.interview_id, owner_id=None)
        self.assertFalse(await AgentEvaluation.objects.filter(id=row.id).aexists())
        self.assertFalse(await AgentAnswer.objects.filter(id=row.answer_id).aexists())
        self.assertFalse(await AgentInterview.objects.filter(id=session.interview_id).aexists())
