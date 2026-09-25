"""职责：验证 Agent 持久化、数据库约束、失败原子性和本机历史接口。

实现：真实 Agent 配合显式 FixtureLLM 和隔离数据库，故障在事务末端注入以检查完整回滚。
关联：覆盖 agent_records、agent_repository、agent_socket 与只读 agent_history。

目录：
- PersistenceTests：
  用 TransactionTestCase 验证提交和清理后的可观测数据库状态。
- PersistenceTests.test_custom_project_and_topic_budgets_are_persisted：
  验证时长、计划、计时起点与安全上限一起持久化。
- PersistenceTests.test_start_rejects_conflicting_topic_options：
  拒绝新旧话题上限同时出现。
- PersistenceTests.start_session：
  预留 start 请求并运行真实 Agent 初始化，返回会话与首题。
- PersistenceTests.answer_command：
  为当前题构造唯一回答命令，不执行 I/O。
- PersistenceTests.test_saved_context_can_be_loaded_by_new_repository：
  新适配器读取相同上下文，越界读取失败。
- PersistenceTests.test_answer_evidence_and_report_are_persisted：
  成功轮次保存评价、请求响应和最终报告。
- PersistenceTests.test_failed_commit_rolls_back_state_question_log_and_evidence：
  事务末端失败不能留下半轮状态。
- PersistenceTests.test_version_conflict_does_not_publish_another_turn：
  相同旧版本不能再次发布动作。
- PersistenceTests.test_duplicate_request_across_connections_never_calls_model：
  重连重发被数据库去重，未生成孤立面试。
- PersistenceTests.test_pending_answer_survives_evaluation_failure：
  评价失败保留待评分回答，不制造评分记录。
- PersistenceTests.test_connection_interrupt_preserves_success_and_marks_pending：
  退出仅终结未完成请求。
- PersistenceTests.test_history_is_read_only_scoped_and_not_cached：
  分页列表不含正文，跨面试请求查询失败。
- PersistenceTests.test_storage_failure_prevents_model_execution：
  请求预留失败时不发 started、不调用模型。
- PersistenceTests.test_response_is_saved_before_transport_delivery：
  发送失败不能撤销已保存的成功响应。
- PersistenceTests.test_response_is_saved_before_transport_delivery.fail_delivery：
  仅在已生成问题发送时模拟断线。
- PersistenceTests.test_schema_rejects_partial_evidence_and_invalid_status：
  数据库直接拒绝无版本评分和非法状态。
- PersistenceTests.test_completed_report_survives_cleanup：
  完成后清理不会将成功报告改成 interrupted。
- PersistenceTests.test_one_pending_request_per_interview：
  不同 UUID 也不能绕过数据库的一场一请求约束。
- PersistenceTests.test_foreign_request_cannot_receive_an_answer：
  已接受请求不能被另一场面试用于保存回答。
- PersistenceTests.test_foreign_question_collision_rolls_back_commit：
  另一场面试的问题 ID 不能被覆盖，版本占用同时回滚。

关键变量：
（无模块级变量。）
约束：
测试不调用实际模型，不证明供应商取消、真实负载性能或多用户认证已经实现。
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
    complete_request,
    fail_request,
    interrupt_interview,
    reserve_request,
)
from interviews.agent_repository import DjangoInterviewRepository
from interviews.agent_session import AgentSession
from interviews.agent_socket import Answer, Start, agent_socket
from shared.contracts import CandidateAnswer, InterviewAction

from .agent_fixtures import ANSWER, RESUME, FixtureLLM
from .test_agent_progress import connect, disconnect, read, send_command


class PersistenceTests(TransactionTestCase):
    """用 TransactionTestCase 验证提交和清理后的可观测数据库状态。"""

    async def test_custom_project_and_topic_budgets_are_persisted(self):
        """验证时长、计划版本、实际计时起点和题数安全上限随上下文一起持久化。"""
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
        await complete_request(session.interview_id, command.request_id, result)
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
        """新旧话题上限互斥，错误在模型调用之前拒绝。"""
        with self.assertRaises(ValueError):
            Start(
                request_id=uuid4(),
                type="start",
                resume_text=RESUME,
                max_questions_per_topic=2,
                max_follow_up_per_topic=2,
            )

    async def start_session(self, count=2):
        """预留 start 请求并运行真实 Agent 初始化，返回会话与首题；只使用离线模型。"""
        session = AgentSession(llm=FixtureLLM())
        command = Start(request_id=uuid4(), type="start", resume_text=RESUME, max_questions=count)
        await reserve_request(session.interview_id, command)
        result = await session.start(command)
        await complete_request(session.interview_id, command.request_id, result)
        return session, result

    def answer_command(self, first):
        """为当前题构造唯一回答命令，不执行 I/O；输入为服务端 question 响应。"""
        return Answer(
            request_id=uuid4(),
            type="answer",
            answer_text=ANSWER,
            question_id=first["question"]["question_id"],
        )

    async def test_saved_context_can_be_loaded_by_new_repository(self):
        """新适配器读取相同上下文，越界读取失败；无需原会话缓存。"""
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
        """成功轮次保存评价、请求响应和最终报告，保持原 MVP 预算和数值评分。"""
        session, first = await self.start_session(count=1)
        command = self.answer_command(first)
        await reserve_request(session.interview_id, command)
        result = await session.answer(command)
        await complete_request(session.interview_id, command.request_id, result)
        answer = await AgentAnswer.objects.aget(request_id=command.request_id)
        record = await AgentInterview.objects.aget(id=session.interview_id)
        saved_request = await AgentRequest.objects.aget(id=command.request_id)
        turn = await AgentTurn.objects.aget(feedback_request_id=command.request_id)
        self.assertEqual(record.status, "completed")
        self.assertEqual(saved_request.response, result)
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
        """事务末端失败不能留下半轮状态；原回答保留，但评价和展示历史不能提前发布。"""
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
        """相同旧版本不能再次发布动作；测试复用已存问题，不请求模型生成。"""
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
        """重连重发被数据库去重，未生成孤立面试；响应不泄露原场次的内容或 ID。"""
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
        """评价失败保留待评分回答，不制造评分记录，错误存储不包含原始异常文本。"""
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
        """退出仅终结未完成请求，成功首题和已有上下文保持可读，不允许继续提交状态。"""
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
        """分页列表不含正文，跨面试请求查询失败；本机策略仍拒绝远程和跨源请求。"""
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
        """请求预留失败时不发 started、不调用模型，不用内存替代数据库。"""
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
        """发送失败不能撤销已保存的成功响应，也不能导致重复模型执行。"""
        queue = []

        async def fail_delivery(message):
            """仅在已生成问题发送时模拟断线，其他事件供测试验证，不模拟数据库行为。"""
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
        # 直接驱动 ASGI receive/send 边界，发送异常发生在真实数据库响应提交之后。
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
        """数据库直接拒绝无版本评分和非法状态，测试绕过仓库以验证真实 CHECK 约束。"""
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
        """完成后清理不会将成功报告改成 interrupted，历史详情可读取与发送结果相同的报告。"""
        session, first = await self.start_session(count=1)
        command = self.answer_command(first)
        await reserve_request(session.interview_id, command)
        result = await session.answer(command)
        await complete_request(session.interview_id, command.request_id, result)
        await interrupt_interview(session.interview_id)
        response = await self.async_client.get(f"/api/agent-interviews/{session.interview_id}/")
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(response.json()["final_report"], result["result"]["final_report"])

    async def test_one_pending_request_per_interview(self):
        """不同 UUID 也不能绕过数据库的一场一请求约束；第二次预留不创建额外记录。"""
        session, first = await self.start_session()
        one = self.answer_command(first)
        two = self.answer_command(first)
        await reserve_request(session.interview_id, one)
        with self.assertRaises(PendingRequest):
            await reserve_request(session.interview_id, two)
        self.assertFalse(await AgentRequest.objects.filter(id=two.request_id).aexists())
        self.assertEqual(await AgentRequest.objects.filter(status="running").acount(), 1)

    async def test_foreign_request_cannot_receive_an_answer(self):
        """已接受请求不能被另一场面试用于保存回答；失败后无跨场回答记录。"""
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
        """另一场面试的问题 ID 不能被覆盖，版本占用同时回滚；不调用模型生成额外问题。"""
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
