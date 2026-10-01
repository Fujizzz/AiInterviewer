"""阶段事件、预解析缓存与先行评分的离线回归；真实 Agent 配合显式 FixtureLLM。

实现：通过 ASGI 通道验证顺序、关联、失效和取消；用事件屏障证明报告未完成时评分已送达。
关联：复用 agent_socket、AgentSession 与固定测试数据；TransactionTestCase 隔离持久化数据库。
目录：
- connect：建立本机同源测试连接并验证能力公告。
- read：读取一条 JSON 事件，超时或非文本响应直接失败。
- send_command：发送显式订阅事件的唯一命令，返回请求 ID。
- collect_until：收集请求内事件直到指定终态，验证关联且拒绝意外错误。
- disconnect：断开并等待 ASGI 任务完成。
- ProgressTests：验证预解析、评分提前交付与错误边界，不调用实际供应商。
- ProgressTests.test_prepare_reuse_and_original_result：预解析不出题，相同文本开始时仅解析一次。
- ProgressTests.test_changed_resume_and_separate_connection：文本变更及新连接均不能复用旧资料。
- ProgressTests.test_prepare_duplicate_and_answer_before_start：预解析阶段拒绝重复请求和提前回答。
- ProgressTests.test_score_arrives_before_blocked_report：通过报告屏障验证先行评分及终态一致。
- ProgressTests.test_score_arrives_before_blocked_report.block_report：
  仅阻塞有模型的文字生成，数值计算保持真实。
- ProgressTests.test_cancel_during_prepare：预解析期间可取消，任务清理且没有 prepared 结果。
- ProgressTests.test_cancel_during_prepare.block_parse：阻塞解析并以 finally 记录取消传递。
- ProgressTests.test_parse_failure_is_explicit：解析失败不发完成事件、不缓存成功、不泄露正文。
- ProgressTests.test_parse_failure_is_explicit.fail_parse：注入含敏感标记异常供脱敏检查。
- ProgressTests.test_report_fallback_is_visible：既有摘要回退被明确标记，数值与预算不变。
- ProgressTests.test_report_fallback_is_visible.fail_report：
  只让报告模型抛错，其他 schema 使用原 fixture。
关键变量：
（无模块级变量。）
约束：
fixture 结果不能证明真实模型速度、准确性或供应商取消；测量的是协议事件的先后。
"""

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

from asgiref.testing import ApplicationCommunicator
from django.test import TransactionTestCase

from app.parsing.resume import ResumeExtraction
from app.reporting.final_report import ReportNarrative, build_final_report
from interviews.agent_records import complete_request, reserve_request
from interviews.agent_session import AgentSession
from interviews.agent_socket import Answer, Start, agent_socket

from .agent_fixtures import ANSWER, RESUME, FixtureLLM


async def connect():
    """无需外部参数；建立本机同源 ASGI 连接，验证 hello，返回待显式关闭的 communicator。"""
    comm = ApplicationCommunicator(
        agent_socket,
        {
            "type": "websocket",
            "path": "/ws/agent/",
            "scheme": "ws",
            "client": ("127.0.0.1", 12345),
            "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")],
        },
    )
    await comm.send_input({"type": "websocket.connect"})
    assert (await comm.receive_output())["type"] == "websocket.accept"
    hello = await read(comm)
    assert hello["capabilities"] == ["prepare", "progress", "assessment"]
    return comm


async def read(comm):
    """输入测试通道，返回下一条 JSON；3 秒仅为测试断言边界，不改变生产超时。"""
    return json.loads((await comm.receive_output(timeout=3))["text"])


async def send_command(comm, kind, **fields):
    """输入通道、命令类型及字段；默认订阅事件，允许显式 request_id 用于重复请求测试。"""
    payload = {"type": kind, "request_id": str(uuid4()), "progress_events": True, **fields}
    await comm.send_input({"type": "websocket.receive", "text": json.dumps(payload)})
    return payload["request_id"]


async def collect_until(comm, kind, request_id):
    """读取同一 request_id 的事件直到指定类型并返回列表；错误、串请求或无限事件均失败。"""
    events = []
    for _ in range(20):
        event = await read(comm)
        assert event["request_id"] == request_id
        assert event["type"] != "error", event
        events.append(event)
        if event["type"] == kind:
            return events
    raise AssertionError("Expected terminal event was not received")


async def disconnect(comm):
    """输入通道，发送断开并等待本地清理；不请求取消真实供应商，返回无。"""
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()


class ProgressTests(TransactionTestCase):
    """固定模型与真实状态机组成协议测试；不允许数据库访问，不等价于真实 API 验证。"""

    async def test_prepare_reuse_and_original_result(self):
        """prepare 仅解析一次；start 精确复用、只生成首题，验证阶段与原有预算。"""
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = await connect()
            try:
                rid = await send_command(comm, "prepare", resume_text=RESUME)
                events = await collect_until(comm, "prepared", rid)
                self.assertEqual(
                    [e["type"] for e in events],
                    [
                        "started",
                        "progress",
                        "progress",
                        "prepared",
                    ],
                )
                self.assertEqual(events[1]["stage"], "resume_parsing")
                self.assertEqual(llm.calls, [ResumeExtraction])
                rid = await send_command(comm, "start", resume_text=RESUME, max_questions=1)
                events = await collect_until(comm, "question", rid)
                self.assertEqual(llm.calls.count(ResumeExtraction), 1)
                self.assertEqual(events[1]["stage"], "question_generation")
                self.assertEqual(events[-1]["interview_state"]["elapsed_seconds"], 0)
                await send_command(comm, "prepare", resume_text=RESUME)
                self.assertEqual((await read(comm))["code"], "already_started")
            finally:
                await disconnect(comm)
            self.assertTrue(llm.closed)

    async def test_changed_resume_and_separate_connection(self):
        """相同文本在当前连接复用，改变文本或打开新连接都必须重新解析，不能串缓存。"""
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = await connect()
            try:
                for text in (RESUME, RESUME, RESUME + " A new project."):
                    rid = await send_command(comm, "prepare", resume_text=text)
                    await collect_until(comm, "prepared", rid)
                self.assertEqual(llm.calls.count(ResumeExtraction), 2)
            finally:
                await disconnect(comm)
        fresh = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=fresh):
            comm = await connect()
            try:
                rid = await send_command(comm, "start", resume_text=RESUME)
                await collect_until(comm, "question", rid)
                self.assertEqual(fresh.calls.count(ResumeExtraction), 1)
            finally:
                await disconnect(comm)

    async def test_prepare_duplicate_and_answer_before_start(self):
        """prepared 不是已开始面试；重复 UUID 和提前回答被拒绝，不增加模型调用。"""
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = await connect()
            try:
                rid = await send_command(comm, "prepare", resume_text=RESUME)
                await collect_until(comm, "prepared", rid)
                await send_command(comm, "prepare", resume_text=RESUME, request_id=rid)
                self.assertEqual((await read(comm))["code"], "duplicate_request")
                await send_command(comm, "answer", answer_text=ANSWER, question_id="missing")
                self.assertEqual((await read(comm))["code"], "not_started")
                self.assertEqual(llm.calls, [ResumeExtraction])
            finally:
                await disconnect(comm)

    async def test_score_arrives_before_blocked_report(self):
        """人为阻塞报告文字，要求先收到相同确定性评分；释放后完整报告及预算仍正确。"""
        release = asyncio.Event()

        async def block_report(context, history, *, llm=None):
            """输入原始报告参数；只在 llm 非空时等待屏障，再委派真实数值与文字报告流程。"""
            if llm is not None:
                await release.wait()
            return await build_final_report(context, history, llm=llm)

        with (
            patch("interviews.agent_session.BackendLLM", return_value=FixtureLLM()),
            patch("interviews.agent_session.build_final_report", side_effect=block_report),
        ):
            comm = await connect()
            try:
                rid = await send_command(comm, "start", resume_text=RESUME, max_questions=1)
                first = (await collect_until(comm, "question", rid))[-1]
                rid = await send_command(
                    comm, "answer", answer_text=ANSWER, question_id=first["question"]["question_id"]
                )
                events = await collect_until(comm, "assessment", rid)
                score = events[-1]["assessment"]
                self.assertAlmostEqual(score["overall_score"], 3.0)
                running = await read(comm)
                self.assertEqual(
                    (running["stage"], running["state"]), ("report_generation", "running")
                )
                self.assertFalse(release.is_set())
                release.set()
                final = (await collect_until(comm, "finished", rid))[-1]["result"]
                self.assertEqual(final["final_report"]["overall_score"], score["overall_score"])
                self.assertEqual(final["final_report"]["competencies"], score["competencies"])
                self.assertLess(final["interview_state"]["elapsed_seconds"], 120)
                self.assertEqual((await comm.receive_output())["code"], 1000)
                await comm.wait()
            finally:
                release.set()
                await disconnect(comm)

    async def test_cancel_during_prepare(self):
        """解析屏障未完成时取消连接，验证取消传入解析协程且没有 prepared 成功响应。"""
        stopped = asyncio.Event()

        async def block_parse(*args, **kwargs):
            """输入模拟解析参数；持续等待直到被取消，finally 设置清理标记，无返回资料。"""
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        llm = FixtureLLM()
        with (
            patch("interviews.agent_session.BackendLLM", return_value=llm),
            patch("interviews.agent_session.parse_resume_profile", side_effect=block_parse),
        ):
            comm = await connect()
            await send_command(comm, "prepare", resume_text=RESUME)
            self.assertEqual((await read(comm))["type"], "started")
            self.assertEqual((await read(comm))["state"], "running")
            await send_command(comm, "cancel")
            self.assertEqual((await read(comm))["type"], "cancelled")
            self.assertEqual((await comm.receive_output())["code"], 1000)
            await comm.wait()
            self.assertTrue(stopped.is_set())
            self.assertTrue(llm.closed)

    async def test_parse_failure_is_explicit(self):
        """解析异常保留失败语义，禁止发完成事件或泄露候选人正文，关闭失败连接。"""

        async def fail_parse(*args, **kwargs):
            """输入任意测试解析参数，抛出带敏感标记的异常；不产生可缓存候选人资料。"""
            raise ValueError("private-resume-marker")

        with (
            patch("interviews.agent_session.BackendLLM", return_value=FixtureLLM()),
            patch("interviews.agent_session.parse_resume_profile", side_effect=fail_parse),
            self.assertLogs("interviews", level="INFO") as captured,
        ):
            comm = await connect()
            await send_command(comm, "prepare", resume_text=RESUME)
            self.assertEqual((await read(comm))["type"], "started")
            self.assertEqual((await read(comm))["state"], "running")
            error = await read(comm)
            self.assertEqual(error["code"], "agent_failed")
            self.assertNotIn("private-resume-marker", json.dumps(error))
            self.assertEqual((await comm.receive_output())["code"], 1011)
            await comm.wait()
        self.assertNotIn("private-resume-marker", " ".join(captured.output))

    async def test_report_fallback_is_visible(self):
        """报告模型错误由原有报告函数回退，后端明确标记而非伪称文字模型成功。"""
        fixture = FixtureLLM()

        def fail_report(prompt, data, schema):
            """仅 ReportNarrative 抛出测试异常；其他调用保持确定性数据与真实 Agent 决策。"""
            if schema is ReportNarrative:
                raise RuntimeError("private-report-marker")
            return fixture(prompt, data, schema)

        session = AgentSession(llm=fail_report)
        command = Start(request_id=uuid4(), type="start", resume_text=RESUME, max_questions=1)
        await reserve_request(session.interview_id, command)
        first = await session.start(command)
        await complete_request(session.interview_id, command.request_id, first)
        with self.assertLogs("interviews.agent_session", level="INFO") as captured:
            command = Answer(
                request_id=uuid4(),
                type="answer",
                question_id=first["question"]["question_id"],
                answer_text=ANSWER,
            )
            await reserve_request(session.interview_id, command)
            result = await session.answer(command)
        self.assertEqual(result["result"]["report_narrative_status"], "fallback")
        self.assertAlmostEqual(result["result"]["final_report"]["overall_score"], 3.0)
        self.assertNotIn("private-report-marker", " ".join(captured.output))
        self.assertLess(result["result"]["interview_state"]["elapsed_seconds"], 120)
