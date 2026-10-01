"""职责：验证真实 ASGI、输入绑定、输出检查、数据库及历史公开边界的闭环。
实现：仅替换业务模型和安全模型；保留实际状态机、网关、事务、协议及 HTTP 历史处理。
关联：agent_safety/agent_socket/agent_records/agent_history；复用已有确定性业务测试资料。

目录：
- ScriptedReviewer：可控审查端口，覆盖拒绝、异常、取消和完整覆盖验证。
- ScriptedReviewer.__init__：记录目标输出、故障模式和同步屏障。
- ScriptedReviewer.assess：记录真实审查请求，按模式给出结论或等待取消。
- IOSafetyTests：隔离数据库中的输入输出闭环测试，不证明真实模型检测效果。
- IOSafetyTests.setUp：注入显式业务/安全替身，注册清理。
- IOSafetyTests.terminal：读取进度直至业务结果或固定错误。
- IOSafetyTests.close_error：验证终止关闭及无后续输出。
- IOSafetyTests.test_allowed_round_trip_and_sources：验证四种输出、整包审查、来源标签和历史一致。
- IOSafetyTests.test_denial_at_each_output：逐出口拒绝，不保存成功正文且历史不可绕过。
- IOSafetyTests.test_review_failures_stop_output：异常、超时和不完整结果不放行、不重试。
- IOSafetyTests.test_real_deadline_stops_output：等待真实五秒预算，验证任务取消与错误关闭。
- IOSafetyTests.test_cancel_or_disconnect_during_review：审查挂起时取消/断线，不产生延迟输出。
- IOSafetyTests.test_input_budget_precedes_business_model：输入超预算在任何业务模型调用前拒绝。
- IOSafetyTests.test_ownership_and_commit_version：归属不符不能绑定，过期凭据不能提交。
- IOSafetyTests.test_history_requires_intact_receipt：未检和篡改结果不能经两类历史接口公开。
- IOSafetyTests.test_progress_cannot_carry_model_text：固定进度字段不能夹带任意文本。

关键变量：
（无模块级变量。）

约束说明：
替身只证明边界被调用和执行语义，不是攻击召回率、模型性能或供应商取消验证。
ScriptedReviewer.requests 保存虚构输入，仅供测试断言；不进入生产日志或配置。
"""

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

from django.test import TransactionTestCase
from django.utils import timezone

from ai_security.errors import SecurityContextChanged
from interviews.agent_models import AgentInterview, AgentRequest
from interviews.agent_records import complete_request, reserve_request
from interviews.agent_safety import (
    InterviewIOGateway,
    IOSafetyError,
    make_output_receipt,
    validate_progress,
)
from interviews.agent_socket import Prepare
from shared.contracts.behavior import BehaviorAssessment

from .agent_fixtures import ANSWER, RESUME, FixtureBehaviorReviewer, FixtureLLM
from .test_agent_progress import connect, disconnect, send_command


class ScriptedReviewer:
    """功能：可控审查故障；逻辑：按操作选择结果；约束：模拟端口而非真实安全判断。"""

    def __init__(self, target=None, mode="allow"):
        """输入目标操作和模式；初始化请求记录及进入/取消事件，不联网。"""
        self.target, self.mode = target, mode
        self.requests = []
        self.entered, self.cancelled = asyncio.Event(), asyncio.Event()

    async def assess(self, request):
        """输入真实请求；记录副本，返回完整/不完整结论或注入故障；等待模式仅由取消结束。"""
        self.requests.append(request.model_copy(deep=True))
        requirements = tuple(r.requirement_id for r in request.boundary.requirements)
        if self.target in (None, request.proposal.operation):
            if self.mode == "wait":
                self.entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled.set()
            if self.mode == "error":
                raise RuntimeError("private-provider-body")
            if self.mode == "timeout":
                raise TimeoutError("private-provider-body")
            if self.mode == "incomplete":
                requirements = requirements[:1]
            if self.mode == "uncertain":
                return BehaviorAssessment(
                    verdict="uncertain",
                    checked_requirement_ids=requirements,
                    violated_requirement_ids=(),
                )
            if self.mode == "deny":
                return BehaviorAssessment(
                    verdict="noncompliant",
                    checked_requirement_ids=requirements,
                    violated_requirement_ids=("TASK_SCOPE",),
                )
        return BehaviorAssessment(
            verdict="compliant", checked_requirement_ids=requirements, violated_requirement_ids=()
        )


class IOSafetyTests(TransactionTestCase):
    """功能：回归真实闭环；逻辑：独立数据库和可控端口；约束：不改变生产模型参数和五秒预算。"""

    def setUp(self):
        """无外部输入；逐测试构造替身并注册 patch 清理；端口之外使用真实实现。"""
        super().setUp()
        self.reviewer, self.llm = ScriptedReviewer(), FixtureLLM()
        for name, instance in (
            ("interviews.agent_session.BackendLLM", self.llm),
            ("interviews.agent_safety.create_behavior_reviewer", self.reviewer),
        ):
            patcher = patch(name, return_value=instance)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def terminal(self, comm, kind, *, timeout=3):
        """输入通道、期望类型和测试等待时间；跳过固定控制消息，错误立即返回供断言。"""
        for _ in range(50):
            packet = await comm.receive_output(timeout=timeout)
            self.assertEqual(packet["type"], "websocket.send")
            value = json.loads(packet["text"])
            if value["type"] in (kind, "error"):
                return value
            self.assertIn(value["type"], {"started", "progress", "assessment"})
        self.fail("missing terminal response")

    async def close_error(self, comm, response, code):
        """输入终态错误及预期代码；检查固定消息、关闭码、任务结束和发送队列空。"""
        self.assertEqual(response["type"], "error")
        self.assertEqual(response["code"], code)
        self.assertNotIn("private-provider-body", json.dumps(response))
        self.assertEqual(
            (await comm.receive_output())["code"], 1008 if code == "security_denied" else 1011
        )
        await comm.wait()
        self.assertTrue(comm.output_queue.empty())

    async def test_allowed_round_trip_and_sources(self):
        """虚构简历→两道问题/评价→先行评分→报告；完整受检正文与网络/历史相同，来源不混淆。"""
        comm = await connect()
        await send_command(comm, "prepare", resume_text=RESUME)
        self.assertEqual((await self.terminal(comm, "prepared"))["type"], "prepared")
        await send_command(comm, "start", resume_text=RESUME, max_questions=2)
        first = await self.terminal(comm, "question")
        await send_command(
            comm, "answer", question_id=first["question"]["question_id"], answer_text=ANSWER
        )
        second = await self.terminal(comm, "question")
        self.assertIsNotNone(second["last_evaluation"])
        rid = await send_command(
            comm, "answer", question_id=second["question"]["question_id"], answer_text=ANSWER
        )
        finished = await self.terminal(comm, "finished")
        self.assertEqual((await comm.receive_output())["code"], 1000)
        await comm.wait()
        self.assertEqual(
            [r.proposal.operation for r in self.reviewer.requests],
            [
                "publish_prepared",
                "publish_question",
                "publish_question",
                "publish_assessment",
                "publish_finished",
            ],
        )
        question_check = self.reviewer.requests[2]
        self.assertEqual(
            json.loads(question_check.proposal.content.text),
            {k: v for k, v in second.items() if k != "request_id"},
        )
        self.assertIn("user", [e.source for e in question_check.evidence])
        self.assertEqual(
            [e.source for e in self.reviewer.requests[1].evidence][:2], ["resume", "job"]
        )
        self.assertEqual(question_check.boundary.session_id, first["interview_id"])
        base = f"/api/agent-interviews/{first['interview_id']}/"
        detail = (await self.async_client.get(base)).json()
        self.assertEqual(detail["final_report"], finished["result"]["final_report"])
        self.assertEqual(detail["questions"][0]["answer"]["evaluation"], second["last_evaluation"])
        saved = await AgentRequest.objects.aget(id=rid)
        self.assertIn("_security", saved.response)
        public = (await self.async_client.get(f"{base}requests/{rid}/")).json()
        self.assertNotIn("_security", public["response"])
        self.assertEqual(public["response"]["result"], finished["result"])

    async def test_denial_at_each_output(self):
        """逐一拒绝四类业务输出；Agent 内部可能已提交，但拒绝正文不可公开。"""
        for kind in ("prepared", "question", "assessment", "finished"):
            with self.subTest(kind=kind):
                self.reviewer.target, self.reviewer.mode = f"publish_{kind}", "deny"
                comm = await connect()
                rid = await send_command(
                    comm,
                    "prepare" if kind == "prepared" else "start",
                    resume_text=RESUME,
                    **({} if kind == "prepared" else {"max_questions": 1}),
                )
                if kind in {"assessment", "finished"}:
                    first = await self.terminal(comm, "question")
                    rid = await send_command(
                        comm,
                        "answer",
                        question_id=first["question"]["question_id"],
                        answer_text=ANSWER,
                    )
                failure = await self.terminal(comm, kind)
                await self.close_error(comm, failure, "security_denied")
                saved = await AgentRequest.objects.aget(id=rid)
                self.assertEqual(saved.status, "failed")
                self.assertEqual(saved.error_code, "security_denied")
                self.assertIsNone(saved.response)
                base = f"/api/agent-interviews/{saved.interview_id}/"
                detail = (await self.async_client.get(base)).json()
                self.assertIsNone(detail["final_report"])
                if kind in {"prepared", "question"}:
                    self.assertEqual(detail["questions"], [])
                    self.assertIsNone(detail["candidate_profile"])
                else:
                    self.assertIsNone(detail["questions"][0]["answer"]["evaluation"])
                self.assertIsNone(
                    (await self.async_client.get(f"{base}requests/{rid}/")).json()["response"]
                )

    async def test_review_failures_stop_output(self):
        """供应商异常、主动 TimeoutError、不确定及覆盖不全均失败关闭；一次请求仅检查一次。"""
        for mode in ("error", "timeout", "uncertain", "incomplete"):
            with self.subTest(mode=mode):
                self.reviewer.mode = mode
                calls = len(self.reviewer.requests)
                comm = await connect()
                rid = await send_command(comm, "prepare", resume_text=RESUME)
                await self.close_error(
                    comm, await self.terminal(comm, "prepared"), "security_check_failed"
                )
                self.assertEqual(len(self.reviewer.requests), calls + 1)
                self.assertIsNone((await AgentRequest.objects.aget(id=rid)).response)

    async def test_real_deadline_stops_output(self):
        """实际等待网关既有五秒 deadline，不缩短生产策略；到期取消审查且不交付正文。"""
        self.reviewer.mode = "wait"
        comm = await connect()
        await send_command(comm, "prepare", resume_text=RESUME)
        await self.close_error(
            comm, await self.terminal(comm, "prepared", timeout=8), "security_check_failed"
        )
        self.assertTrue(self.reviewer.cancelled.is_set())
        self.assertEqual(len(self.reviewer.requests), 1)

    async def test_cancel_or_disconnect_during_review(self):
        """由同步事件确认已进入检查，再取消或断线；本地审查结束且请求中断，无延迟正文。"""
        for cancel in (True, False):
            self.reviewer.mode = "wait"
            self.reviewer.entered.clear()
            self.reviewer.cancelled.clear()
            comm = await connect()
            rid = await send_command(comm, "prepare", resume_text=RESUME, progress_events=False)
            self.assertEqual(json.loads((await comm.receive_output())["text"])["type"], "started")
            await asyncio.wait_for(self.reviewer.entered.wait(), timeout=3)
            if cancel:
                await send_command(comm, "cancel")
                self.assertEqual(
                    json.loads((await comm.receive_output())["text"])["type"], "cancelled"
                )
                self.assertEqual((await comm.receive_output())["code"], 1000)
                await comm.wait()
            else:
                await disconnect(comm)
            self.assertTrue(self.reviewer.cancelled.is_set())
            self.assertEqual((await AgentRequest.objects.aget(id=rid)).status, "interrupted")
            self.assertTrue(comm.output_queue.empty())

    async def test_input_budget_precedes_business_model(self):
        """仍低于协议字节限制、但超过既有安全字符预算的输入明确拒绝，不调用业务模型或检测模型。"""
        comm = await connect()
        await send_command(comm, "prepare", resume_text="x" * 100001)
        await self.close_error(
            comm, await self.terminal(comm, "prepared"), "security_contract_failed"
        )
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.reviewer.requests, [])

    async def test_ownership_and_commit_version(self):
        """真实数据库归属不符不能绑定；与记录版本不符的批准凭据也不能进入成功状态。"""
        iid = uuid4()
        command = Prepare(type="prepare", request_id=uuid4(), resume_text=RESUME)
        await reserve_request(iid, command)
        gateway = InterviewIOGateway(
            iid, owner_id=987654, connection_id="test", reviewer=FixtureBehaviorReviewer()
        )
        with self.assertRaises(IOSafetyError):
            await gateway.bind_input(command)
        payload = {"type": "prepared", "candidate_profile": {}}
        with self.assertRaises(SecurityContextChanged):
            await complete_request(
                iid,
                command.request_id,
                payload,
                receipt=make_output_receipt(payload, command.request_id, 1),
            )
        self.assertEqual((await AgentRequest.objects.aget(id=command.request_id)).status, "running")

    async def test_history_requires_intact_receipt(self):
        """旧无凭据及篡改结果均隐藏；验证成功时只公开获准副本，不公开原始 context。"""
        interview = await AgentInterview.objects.acreate()
        payload = {"type": "prepared", "candidate_profile": {"name": "approved-person"}}
        record = await AgentRequest.objects.acreate(
            id=uuid4(),
            interview=interview,
            kind="prepare",
            status="succeeded",
            response=payload,
            finished_at=timezone.now(),
        )
        base = f"/api/agent-interviews/{interview.id}/"
        for mode in ("legacy", "approved", "tampered"):
            if mode != "legacy":
                record.response = {
                    **payload,
                    "_security": make_output_receipt(payload, record.id, 0),
                }
                if mode == "tampered":
                    record.response["candidate_profile"] = {"name": "unreviewed-secret"}
                await record.asave(update_fields=["response"])
            detail = (await self.async_client.get(base)).json()
            saved = (await self.async_client.get(f"{base}requests/{record.id}/")).json()
            self.assertEqual(saved["security_output_available"], mode == "approved")
            self.assertEqual(
                detail["candidate_profile"],
                payload["candidate_profile"] if mode == "approved" else None,
            )
            self.assertNotIn("unreviewed-secret", json.dumps([saved, detail]))

    def test_progress_cannot_carry_model_text(self):
        """保留固定阶段/状态及非负耗时，未知字段、阶段或正文不能借进度通道绕过模型审查。"""
        valid = {"type": "progress", "stage": "resume_parsing", "state": "running"}
        self.assertEqual(validate_progress(valid), valid)
        for extra in ({"text": "unreviewed"}, {"stage": "model text"}, {"duration_ms": True}):
            with self.assertRaises(IOSafetyError):
                validate_progress({**valid, **extra})
