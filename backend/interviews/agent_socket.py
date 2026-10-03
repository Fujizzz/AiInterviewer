"""文字面试 WebSocket：输入绑定、完整输出安全检查、单请求执行与断线清理。

实现：业务模型运行前绑定可信状态，所有模型结果经安全网关后保存/发送；固定控制事件独立处理。
关联：agent_safety 审查输出，agent_records 保存校验记录；不接入业务工具拦截。
answer_mcp 将同连接 MCP finish_current_answer 映射为相同的安全/持久化回答流程。

目录：
- Command：
  校验 UUID 和可选阶段事件订阅，拒绝其他未知字段与隐式转换。
- Prepare：
  预解析文本或本人简历版本，不启动题目预算；已开始的连接不可重新准备。
- Prepare.resume_source：要求文本与版本 ID 恰好提供一个。
- Start：
  校验面试分钟时长及三层题数安全上限，默认时长为 30 分钟。
- Start.exclusive_topic_budget：
  拒绝同时指定旧追问上限与新话题总题数上限。
- Start.resume_source：要求文本与版本 ID 恰好提供一个。
- Answer：
  回答必须关联当前问题，旧问题或重复请求不能再次触发模型调用。
- Cancel：
  取消当前连接的整场面试，保留历史但不自动恢复。
- parse_command：
  将单条 JSON 文本转换为 Start、Answer 或 Cancel 命令，不产生 I/O。
- agent_socket：
  管理一次本机同源文字面试的 ASGI 生命周期，不访问练习数据库。
- agent_socket.emit：
  将响应字典编码为单条 JSON 并发送给本连接；编码或传输异常保持传播。
- agent_socket.reject：
  发送业务 error 或 MCP JSON-RPC error 并记录关联 ID；未知业务请求用 null 标识。
- agent_socket.progress：
  校验固定阶段事件；评分事件必须经过行为审查后才发送。
- agent_socket.progress.deliver_assessment：将已审查评分绑定请求 ID 并发送，不提前保存终态。
- agent_socket.deliver_result：保存已审查正文与凭据；MCP 调用再包装相同正文，不改变安全摘要。
- agent_socket.run：
  绑定输入、调度 Agent、检查完整结果并保存/发送；安全异常绕过业务备用路径。

关键变量：
- MAX_MESSAGE_BYTES：
  Agent 单条 JSON 文本的 UTF-8 字节上限，含简历或答案及命令字段。
- logger：
  当前模块的控制台日志入口；上下文标识及异常处理方式见相应函数。

关键状态说明：
agent_socket 内 session/safety 属于当前连接；operation 为唯一业务任务，receiver 为接收任务。
interview_started 区分资料已准备和已开始面试；progress_events 只控制事件交付，不影响策略。
request_id 关联当前响应；seen 记录已接受执行的请求。Command.request_id 为 UUID。
mcp 为本连接 MCP 握手状态；rpc_ids 区分工具调用以包装终态/错误，工具仍映射为 Answer。
transport_failed 标记发送端异常，避免将断线误报为可继续发送的业务错误。
数据库请求主键提供跨连接去重；scope.user 来自会话认证，历史按创建用户隔离。
Start 的题数参数为安全上限，不决定时间预算；Answer 绑定当前问题。
ASGI 准入租约通过模型引用延长到实际同步调用结束；不把资源拒绝传入 Agent 触发备用出题。
"""

import asyncio
import json
import logging
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .access import websocket_allowed
from .agent_records import (
    DuplicateRequest,
    PendingRequest,
    complete_request,
    fail_request,
    interrupt_interview,
    reserve_request,
)
from .agent_safety import InterviewIOGateway, security_error_code, validate_progress
from .agent_session import AgentSession
from .answer_mcp import InterviewMCP, tool_result
from .api.resume_versions import resolve_resume_version

logger = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 262144


class Command(BaseModel):
    """输入 UUID 和 progress_events（默认 False）；后者只订阅额外事件，旧客户端响应序列不变。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_id: UUID
    progress_events: bool = False


class Prepare(Command):
    """输入非空文本或本人版本 ID；解析前由后端读取版本，不创建计划。"""

    type: Literal["prepare"]
    resume_text: str | None = Field(default=None, min_length=1)
    resume_version_id: UUID | None = None

    @model_validator(mode="after")
    def resume_source(self):
        """输入已校验字段，输出命令；要求文本或版本 ID 恰好一个，解析后的可信命令由后端填充。"""
        if (self.resume_text is None) == (self.resume_version_id is None):
            raise ValueError("Provide resume_text OR resume_version_id")
        return self


class Start(Command):
    """输入分钟时长和可选题数安全上限；默认使用 30 分钟及 Agent 配置上限。"""

    type: Literal["start"]
    resume_text: str | None = Field(default=None, min_length=1)
    resume_version_id: UUID | None = None
    duration_minutes: int = Field(default=30, ge=1)
    max_questions: int | None = Field(default=None, ge=1)
    max_follow_up_per_topic: int | None = Field(default=None, ge=0)
    max_questions_per_project: int | None = Field(default=None, ge=1)
    max_questions_per_topic: int | None = Field(default=None, ge=1)
    job_title: str = Field(default="General AI / Software Engineer", min_length=1)

    @model_validator(mode="after")
    def resume_source(self):
        """输入已校验字段，输出命令；拒绝缺失或同时提供文本/版本，保留旧文本调用行为。"""
        if (self.resume_text is None) == (self.resume_version_id is None):
            raise ValueError("Provide resume_text OR resume_version_id")
        return self

    @model_validator(mode="after")
    def exclusive_topic_budget(self):
        """拒绝新旧话题上限同时出现；旧参数仅在配置入口转换一次。"""
        if self.max_follow_up_per_topic is not None and self.max_questions_per_topic is not None:
            raise ValueError("Use max_questions_per_topic OR max_follow_up_per_topic, not both")
        return self


class Answer(Command):
    """回答必须关联当前问题，旧问题或重复请求不能再次触发模型调用。"""

    type: Literal["answer"]
    question_id: str = Field(min_length=1)
    answer_text: str = Field(min_length=1)


class Cancel(Command):
    """取消当前连接的整场面试，保留历史但不自动恢复或重放模型调用。"""

    type: Literal["cancel"]


def parse_command(raw):
    """将单条 JSON 文本转换为 Prepare、Start、Answer 或 Cancel 命令，不产生 I/O。

    前置条件：调用方已检查消息为文本且未超过字节上限。
    逻辑：解析对象并选择类型，再用严格模型校验 UUID、必填字段、额外字段及参数范围。
    返回：对应 Pydantic 命令实例；损坏 JSON、未知类型或字段错误通过异常传播。
    本函数的异常对象可能含输入信息；协议层必须转换为固定提示，不能直接序列化异常。
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    schema = {"prepare": Prepare, "start": Start, "answer": Answer, "cancel": Cancel}.get(
        data.get("type")
    )
    if schema is None:
        raise ValueError("Unknown message type.")
    return schema.model_validate_json(raw)


async def agent_socket(scope, receive, send):
    """管理一次本机同源文字面试的 ASGI 生命周期，不访问练习数据库。

    输入：scope 提供连接地址、来源和上游验证的 user；receive/send 为 ASGI 异步事件回调。
    逻辑：握手→校验命令及输入状态→Agent→安全检查完整结果→保存→发送。
    先发送 started，再调度业务协程，保证真实阶段事件不会早于请求接收确认。
    状态不变量：operation 至多一个；receiver 持续监听；seen 只收录已接受执行的请求 ID。
    普通协议错误保留连接；安全拒绝关闭 1008，检查失败/配置/业务/存储错误关闭 1011。
    大小超限关闭 1009，结束/取消关闭 1000；检查异常不送回 Agent 重试或生成备用答案。
    若接收与业务任务同时完成，先处理断线；其他命令在业务结果发布后按最新状态判断。
    退出时取消并等待本地任务，再提出客户端关闭请求；在途同步模型调用可能继续执行。
    返回 None；传输层或清理异常向 ASGI 服务器传播，本层不重连、不排队或重发计费请求。
    """
    if (await receive())["type"] != "websocket.connect":
        return
    if not websocket_allowed(scope):
        logger.warning("Agent handshake rejected: non-local peer or foreign origin")
        await send({"type": "websocket.close", "code": 1008})
        return
    connection_id = str(uuid4())
    session = None
    safety = None
    owner_id = getattr(scope.get("user"), "pk", None)
    mcp = InterviewMCP(owner_id)
    rpc_ids = set()
    operation = None
    receiver = None
    request_id = None
    seen = set()
    interview_started = False
    transport_failed = False

    async def emit(data):
        """将响应字典编码为单条 JSON 并发送给本连接；编码或传输异常保持传播。"""
        nonlocal transport_failed
        try:
            await send({"type": "websocket.send", "text": json.dumps(data, ensure_ascii=False)})
        except Exception:
            transport_failed = True
            raise

    async def reject(code, detail, rejected_id=None):
        """发送业务 error 或 MCP JSON-RPC error 并记录 ID；未知业务请求用 null 标识。

        code/detail 来自协议层固定消息，不能传入包含候选人输入的原始异常文本。
        仅发送错误，不决定连接是否终止；关闭策略由调用分支执行。
        """
        logger.warning(
            "Agent rejected connection=%s request=%s code=%s", connection_id, rejected_id, code
        )
        if rejected_id in rpc_ids:
            await emit(
                {
                    "jsonrpc": "2.0",
                    "id": rejected_id,
                    "error": {"code": -32000, "message": detail, "data": {"code": code}},
                }
            )
        else:
            await emit({"type": "error", "request_id": rejected_id, "code": code, "detail": detail})

    async def progress(data):
        """输入进度/评分字典；进度仅允许固定元数据，评分必须获准后才发送；返回 None。

        回调位于报告模型回退之外，安全失败直接终止本轮；取消传播，无缓冲后继续发送。
        """

        async def deliver_assessment(payload, receipt):
            """输入网关检查后的正文及凭据；只发送正文并绑定请求 ID，不将评分当作最终成功响应。"""
            await emit({**payload, "request_id": request_id})

        if data.get("type") == "assessment":
            await safety.publish(data, deliver_assessment)
        else:
            await emit({**validate_progress(data), "request_id": request_id})

    async def deliver_result(payload, receipt):
        """输入获准正文和凭据；核对版本后保存，再发送业务或 MCP 包装内的相同正文。
        MCP result 的 structuredContent/text 均来自获准正文；传输失败不重试或撤销提交。
        """
        await complete_request(
            session.interview_id, request_id, payload, receipt=receipt, owner_id=owner_id
        )
        message = {**payload, "request_id": request_id}
        await emit(tool_result(request_id, message) if request_id in rpc_ids else message)
        return payload

    async def run(command):
        """输入已预留命令；绑定后端输入、运行 Agent、检查并交付输出；返回已交付响应。

        安全异常发生于 Agent 自动修复路径之外；失败只保存有限错误码，未检查结果不公开。
        不在数据库事务内等待模型，取消由最终清理标记 interrupted。
        """
        try:
            command = await safety.bind_input(command)
            handler = {"prepare": session.prepare, "start": session.start, "answer": session.answer}
            result = await handler[command.type](command)
            return await safety.publish(result, deliver_result)
        except Exception as exc:
            await fail_request(
                session.interview_id, command.request_id, error_code=security_error_code(exc)
            )
            raise

    await send({"type": "websocket.accept"})
    await emit(
        {
            "type": "hello",
            "connection_id": connection_id,
            "max_message_bytes": MAX_MESSAGE_BYTES,
            "seconds_per_question": 120,
            "capabilities": ["prepare", "progress", "assessment", "answer_completion_mcp"],
        }
    )
    logger.info("Agent connected connection=%s", connection_id)
    try:
        # 接收与业务计算各自拥有一个任务；FIRST_COMPLETED 使模型等待期间仍能响应断线。
        receiver = asyncio.create_task(receive())
        while True:
            pending = {receiver} if operation is None else {receiver, operation}
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            # 断线优先，避免在同一轮得知连接已关后仍发送报告。
            if receiver in done:
                event = receiver.result()
                if event["type"] == "websocket.disconnect":
                    return
            if operation is not None and operation in done:
                try:
                    result = operation.result()
                except Exception as exc:
                    if transport_failed:
                        raise
                    error_code = security_error_code(exc)
                    logger.error(
                        "Agent failed connection=%s request=%s exception=%s; "
                        "check model-call logs and backend configuration",
                        connection_id,
                        request_id,
                        type(exc).__name__,
                    )
                    await reject(
                        error_code,
                        "输出未通过安全检查或检查未完成，本次面试已停止。"
                        if error_code.startswith("security_")
                        else "面试处理失败，请检查后端日志、模型配置与简历内容。",
                        request_id,
                    )
                    await send(
                        {
                            "type": "websocket.close",
                            "code": 1008 if error_code == "security_denied" else 1011,
                        }
                    )
                    return
                operation = None
                logger.info(
                    "Agent response connection=%s request=%s type=%s",
                    connection_id,
                    request_id,
                    result["type"],
                )
                if result["type"] == "finished":
                    await send({"type": "websocket.close", "code": 1000})
                    return
            if receiver not in done:
                continue
            receiver = asyncio.create_task(receive())
            raw = event.get("text")
            if raw is None:
                await reject("invalid_message", "Agent 仅接受 JSON 文本消息。")
                continue
            if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await reject("size_limit", "消息超过 256 KiB 限制。")
                await send({"type": "websocket.close", "code": 1009})
                return
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and "jsonrpc" in data:
                    try:
                        reply, normalized = mcp.handle(data)
                    except (ValueError, TypeError, AttributeError):
                        await emit(
                            {
                                "jsonrpc": "2.0",
                                "id": data.get("id"),
                                "error": {
                                    "code": -32602,
                                    "message": "Invalid MCP request or completion receipt.",
                                },
                            }
                        )
                        continue
                    if reply is not None:
                        await emit(reply)
                    if normalized is None:
                        continue
                    raw = json.dumps(normalized)
                    rpc_ids.add(normalized["request_id"])
                command = parse_command(raw)
            except (ValueError, TypeError, ValidationError):
                await reject("invalid_message", "检查消息类型、UUID、必填字段及参数类型。")
                continue
            incoming_id = str(command.request_id)
            # 取消针对整场会话，不受 busy 或去重限制；退出路径统一撤销正在执行的本地任务。
            if isinstance(command, Cancel):
                await emit({"type": "cancelled", "request_id": incoming_id})
                await send({"type": "websocket.close", "code": 1000})
                return
            if incoming_id in seen:
                await reject("duplicate_request", "此 request_id 已处理，请勿重发。", incoming_id)
                continue
            if operation is not None:
                await reject("busy", "上一请求仍在处理中。", incoming_id)
                continue
            if isinstance(command, (Prepare, Start)):
                if interview_started:
                    await reject("already_started", "每个连接只能初始化一次面试。", incoming_id)
                    continue
                try:
                    if session is None:
                        session = AgentSession()
                        safety = InterviewIOGateway(
                            session.interview_id, owner_id=owner_id, connection_id=connection_id
                        )
                        if hasattr(getattr(session, "llm", None), "capacity_lease"):
                            session.llm.capacity_lease = scope.get("interview.capacity_lease")
                except Exception as exc:
                    logger.error(
                        "Agent setup failed connection=%s exception=%s; "
                        "check repository-root .env provider, model, key and temperature",
                        connection_id,
                        type(exc).__name__,
                    )
                    await reject(
                        "configuration_error",
                        "请在项目根目录 .env 配置供应商、模型与 API key，并重启后端。",
                        incoming_id,
                    )
                    await send({"type": "websocket.close", "code": 1011})
                    return
                logger.info(
                    "Agent initialized connection=%s interview=%s",
                    connection_id,
                    session.interview_id,
                )
            else:
                if session is None or session.action is None:
                    await reject("not_started", "请先发送 start 并等待问题。", incoming_id)
                    continue
                if (
                    session.action.question is None
                    or command.question_id != session.action.question.question_id
                ):
                    await reject("stale_question", "请回答服务端返回的当前问题。", incoming_id)
                    continue
            try:
                resume_version_id = getattr(command, "resume_version_id", None)
                command = await resolve_resume_version(command, owner_id)
            except ValueError:
                await reject(
                    "resume_unavailable",
                    "简历版本不存在、不属于当前用户或尚未解析完成。",
                    incoming_id,
                )
                continue
            try:
                await reserve_request(
                    session.interview_id,
                    command,
                    owner_id=getattr(scope.get("user"), "pk", None),
                    resume_version_id=resume_version_id,
                )
            except DuplicateRequest:
                await reject("duplicate_request", "此 request_id 已接收，请勿重发。", incoming_id)
                continue
            except PendingRequest:
                await reject("busy", "面试仍有未完成请求，不能开始下一请求。", incoming_id)
                continue
            except Exception as exc:
                logger.error(
                    "Agent storage unavailable connection=%s exception=%s; "
                    "check migrations and database",
                    connection_id,
                    type(exc).__name__,
                )
                await reject("storage_unavailable", "请求未执行，请检查数据库与迁移。", incoming_id)
                await send({"type": "websocket.close", "code": 1011})
                return
            if isinstance(command, Start):
                interview_started = True
            work = run(command)
            seen.add(incoming_id)
            request_id = incoming_id
            session.emit_event = progress if command.progress_events else None
            try:
                await emit({"type": "started", "request_id": request_id, "operation": command.type})
            except (Exception, asyncio.CancelledError):
                # 尚未调度的协程没有机会进入 finally，必须显式关闭，防止发送失败时遗留。
                work.close()
                raise
            operation = asyncio.create_task(work)
    finally:
        # 先等待异步取消生效，再关闭模型入口，避免后续步骤继续使用将要释放的客户端。
        tasks = [task for task in (operation, receiver) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if session is not None:
            try:
                await interrupt_interview(session.interview_id)
            except Exception as exc:
                logger.error(
                    "Agent cleanup storage failed interview=%s exception=%s; "
                    "pending requests require review",
                    session.interview_id,
                    type(exc).__name__,
                )
                raise
            finally:
                session.close()
        logger.info("Agent disconnected connection=%s", connection_id)
