"""Fixed opening uses real Agent/database transitions and never creates evaluator evidence."""

from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from agents.question.introduction import INTRODUCTION_TEXT
from interviews.agent_models import AgentAnswer
from interviews.agent_records import reserve_request
from interviews.agent_session import AgentSession
from interviews.agent_socket import Answer, Finish, Skip, Start
from interviews.api.resume_versions import resolve_resume_version
from interviews.evaluation_models import AgentEvaluation, AgentShadowJob
from interviews.resume_models import ResumeVersion
from interviews.resume_profile import prepare_profile

from .agent_fixtures import ANSWER, RESUME, FixtureLLM, complete_fixture_request


class IntroductionTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="intro-owner")
        self.version = ResumeVersion.objects.create(owner=self.owner, text=RESUME, status="ready")
        prepare_profile(self.version.pk, self.owner.pk, llm=FixtureLLM())

    async def start_session(self):
        llm = FixtureLLM()
        session = AgentSession(llm=llm)
        command = Start(
            type="start",
            request_id=uuid4(),
            resume_version_id=self.version.pk,
            duration_minutes=10,
            max_questions=1,
        )
        command = await resolve_resume_version(command, self.owner.pk)
        await reserve_request(
            session.interview_id, command, owner_id=self.owner.pk, resume_version_id=self.version.pk
        )
        result = await session.start(command)
        await complete_fixture_request(session.interview_id, command.request_id, result)
        self.assertEqual(result["question"]["text"], INTRODUCTION_TEXT)
        self.assertEqual(result["interview_state"]["stage"], "intro")
        self.assertEqual(result["question_index"], 1)
        self.assertIsNone(result["last_evaluation"])
        self.assertTrue(
            all(schema.__name__ == "CompactPlanProposal" for schema in llm.calls),
            "Only speculative planning may run while the fixed introduction is displayed",
        )
        return session, llm, result

    async def test_intro_answer_saved_unscored_then_first_project_question(self):
        session, llm, first = await self.start_session()
        command = Answer(
            type="answer",
            request_id=uuid4(),
            question_id=first["question"]["question_id"],
            answer_text=ANSWER,
        )
        await reserve_request(session.interview_id, command, owner_id=self.owner.pk)
        with patch.object(
            session.app.evaluation, "evaluate", side_effect=AssertionError("No intro eval")
        ):
            result = await session.answer(command)
        self.assertEqual(result["type"], "question")
        self.assertNotEqual(result["question"]["intent"], "SELF_INTRODUCTION")
        self.assertEqual(result["question_index"], 2)
        self.assertIsNone(result["last_evaluation"])
        stored = await AgentAnswer.objects.aget(request_id=command.request_id)
        self.assertEqual(stored.text, ANSWER)
        self.assertIsNone(stored.evaluation)
        self.assertEqual(stored.unscored_reason, "self_introduction")
        self.assertIsNotNone(stored.committed_state_version)
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        self.assertEqual(await AgentShadowJob.objects.acount(), 0)
        context = await session.app.repository.get_interview_context(session.interview_id)
        self.assertEqual(context.question_history, [])
        self.assertEqual(context.evidence_records, [])
        self.assertEqual(context.processed_feedback_ids, [])
        replayed = await session.service.complete_introduction(
            session.interview_id,
            context.introduction_answer,
            request_id=command.request_id,
        )
        self.assertEqual(replayed.action_id, session.action.action_id)
        await session.close_background()
        session.close()

    async def test_skipped_intro_enters_main_flow_without_evaluation(self):
        session, _, first = await self.start_session()
        command = Skip(
            type="skip", request_id=uuid4(), question_id=first["question"]["question_id"]
        )
        await reserve_request(session.interview_id, command, owner_id=self.owner.pk)
        with patch.object(
            session.app.evaluation, "evaluate", side_effect=AssertionError("No intro eval")
        ):
            result = await session.answer(command)
        self.assertEqual(result["type"], "question")
        self.assertEqual(result["interview_state"]["stage"], "project_deep_dive")
        await session.close_background()
        session.close()

    async def test_finish_with_intro_does_not_score_or_generate_project_question(self):
        session, llm, first = await self.start_session()
        command = Finish(
            type="finish",
            request_id=uuid4(),
            question_id=first["question"]["question_id"],
            answer_text=ANSWER,
        )
        await reserve_request(session.interview_id, command, owner_id=self.owner.pk)
        with patch.object(
            session.app.evaluation, "evaluate", side_effect=AssertionError("No intro eval")
        ):
            result = await session.finish(command)
        self.assertEqual(result["type"], "finished")
        self.assertIsNone(result["result"]["final_report"]["overall_score"])
        self.assertEqual(result["result"]["final_report"]["unscored_answer_count"], 0)
        self.assertNotIn("QuestionAgentDecision", [schema.__name__ for schema in llm.calls])
        self.assertEqual(await AgentEvaluation.objects.acount(), 0)
        await session.close_background()
        session.close()
