"""Verify test-only exclusions through real backend handlers and persistence.

Declaration Index:
- AnalysisOnlyLLM: Reject scoring/report schemas while serving offline dialogue fixtures.
- AnalysisOnlyLLM.__init__: Initialize captured schema names.
- AnalysisOnlyLLM.__call__: Fail on scoring requests and delegate valid analysis/question calls.
- LiveQuestionSessionTests: Exercise question-only and opt-in production compositions.
- LiveQuestionSessionTests.setUp: Store one offline parsed resume.
- LiveQuestionSessionTests.command: Apply request ownership and normal session persistence.
- LiveQuestionSessionTests.exercise: Complete introduction, technical answers and finish.
- LiveQuestionSessionTests.test_no_background_scoring_or_report_with_final_answer: Check ending.
- LiveQuestionSessionTests.test_finish_without_final_answer_also_omits_report: Check early ending.
- LiveQuestionSessionTests.test_explicit_evaluation_opt_in_keeps_normal_session: Check opt-in scope.
- LiveQuestionSessionTests.test_metrics_mark_scoring_as_not_tested: Check test report boundaries.
"""

import sys
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from app.adapters.assessment import RealtimeDecisionAdapter
from backend.tests.live_question_session import create_live_session
from interviews.agent_models import AgentAnswer
from interviews.agent_records import reserve_request
from interviews.agent_session import AgentSession
from interviews.agent_socket import Answer, Finish, Start
from interviews.api.resume_versions import resolve_resume_version
from interviews.evaluation_models import AgentAssessment, AgentEvaluation, AgentShadowJob
from interviews.resume_models import ResumeVersion
from interviews.resume_profile import prepare_profile

from .agent_fixtures import ANSWER, RESUME, FixtureLLM, complete_fixture_request


class AnalysisOnlyLLM(FixtureLLM):
    def __init__(self):
        super().__init__()
        self.schemas = []

    def __call__(self, prompt, data, schema):
        self.schemas.append(schema.__name__)
        if schema.__module__.startswith("evaluation.") or schema.__name__ in {
            "AnswerAssessment",
            "ReportNarrative",
        }:
            raise AssertionError("Question-only test invoked scoring/report generation")
        return super().__call__(prompt, data, schema)


class LiveQuestionSessionTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="question-only-owner")
        self.version = ResumeVersion.objects.create(owner=self.owner, text=RESUME, status="ready")
        prepare_profile(self.version.pk, self.owner.pk, llm=FixtureLLM())

    async def command(self, session, command):
        command = await resolve_resume_version(command, self.owner.pk)
        await reserve_request(
            session.interview_id,
            command,
            owner_id=self.owner.pk,
            resume_version_id=getattr(command, "resume_version_id", None),
        )
        result = await getattr(session, command.type)(command)
        await complete_fixture_request(session.interview_id, command.request_id, result)
        return result

    async def exercise(self, *, final_answer):
        llm = AnalysisOnlyLLM()
        session = create_live_session(llm=llm)
        try:
            self.assertIs(type(session.app.evaluation), RealtimeDecisionAdapter)
            with patch(
                "interviews.agent_session.build_final_report",
                side_effect=AssertionError(
                    "Question-only test must not build numeric or narrative reports"
                ),
            ):
                first = await self.command(
                    session,
                    Start(
                        type="start",
                        request_id=uuid4(),
                        resume_version_id=self.version.pk,
                        duration_minutes=10,
                        max_questions=3,
                    ),
                )
                technical = await self.command(
                    session,
                    Answer(
                        type="answer",
                        request_id=uuid4(),
                        question_id=first["question"]["question_id"],
                        answer_text=ANSWER,
                    ),
                )
                self.assertEqual(technical["type"], "question")
                next_question = await self.command(
                    session,
                    Answer(
                        type="answer",
                        request_id=uuid4(),
                        question_id=technical["question"]["question_id"],
                        answer_text=ANSWER,
                    ),
                )
                self.assertEqual(next_question["type"], "question")
                self.assertIn("CompactAnswerDecision", llm.schemas)
                finish = Finish(
                    type="finish",
                    request_id=uuid4(),
                    question_id=next_question["question"]["question_id"] if final_answer else None,
                    answer_text=ANSWER if final_answer else None,
                )
                result = await self.command(session, finish)
                self.assertEqual(result["type"], "finished")
                self.assertFalse(result["result"]["evaluation_tested"])
                self.assertFalse(result["result"]["report_generated"])
                self.assertNotIn("final_report", result["result"])
                self.assertTrue(result["result"]["interview_finished"])
            context = await session.app.repository.get_interview_context(session.interview_id)
            self.assertEqual(context.state.status, "finished")
            self.assertGreaterEqual(len(context.question_history), 1)
            self.assertEqual(context.assessment_feedback_ids, [])
            self.assertEqual(await AgentShadowJob.objects.acount(), 0)
            self.assertEqual(await AgentEvaluation.objects.acount(), 0)
            self.assertEqual(await AgentAssessment.objects.acount(), 0)
            self.assertGreaterEqual(await AgentAnswer.objects.acount(), 2)
        finally:
            await session.close_background()
            session.close()

    async def test_no_background_scoring_or_report_with_final_answer(self):
        await self.exercise(final_answer=True)

    async def test_finish_without_final_answer_also_omits_report(self):
        await self.exercise(final_answer=False)

    async def test_explicit_evaluation_opt_in_keeps_normal_session(self):
        session = create_live_session(include_evaluation=True, llm=FixtureLLM())
        try:
            self.assertIs(type(session), AgentSession)
            self.assertTrue(hasattr(session.app.evaluation, "start_background"))
        finally:
            await session.close_background()
            session.close()

    def test_metrics_mark_scoring_as_not_tested(self):
        from backend.tests import run_full_preparation_benchmark as benchmark

        with patch.dict(sys.modules, {"run_full_preparation_benchmark": benchmark}):
            from backend.tests.analyze_live_twenty_minute import summarize

        report = dict(
            status="completed",
            model="offline",
            evaluation_tested=False,
            report_generated=False,
            turns=[],
            api_requests=[],
            logical_calls=[],
            progress=[],
            traces=[],
            question_attempts=[],
            final_response={"type": "finished", "result": {"test_mode": "questions_only"}},
        )
        summary = summarize(report)
        self.assertEqual(summary["final_score_status"], "not_tested")
        self.assertIsNone(summary["overall_score"])
        self.assertIsNone(summary["unscored_answer_count"])
        self.assertIsNone(summary["final_report_wait_seconds"])
