"""Real SQL transactions for Evaluation's append-only shadow ledger."""

import json
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError
from django.test import TransactionTestCase

from agents.domain.errors import InvalidAgentState, StateConflictError
from app.providers.llm import LLMError
from evaluation.aggregator import replay_aggregation
from evaluation.persistence import EvaluationRecord
from interviews.agent_models import AgentAnswer, AgentInterview, AgentTurn
from interviews.agent_records import reserve_request
from interviews.agent_repository import DjangoInterviewRepository
from interviews.agent_session import AgentSession
from interviews.agent_socket import Answer, Start
from interviews.evaluation_models import AgentEvaluation

from .agent_fixtures import ANSWER, RESUME, FixtureLLM, SafetyTestMixin, complete_fixture_request


class EvaluationPersistenceTests(SafetyTestMixin, TransactionTestCase):
    async def start_session(self, count=3):
        session = AgentSession(llm=FixtureLLM())
        command = Start(request_id=uuid4(), type="start", resume_text=RESUME, max_questions=count)
        await reserve_request(session.interview_id, command)
        result = await session.start(command)
        await complete_fixture_request(session.interview_id, command.request_id, result)
        return session, result

    def command(self, response):
        return Answer(
            request_id=uuid4(),
            type="answer",
            answer_text=ANSWER,
            question_id=response["question"]["question_id"],
        )

    async def test_complete_ledger_replays_after_repository_restart_and_is_private(self):
        session, response = await self.start_session()
        first_payload = None
        for _ in range(2):
            command = self.command(response)
            await reserve_request(session.interview_id, command)
            response = await session.answer(command)
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
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        self.assertEqual(
            await session.app.repository.get_interview_context(session.interview_id), before
        )
        answer = await AgentAnswer.objects.aget(request_id=command.request_id)
        self.assertEqual(answer.text, ANSWER)
        self.assertIsNone(answer.evaluation)
        self.assertIsNone(answer.committed_state_version)

    async def test_failed_shadow_persists_no_snapshot_and_survives_restart(self):
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        with patch.object(
            session.app.evaluation.formal.service._judge, "judge", side_effect=LLMError("private")
        ):
            response = await session.answer(command)
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
        row2 = await AgentEvaluation.objects.aget(feedback_request_id=next_command.request_id)
        next_record = EvaluationRecord.model_validate(row2.payload)
        self.assertEqual(
            next_record.scored.aggregation.inputs.evaluation_failure_codes,
            (f"unassessed_feedback:{command.request_id}",),
        )
        self.assertEqual((await AgentEvaluation.objects.aget(id=row.id)).payload, row.payload)

    async def test_stale_base_version_is_rejected_even_when_turn_cas_is_fresh(self):
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        original = session.app.repository.commit_turn
        before = await session.app.repository.get_interview_context(session.interview_id)

        async def stale(request):
            record = request.evaluation_record
            request.evaluation_record = record.model_copy(
                update={"base_state_version": record.base_state_version - 1}
            )
            return await original(request)

        with patch.object(session.app.repository, "commit_turn", stale):
            with self.assertRaises(StateConflictError):
                await session.answer(command)
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        self.assertEqual(
            await session.app.repository.get_interview_context(session.interview_id), before
        )

    async def test_duplicate_feedback_does_not_append_or_recompute(self):
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        await session.answer(command)
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
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        original = session.app.repository.commit_turn

        async def omitted(request):
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
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        await session.answer(command)
        row = await AgentEvaluation.objects.aget(feedback_request_id=command.request_id)
        row.payload["base_state_version"] += 1
        # Deliberately bypass the append-only repository to simulate storage corruption.
        await AgentEvaluation.objects.filter(id=row.id).aupdate(payload=row.payload)
        fresh = DjangoInterviewRepository(session.interview_id)
        with self.assertRaises(InvalidAgentState):
            await fresh.get_evaluation_records(session.interview_id)

    async def test_scored_source_must_match_the_already_accepted_answer(self):
        session, first = await self.start_session()
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        original = session.app.repository.accept_answer

        async def changed_source(request_id, answer):
            await original(request_id, answer)
            await AgentAnswer.objects.filter(id=answer.answer_id).aupdate(text="Different source")

        with patch.object(session.app.repository, "accept_answer", changed_source):
            with self.assertRaises(InvalidAgentState):
                await session.answer(command)
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        self.assertIsNone(
            (await AgentAnswer.objects.aget(request_id=command.request_id)).evaluation
        )
