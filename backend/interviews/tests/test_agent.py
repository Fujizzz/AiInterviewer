"""Agent 接入回归：真实 Agent 核心配合离线供应商和隔离测试数据库，不访问真实模型。

目录：
- AgentTests：
  使用 ASGI 消息驱动完整面试，TransactionTestCase 隔离持久化数据。
- AgentTests.connect：
  建立受相同访问策略约束的测试连接；调用方须等待终态或显式调用 disconnect。
- AgentTests.accepted：
  消费握手和公告，验证网络计时契约使用 MVP 的 120 秒。
- AgentTests.read：
  读取一条 JSON，超时意味着协议未按预期推进。
- AgentTests.command：
  发送唯一 UUID 的命令并读取第一个响应，也支持显式重复 ID。
- AgentTests.disconnect：
  发送断线并等待本地协程结束，避免测试遗留挂起任务。
- AgentTests.test_full_interview_matches_terminal_mvp：
  同样输入下，网络与原终端的题数、评价、状态预算及报告保持一致。
- AgentTests.test_invalid_commands_do_not_create_model：
  损坏 JSON、未知字段、空文本、错误类型及提前回答不触发收费请求。
- AgentTests.test_duplicate_and_stale_question_rejected_before_model_call：
  重复 start/answer 及旧问题均明确拒绝，客户端不能重复消费同一轮。
- AgentTests.test_busy_cancel_and_disconnect_release_session：
  模型未完成时拒绝第二个命令；取消和断线都取消本地任务并清理会话。
- AgentTests.test_busy_cancel_and_disconnect_release_session.slow_start：
  模拟尚未返回的上游任务，finally 证明取消已传递。
- AgentTests.test_errors_are_redacted_and_terminal：
  初始化失败或上游失败不能暴露异常正文，也不产生虚假成功。
- AgentTests.test_errors_are_redacted_and_terminal.fail：
  制造含敏感标记的异常，验证日志与响应均不回显。
- AgentTests.test_origin_size_limits_and_static_secret_protection：
  拒绝外部连接和过大消息；密钥文件不能经静态资源路由读取。
- AgentTests.test_connections_keep_candidate_state_isolated：
  两个连接分别建立 Agent 仓库，问题和面试 ID 互不串用。
- ProviderTests：
  后端配置不读取根目录秘密，保留 MVP 调用参数与异常行为。
- ProviderTests.test_missing_config_does_not_initialize_sdk：
  缺少供应商、模型或 key 时明确停止，不启用默认模型或模拟实现。
- ProviderTests.test_provider_options_and_no_root_dotenv：
  只使用已装载的后端环境，并保持 MVP 温度、60 秒超时和两次 SDK 重试。
- ProviderTests.test_inflight_client_closed_only_after_call_returns：
  取消不破坏在途同步 SDK；后台返回后才释放客户端和服务名额。
- ProviderTests.test_inflight_client_closed_only_after_call_returns.blocked：
  在可控屏障等待，让测试在调用仍执行时请求关闭。

关键变量：
（无模块级变量。）
"""

import asyncio
import json
import threading
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from uuid import uuid4

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase, TransactionTestCase

from app.application import MVPInterviewApplication
from app.providers.llm import LLMError, OpenAILLM
from interviews.agent_provider import BackendLLM
from interviews.agent_session import AgentSession
from interviews.agent_socket import MAX_MESSAGE_BYTES, agent_socket
from interviews.capacity import CapacityExceeded, take_slot

from .agent_fixtures import ANSWER, RESUME, FixtureLLM


class AgentTests(TransactionTestCase):
    """使用 ASGI 消息驱动完整面试，TransactionTestCase 隔离持久化数据。"""

    async def connect(self, origin="http://localhost", client="127.0.0.1"):
        """建立受相同访问策略约束的测试连接；调用方须等待终态或显式调用 disconnect。"""
        comm = ApplicationCommunicator(
            agent_socket,
            {
                "type": "websocket",
                "path": "/ws/agent/",
                "scheme": "ws",
                "client": (client, 12345),
                "headers": [(b"host", b"localhost"), (b"origin", origin.encode())],
            },
        )
        await comm.send_input({"type": "websocket.connect"})
        return comm

    async def accepted(self):
        """消费握手和公告，验证网络计时契约使用 MVP 的 120 秒。"""
        comm = await self.connect()
        self.assertEqual((await comm.receive_output())["type"], "websocket.accept")
        self.assertEqual((await self.read(comm))["seconds_per_question"], 120)
        return comm

    async def read(self, comm):
        """读取一条 JSON，超时意味着协议未按预期推进。"""
        return json.loads((await comm.receive_output(timeout=3))["text"])

    async def command(self, comm, kind, **fields):
        """发送唯一 UUID 的命令并读取第一个响应，也支持显式重复 ID。"""
        payload = {"type": kind, "request_id": str(uuid4()), **fields}
        await comm.send_input({"type": "websocket.receive", "text": json.dumps(payload)})
        return await self.read(comm)

    async def disconnect(self, comm):
        """发送断线并等待本地协程结束，避免测试遗留挂起任务。"""
        await comm.send_input({"type": "websocket.disconnect", "code": 1000})
        await comm.wait()

    async def test_full_interview_matches_terminal_mvp(self):
        """同样输入下，网络与原终端的题数、评价、状态预算及报告保持一致。"""
        for count in (1, 3):
            with self.subTest(count=count):
                llm = FixtureLLM()
                with patch("interviews.agent_session.BackendLLM", return_value=llm):
                    comm = await self.accepted()
                    started = await self.command(
                        comm, "start", resume_text=RESUME, max_questions=count
                    )
                    self.assertEqual(started["type"], "started")
                    message = await self.read(comm)
                    while message["type"] == "question":
                        started = await self.command(
                            comm,
                            "answer",
                            answer_text=ANSWER,
                            question_id=message["question"]["question_id"],
                        )
                        self.assertEqual(started["type"], "started")
                        message = await self.read(comm)
                    self.assertEqual(message["type"], "finished")
                    self.assertEqual((await comm.receive_output())["code"], 1000)
                    await comm.wait()
                expected = await MVPInterviewApplication(FixtureLLM()).run(
                    RESUME,
                    max_questions=count,
                    read_answer=lambda _: ANSWER,
                    write=lambda _: None,
                )
                result = message["result"]
                self.assertTrue(result["interview_finished"])
                self.assertEqual(len(result["question_history"]), count)
                self.assertEqual(result["final_report"], expected["final_report"])
                self.assertLess(result["interview_state"]["elapsed_seconds"], 120)
                self.assertEqual(result["interview_plan"]["duration_seconds"], 1800)
                self.assertEqual(
                    result["interview_state"]["competencies"],
                    expected["interview_state"]["competencies"],
                )
                self.assertEqual(
                    [q["question"] for q in result["question_history"]],
                    [q["question"] for q in expected["question_history"]],
                )
                self.assertTrue(llm.closed)

    async def test_invalid_commands_do_not_create_model(self):
        """损坏 JSON、未知字段、空文本、错误类型及提前回答不触发收费请求。"""
        with patch("interviews.agent_socket.AgentSession") as factory:
            comm = await self.accepted()
            for raw in (
                "{",
                "[]",
                '{"type":[]}',
                json.dumps({"type": "start", "request_id": str(uuid4()), "resume_text": " "}),
                json.dumps(
                    {
                        "type": "start",
                        "request_id": str(uuid4()),
                        "resume_text": RESUME,
                        "max_questions": True,
                    }
                ),
                json.dumps(
                    {
                        "type": "start",
                        "request_id": str(uuid4()),
                        "resume_text": RESUME,
                        "api_key": "must-not-accept",
                    }
                ),
            ):
                await comm.send_input({"type": "websocket.receive", "text": raw})
                self.assertEqual((await self.read(comm))["code"], "invalid_message")
            reply = await self.command(comm, "answer", question_id="old", answer_text=ANSWER)
            self.assertEqual(reply["code"], "not_started")
            factory.assert_not_called()
            await self.disconnect(comm)

    async def test_duplicate_and_stale_question_rejected_before_model_call(self):
        """重复 start/answer 及旧问题均明确拒绝，客户端不能重复消费同一轮。"""
        llm = FixtureLLM()
        with patch("interviews.agent_session.BackendLLM", return_value=llm):
            comm = await self.accepted()
            first = str(uuid4())
            await self.command(comm, "start", request_id=first, resume_text=RESUME)
            question = (await self.read(comm))["question"]["question_id"]
            calls = len(llm.calls)
            self.assertEqual(
                (await self.command(comm, "start", request_id=first, resume_text=RESUME))["code"],
                "duplicate_request",
            )
            self.assertEqual(
                (await self.command(comm, "start", resume_text=RESUME))["code"], "already_started"
            )
            self.assertEqual(
                (await self.command(comm, "answer", question_id="old", answer_text=ANSWER))["code"],
                "stale_question",
            )
            self.assertEqual(len(llm.calls), calls)
            answer_id = str(uuid4())
            await self.command(
                comm, "answer", request_id=answer_id, question_id=question, answer_text=ANSWER
            )
            await self.read(comm)
            calls = len(llm.calls)
            self.assertEqual(
                (
                    await self.command(
                        comm,
                        "answer",
                        request_id=answer_id,
                        question_id=question,
                        answer_text=ANSWER,
                    )
                )["code"],
                "duplicate_request",
            )
            self.assertEqual(
                (await self.command(comm, "answer", question_id=question, answer_text=ANSWER))[
                    "code"
                ],
                "stale_question",
            )
            self.assertEqual(len(llm.calls), calls)
            await self.disconnect(comm)

    async def test_busy_cancel_and_disconnect_release_session(self):
        """模型未完成时拒绝第二个命令；取消和断线都取消本地任务并清理会话。"""
        for cancel in (True, False):
            blocked = asyncio.Event()
            stopped = asyncio.Event()

            async def slow_start(command, blocked=blocked, stopped=stopped):
                """模拟尚未返回的上游任务，finally 证明取消已传递。"""
                try:
                    await blocked.wait()
                finally:
                    stopped.set()

            fake = Mock(spec=AgentSession)
            fake.interview_id = str(uuid4())
            fake.start = slow_start
            with patch("interviews.agent_socket.AgentSession", return_value=fake):
                comm = await self.accepted()
                await self.command(comm, "start", resume_text=RESUME)
                busy = await self.command(comm, "start", resume_text=RESUME)
                self.assertEqual(busy["code"], "busy")
                if cancel:
                    self.assertEqual((await self.command(comm, "cancel"))["type"], "cancelled")
                    self.assertEqual((await comm.receive_output())["code"], 1000)
                    await comm.wait()
                else:
                    await self.disconnect(comm)
                self.assertTrue(stopped.is_set())
                fake.close.assert_called_once()

    async def test_errors_are_redacted_and_terminal(self):
        """初始化失败或上游失败不能暴露异常正文，也不产生虚假成功。"""
        for setup in (True, False):
            fake = Mock(spec=AgentSession)
            fake.interview_id = str(uuid4())

            async def fail(command):
                """制造含敏感标记的异常，验证日志与响应均不回显。"""
                raise RuntimeError("secret-input-must-not-leak")

            fake.start = fail
            kwargs = (
                {"side_effect": LLMError("secret-input-must-not-leak")}
                if setup
                else {"return_value": fake}
            )
            with (
                patch("interviews.agent_socket.AgentSession", **kwargs),
                self.assertLogs("interviews.agent_socket", level="INFO") as logs,
            ):
                comm = await self.accepted()
                reply = await self.command(comm, "start", resume_text=RESUME)
                if not setup:
                    self.assertEqual(reply["type"], "started")
                    reply = await self.read(comm)
                self.assertEqual(reply["code"], "configuration_error" if setup else "agent_failed")
                self.assertNotIn("secret-input", json.dumps(reply))
                self.assertEqual((await comm.receive_output())["code"], 1011)
                await comm.wait()
            self.assertNotIn("secret-input", " ".join(logs.output))

    async def test_origin_size_limits_and_static_secret_protection(self):
        """拒绝外部连接和过大消息；密钥文件不能经静态资源路由读取。"""
        for kwargs in ({"origin": "https://example.com"}, {"client": "192.0.2.1"}):
            comm = await self.connect(**kwargs)
            self.assertEqual((await comm.receive_output())["type"], "websocket.close")
            await comm.wait()
        comm = await self.accepted()
        await comm.send_input({"type": "websocket.receive", "text": " " * (MAX_MESSAGE_BYTES + 1)})
        self.assertEqual((await self.read(comm))["code"], "size_limit")
        self.assertEqual((await comm.receive_output())["code"], 1009)
        await comm.wait()
        response = await self.async_client.get(
            "/stream-demo/.env", REMOTE_ADDR="127.0.0.1", HTTP_HOST="localhost"
        )
        self.assertIn(response.status_code, (403, 404))

    async def test_connections_keep_candidate_state_isolated(self):
        """两个连接分别建立 Agent 仓库，问题和面试 ID 互不串用。"""
        with patch("interviews.agent_session.BackendLLM", side_effect=FixtureLLM):
            first, second = await self.accepted(), await self.accepted()
            await self.command(first, "start", resume_text=RESUME)
            a = await self.read(first)
            await self.command(second, "start", resume_text=RESUME)
            b = await self.read(second)
            self.assertNotEqual(a["interview_id"], b["interview_id"])
            reply = await self.command(
                second, "answer", question_id=a["question"]["question_id"], answer_text=ANSWER
            )
            self.assertEqual(reply["code"], "stale_question")
            await self.disconnect(first)
            await self.disconnect(second)


class ProviderTests(SimpleTestCase):
    """后端配置不读取根目录秘密，保留 MVP 调用参数与异常行为。"""

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_config_does_not_initialize_sdk(self):
        """缺少供应商、模型或 key 时明确停止，不启用默认模型或模拟实现。"""
        with patch("interviews.agent_provider.OpenAI") as sdk:
            with self.assertRaises(LLMError):
                BackendLLM()
            sdk.assert_not_called()

    @patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-only",
            "OPENAI_MODEL": "configured-model",
            "OPENAI_TEMPERATURE": "0",
        },
        clear=True,
    )
    def test_provider_options_and_no_root_dotenv(self):
        """只使用已装载的后端环境，并保持 MVP 温度、60 秒超时和两次 SDK 重试。"""
        with (
            patch("interviews.agent_provider.OpenAI") as sdk,
            patch("app.providers.llm.load_dotenv") as dotenv,
        ):
            provider = BackendLLM()
            sdk.assert_called_once_with(api_key="test-only", timeout=30.0, max_retries=0)
            dotenv.assert_not_called()
            self.assertEqual(provider.options, {"temperature": 0.0})
            provider.close()
            sdk.return_value.close.assert_called_once()

    @patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-only",
            "OPENAI_MODEL": "configured-model",
        },
        clear=True,
    )
    def test_inflight_client_closed_only_after_call_returns(self):
        """取消不破坏在途同步 SDK；后台返回后才释放客户端和服务名额，模型用屏障模拟。"""
        entered, release = threading.Event(), threading.Event()

        def blocked(*args):
            """在可控屏障等待，让测试在调用仍执行时请求关闭。"""
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release model call")
            return "ok"

        with (
            TemporaryDirectory() as directory,
            patch("interviews.agent_provider.OpenAI") as sdk,
            patch.object(OpenAILLM, "__call__", blocked),
        ):
            provider = BackendLLM()
            provider.capacity_lease = take_slot("agent", 1, directory)
            worker = threading.Thread(target=provider, args=("", {}, FixtureLLM))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                provider.close()
                provider.capacity_lease.release()
                with self.assertRaises(CapacityExceeded):
                    take_slot("agent", 1, directory)
                sdk.return_value.close.assert_not_called()
                with self.assertRaises(LLMError):
                    provider("", {}, FixtureLLM)
            finally:
                release.set()
                worker.join(3)
            sdk.return_value.close.assert_called_once()
            take_slot("agent", 1, directory).release()
