"""文字面试 WebSocket：命令校验、单请求执行、断线清理及脱敏错误。

目录：
- Command：
  校验 UUID 和可选阶段事件订阅，拒绝其他未知字段与隐式转换。
- Prepare：
  预解析简历，不启动题目预算；已开始的连接不可重新准备。
- Start：
  MVP 原有参数默认值；文本必须非空，题数及追问范围保持原有语义。
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
  发送稳定 error 结构并记录关联 ID；未知或无效请求用 null 标识。
- agent_socket.progress：
  为请求内阶段与先行评分事件绑定 request_id，再交给单连接 emit。
- agent_socket.run：
  调度已接收命令并在发送响应前保存结果；失败只保存固定错误状态。

关键变量：
- MAX_MESSAGE_BYTES：
  Agent 单条 JSON 文本的 UTF-8 字节上限，含简历或答案及命令字段。
- logger：
  当前模块的控制台日志入口；上下文标识及异常处理方式见相应函数。

关键状态说明：
agent_socket 内 session 属于当前连接；operation 为唯一业务任务，receiver 为接收任务。
interview_started 区分资料已准备和已开始面试；progress_events 只控制事件交付，不影响策略。
request_id 关联当前响应；seen 记录已接受执行的请求。Command.request_id 为 UUID。
数据库请求主键提供跨连接去重；输入正文不写日志；历史仍受本机同源访问策略保护。
Start 保留 MVP 默认题数、追问和岗位参数；Answer 绑定当前问题。
"""

import asyncio
import json
import logging
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .access import websocket_allowed
from .agent_records import (
    DuplicateRequest,
    PendingRequest,
    complete_request,
    fail_request,
    interrupt_interview,
    reserve_request,
)
from .agent_session import AgentSession

logger = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 262144


class Command(BaseModel):
    """输入 UUID 和 progress_events（默认 False）；后者只订阅额外事件，旧客户端响应序列不变。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_id: UUID
    progress_events: bool = False


class Prepare(Command):
    """输入非空 resume_text；保存请求及成功资料响应，不创建计划、不保存原始简历。"""

    type: Literal["prepare"]
    resume_text: str = Field(min_length=1)


class Start(Command):
    """MVP 原有参数默认值；文本必须非空，题数及追问范围保持原有语义。"""

    type: Literal["start"]
    resume_text: str = Field(min_length=1)
    max_questions: int = Field(default=5, ge=1)
    max_follow_up_per_topic: int = Field(default=2, ge=0)
    job_title: str = Field(default="General AI / Software Engineer", min_length=1)


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

    输入：scope 提供连接地址和来源；receive/send 为 ASGI 异步事件回调。
    逻辑：握手校验→公告限制→并行等待接收与当前业务任务→校验命令→返回完整结果。
    先发送 started，再调度业务协程，保证真实阶段事件不会早于请求接收确认。
    状态不变量：operation 至多一个；receiver 持续监听；seen 只收录已接受执行的请求 ID。
    普通协议错误保留连接，配置/业务/存储错误关闭 1011，大小超限关闭 1009，结束/取消关闭 1000。
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
    operation = None
    receiver = None
    request_id = None
    seen = set()
    interview_started = False

    async def emit(data):
        """将响应字典编码为单条 JSON 并发送给本连接；编码或传输异常保持传播。"""
        await send({"type": "websocket.send", "text": json.dumps(data, ensure_ascii=False)})

    async def reject(code, detail, rejected_id=None):
        """发送稳定 error 结构并记录关联 ID；未知或无效请求用 null 标识。

        code/detail 来自协议层固定消息，不能传入包含候选人输入的原始异常文本。
        仅发送错误，不决定连接是否终止；关闭策略由调用分支执行。
        """
        logger.warning(
            "Agent rejected connection=%s request=%s code=%s", connection_id, rejected_id, code
        )
        await emit({"type": "error", "request_id": rejected_id, "code": code, "detail": detail})

    async def progress(data):
        """输入服务端进度/评分事件，绑定当前 request_id；单业务任务确保请求不会交叉。

        由 Session 的事件循环协程调用，不从 SDK 工作线程发送；关闭时先取消任务，
        因此迟到的同步模型返回不能继续发送事件。发送失败原样传播。
        """
        await emit({**data, "request_id": request_id})

    async def run(command):
        """调度已接收命令并在发送响应前保存结果；失败只保存固定错误状态。

        输入为已预留数据库请求的命令；读取本连接 session，返回业务结果。
        不在数据库事务内等待模型。取消由最终清理标为 interrupted；存储失败不会返回成功。
        """
        try:
            handler = {"prepare": session.prepare, "start": session.start, "answer": session.answer}
            result = await handler[command.type](command)
            await complete_request(session.interview_id, command.request_id, result)
            return result
        except Exception:
            await fail_request(session.interview_id, command.request_id)
            raise

    await send({"type": "websocket.accept"})
    await emit(
        {
            "type": "hello",
            "connection_id": connection_id,
            "max_message_bytes": MAX_MESSAGE_BYTES,
            "seconds_per_question": 120,
            "capabilities": ["prepare", "progress", "assessment"],
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
                    logger.error(
                        "Agent failed connection=%s request=%s exception=%s; "
                        "check model-call logs and backend configuration",
                        connection_id,
                        request_id,
                        type(exc).__name__,
                    )
                    await reject(
                        "agent_failed",
                        "面试处理失败，请检查后端日志、模型配置与简历内容。",
                        request_id,
                    )
                    await send({"type": "websocket.close", "code": 1011})
                    return
                operation = None
                await emit({**result, "request_id": request_id})
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
                except Exception as exc:
                    logger.error(
                        "Agent setup failed connection=%s exception=%s; "
                        "check backend/.env provider, model, key and temperature",
                        connection_id,
                        type(exc).__name__,
                    )
                    await reject(
                        "configuration_error",
                        "请在 backend/.env 配置供应商、模型与 API key，并重启后端。",
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
                await reserve_request(session.interview_id, command)
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
