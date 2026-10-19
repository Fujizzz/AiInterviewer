from datetime import timedelta
from unittest.mock import AsyncMock, patch

from django.db import OperationalError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from interviews import evaluation_worker
from interviews.agent_records import discard_interview, interrupt_interview, reserve_request
from interviews.evaluation_models import AgentEvaluation, AgentEvaluationRun, AgentShadowJob
from interviews.evaluation_worker import claim_run, process_one, verified_report

from . import test_evaluation_persistence as persistence_tests
from .agent_fixtures import (
    FixtureBehaviorReviewer,
    FixtureLLM,
    SafetyTestMixin,
    complete_fixture_request,
)


def no_scoring_client(**kwargs):
    raise AssertionError("Report recovery must not construct a scoring model client")


@override_settings(EVALUATION_USE_WORKER=True, EVALUATION_FINAL_WAIT_SECONDS=0)
class WorkerTests(SafetyTestMixin, TransactionTestCase):
    use_durable_worker = True
    start_session = persistence_tests.EvaluationPersistenceTests.start_session
    command = persistence_tests.EvaluationPersistenceTests.command

    async def ended(self):
        session, first = await self.start_session(count=1)
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        result = await session.answer(command)
        await complete_fixture_request(session.interview_id, command.request_id, result)
        await session.close_background()
        self.assertEqual(result["result"]["evaluation_status"], "pending")
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        return session

    async def test_closed_connection_finishes_scoring_and_publishes_once(self):
        session = await self.ended()
        calls = []
        fixture = FixtureLLM()

        def llm(prompt, data, schema):
            calls.append(schema.__name__)
            return fixture(prompt, data, schema)

        self.assertTrue(await process_one(llm_factory=lambda **_: llm))
        run = await AgentEvaluationRun.objects.select_related("interview").aget(
            pk=session.interview_id
        )
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.report["result"]["final_report"]["unscored_answer_count"], 0)
        self.assertIsNotNone(verified_report(run))
        # This fixture leaves the live answer scope unknown: one formal Analyzer is needed.
        self.assertEqual(len(calls), 4)
        self.assertFalse(await process_one(llm_factory=lambda **_: llm))
        self.assertEqual(len(calls), 4)

    @override_settings(AI_SECURITY_ENABLED=True)
    async def test_background_report_uses_enabled_output_review(self):
        session = await self.ended()
        await process_one(llm_factory=lambda **_: FixtureLLM())
        run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.report_receipt["status"], "allow")

    async def test_finished_state_survives_crash_before_socket_receipt(self):
        session, first = await self.start_session(count=1)
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        await session.answer(command)
        await session.close_background()
        await process_one(llm_factory=lambda **_: FixtureLLM())
        run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(run.report)

    async def test_expired_claim_is_recovered_but_live_claim_is_exclusive(self):
        session = await self.ended()
        self.assertIsNotNone(await claim_run())
        self.assertIsNone(await claim_run())
        await AgentEvaluationRun.objects.filter(pk=session.interview_id).aupdate(
            lease_until=timezone.now() - timedelta(seconds=1)
        )
        self.assertTrue(await process_one(llm_factory=lambda **_: FixtureLLM()))
        self.assertEqual(await AgentEvaluation.objects.acount(), 1)

    async def test_saved_stage_survives_worker_restart(self):
        import asyncio

        from evaluation.resolver import EvidenceResolver

        session = await self.ended()
        entered = asyncio.Event()

        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(EvidenceResolver, "resolve", blocked):
            self.assertTrue(
                await process_one(llm_factory=lambda **_: FixtureLLM(), batch_seconds=0.5)
            )
        self.assertTrue(entered.is_set())
        job = await AgentShadowJob.objects.aget(interview_id=session.interview_id)
        self.assertIn("result", job.stage_journal["extractor"])
        calls = []
        fixture = FixtureLLM()

        def llm(prompt, data, schema):
            calls.append(schema.__name__)
            return fixture(prompt, data, schema)

        await process_one(llm_factory=lambda **_: llm)
        self.assertEqual(len(calls), 2)
        self.assertEqual(await AgentEvaluation.objects.acount(), 1)

    async def test_discarded_interview_never_calls_provider(self):
        session = await self.ended()
        await discard_interview(session.interview_id, None)

        def unexpected(**kwargs):
            raise AssertionError("discarded work cannot invoke provider")

        self.assertFalse(await process_one(llm_factory=unexpected))

    async def test_transient_failure_budget_is_durable_and_exhaustion_is_explicit(self):
        from app.providers.llm import LLMError

        session = await self.ended()
        await AgentEvaluationRun.objects.filter(pk=session.interview_id).aupdate(recovery_limit=0)
        attempts = []
        fixture = FixtureLLM()

        def failing(prompt, data, schema):
            if schema.__name__ == "ResolutionDraft":
                attempts.append(schema.__name__)
                raise LLMError("fixture timeout", code="timeout")
            return fixture(prompt, data, schema)

        await process_one(llm_factory=lambda **_: failing)
        self.assertEqual(len(attempts), 1)
        run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
        self.assertEqual(run.recovery_used, 0)
        self.assertEqual(run.report["result"]["evaluation_status"], "completed_with_errors")
        self.assertEqual(run.report["result"]["final_report"]["unscored_answer_count"], 1)
        self.assertFalse(await process_one(llm_factory=lambda **_: failing))
        self.assertEqual(len(attempts), 1)

    async def test_disconnect_before_final_answer_still_scores_committed_answer(self):
        session, first = await self.start_session(count=3)
        command = self.command(first)
        await reserve_request(session.interview_id, command)
        await session.answer(command)
        await session.close_background()
        await interrupt_interview(session.interview_id)
        await process_one(llm_factory=lambda **_: FixtureLLM())
        run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.report["result"]["final_report"]["unscored_answer_count"], 0)

    @override_settings(AI_SECURITY_ENABLED=True)
    async def test_single_storage_failure_reuses_approved_report(self):
        session = await self.ended()
        original = evaluation_worker.save_report
        writes = 0

        async def flaky(*args):
            nonlocal writes
            writes += 1
            if writes == 1:
                raise OperationalError("temporary storage failure")
            return await original(*args)

        reviewer = AsyncMock(wraps=FixtureBehaviorReviewer().assess)
        with (
            patch.object(evaluation_worker, "save_report", flaky),
            patch.object(FixtureBehaviorReviewer, "assess", reviewer),
        ):
            await process_one(llm_factory=lambda **_: FixtureLLM())
        run = await AgentEvaluationRun.objects.select_related("interview").aget(
            pk=session.interview_id
        )
        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(verified_report(run))
        self.assertEqual(run.publication_attempts, 1)
        self.assertEqual(writes, 2)
        self.assertEqual(reviewer.await_count, 1)

    async def test_storage_outage_requeues_with_backoff_without_rescoring(self):
        session = await self.ended()
        with patch.object(
            evaluation_worker,
            "save_report",
            AsyncMock(side_effect=OperationalError("temporary storage outage")),
        ) as writes:
            await process_one(llm_factory=lambda **_: FixtureLLM())
        run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
        self.assertEqual(writes.await_count, 3)
        self.assertEqual(run.status, "pending")
        self.assertGreater(run.lease_until, timezone.now())
        self.assertIsNone(await claim_run())
        self.assertEqual(await AgentEvaluation.objects.acount(), 1)
        await AgentEvaluationRun.objects.filter(pk=session.interview_id).aupdate(
            lease_until=timezone.now() - timedelta(seconds=1)
        )
        await process_one(llm_factory=no_scoring_client)
        run = await AgentEvaluationRun.objects.select_related("interview").aget(
            pk=session.interview_id
        )
        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(verified_report(run))
        self.assertEqual(run.publication_attempts, 2)
        self.assertEqual(await AgentEvaluation.objects.acount(), 1)

    async def test_crash_after_publication_reservation_can_resume(self):
        import asyncio

        session = await self.ended()
        with patch.object(
            evaluation_worker.ReportGateway,
            "publish",
            AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await process_one(llm_factory=lambda **_: FixtureLLM())
        # A killed worker leaves a running lease rather than executing finally.
        await AgentEvaluationRun.objects.filter(pk=session.interview_id).aupdate(
            status="running",
            lease_token="dead-worker",
            lease_until=timezone.now() - timedelta(seconds=1),
        )
        await process_one(llm_factory=no_scoring_client)
        run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(run.report)
        self.assertEqual(run.publication_attempts, 2)

    async def test_saved_report_survives_crash_before_queue_completion(self):
        session = await self.ended()
        with patch.object(
            evaluation_worker,
            "release_run",
            AsyncMock(side_effect=OperationalError("crash after report commit")),
        ):
            with self.assertRaises(OperationalError):
                await process_one(llm_factory=lambda **_: FixtureLLM())
        await AgentEvaluationRun.objects.filter(pk=session.interview_id).aupdate(
            lease_until=timezone.now() - timedelta(seconds=1)
        )
        with patch.object(
            evaluation_worker.ReportGateway,
            "publish",
            AsyncMock(side_effect=AssertionError("Saved reports must not be reviewed again")),
        ) as publish:
            await process_one(llm_factory=no_scoring_client)
        run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.publication_attempts, 1)
        publish.assert_not_awaited()

    async def test_publication_storage_retries_have_a_persistent_limit(self):
        session = await self.ended()
        with patch.object(
            evaluation_worker,
            "save_report",
            AsyncMock(side_effect=OperationalError("persistent storage outage")),
        ) as writes:
            await process_one(llm_factory=lambda **_: FixtureLLM())
            for _ in range(2):
                await AgentEvaluationRun.objects.filter(pk=session.interview_id).aupdate(
                    lease_until=timezone.now() - timedelta(seconds=1)
                )
                await process_one(llm_factory=no_scoring_client)
        run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.publication_attempts, 3)
        self.assertEqual(writes.await_count, 9)
        self.assertIsNone(run.report)
        self.assertIsNone(await claim_run())

    async def test_report_retry_rechecks_version_and_does_not_retry_rejection(self):
        from interviews.agent_models import AgentInterview

        session = await self.ended()
        original = evaluation_worker.save_report
        writes = 0

        async def changed(*args):
            nonlocal writes
            writes += 1
            if writes == 1:
                interview = await AgentInterview.objects.aget(pk=session.interview_id)
                await AgentInterview.objects.filter(pk=session.interview_id).aupdate(
                    state_version=interview.state_version + 1
                )
                raise OperationalError("temporary storage failure")
            return await original(*args)

        with patch.object(evaluation_worker, "save_report", changed):
            await process_one(llm_factory=lambda **_: FixtureLLM())
        run = await AgentEvaluationRun.objects.aget(pk=session.interview_id)
        self.assertEqual(writes, 2)
        self.assertEqual(run.status, "failed")
        self.assertIsNone(run.report)
        self.assertIsNone(await claim_run())

    async def test_old_analysis_defaults_are_read_without_rewriting_receipts(self):
        from copy import deepcopy

        from interviews.agent_models import AgentAnswer
        from interviews.agent_repository import DjangoInterviewRepository

        session = await self.ended()
        row = await AgentShadowJob.objects.select_related("answer").aget(
            interview_id=session.interview_id
        )
        previous = deepcopy(row.answer.evaluation)
        previous["analysis"].pop("needs_assessed", None)
        previous["analysis"].pop("need_updates", None)
        await AgentAnswer.objects.filter(pk=row.answer_id).aupdate(evaluation=previous)
        old_payload = deepcopy(row.payload)
        old_payload["feedback"] = previous
        await AgentShadowJob.objects.filter(pk=row.pk).aupdate(payload=old_payload)
        jobs = await DjangoInterviewRepository(session.interview_id).get_shadow_jobs(
            session.interview_id
        )
        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0].feedback.analysis.needs_assessed)
        self.assertEqual(jobs[0].feedback.analysis.need_updates, [])
        answer = await AgentAnswer.objects.aget(pk=row.answer_id)
        self.assertEqual(answer.evaluation, previous)

    async def test_old_default_compatibility_does_not_accept_changed_feedback(self):
        from copy import deepcopy

        from agents.domain.errors import InvalidAgentState
        from interviews.agent_models import AgentAnswer
        from interviews.agent_repository import DjangoInterviewRepository

        session = await self.ended()
        row = await AgentShadowJob.objects.select_related("answer").aget(
            interview_id=session.interview_id
        )
        repo = DjangoInterviewRepository(session.interview_id)
        for change in (
            {"needs_assessed": not row.answer.evaluation["analysis"]["needs_assessed"]},
            {"summary": "altered"},
            {"extra": "altered"},
        ):
            modified = deepcopy(row.answer.evaluation)
            modified["analysis"].update(change)
            await AgentAnswer.objects.filter(pk=row.answer_id).aupdate(evaluation=modified)
            with self.assertRaises(InvalidAgentState):
                await repo.get_shadow_jobs(session.interview_id)
