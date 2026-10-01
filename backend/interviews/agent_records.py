"""职责：在模型调用之外记录命令接收、响应提交与连接退出。

实现：同步短事务经 sync_to_async 执行；任何数据库失败向调用者传播，不降级到内存。
关联：agent_socket 在 started 前预留请求；安全网关放行后才调用 complete_request 并发送结果。

目录：
- DuplicateRequest：标识已接受过的 UUID，不透露其所属面试或输入。
- PendingRequest：标识同场面试仍有正在执行或尚未确认的请求，不能开始下一条。
- reserve_request：原子创建面试壳与请求记录，重复请求不会留下新面试。
- complete_request：原子核对安全凭据、归属与版本，保存已检查响应及校验记录。
- fail_request：以有限业务/安全错误码结束正在执行的请求并标记面试失败。
- interrupt_interview：连接退出时标记未完成请求和面试，不覆盖已保存的成功结果。

关键变量：
- logger：仅记录面试、请求、操作和生命周期，不记录输入及响应正文。
"""

import logging

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from django.utils import timezone

from ai_security.errors import SecurityContextChanged

from .agent_models import AgentInterview, AgentRequest
from .agent_safety import make_output_receipt
from .resume_models import ResumeVersion

logger = logging.getLogger(__name__)


class DuplicateRequest(Exception):
    """标识已接受过的 UUID，不透露其所属面试或输入。"""


class PendingRequest(Exception):
    """标识同场面试仍有正在执行或尚未确认的请求，不能开始下一条。"""


@sync_to_async
def reserve_request(interview_id, command, *, owner_id=None, resume_version_id=None):
    """原子创建面试壳与请求记录，重复请求不会留下新面试。

    输入为服务器面试 UUID、已验证命令和握手认证的 owner_id；默认 None 为本地旧流程。
    owner_id 不能由客户端字段提供，已有场次归属不符则拒绝。
    resume_version_id 为可选本人版本；事务内重新校验归属、状态和文本，锁定历史输入。
    旧文本路径不保存文本；版本路径将实际文本快照保存在面试，版本删除受 PROTECT 约束。
    返回 None。全局主键冲突转换为 DuplicateRequest，已有未结束请求转 PendingRequest，
    其他数据库错误保持传播。
    此步骤须在任何收费调用之前完成；同一 UUID 换内容也不会重新执行。
    """
    try:
        with transaction.atomic():
            interview, _ = AgentInterview.objects.get_or_create(
                id=interview_id, defaults={"owner_id": owner_id}
            )
            if interview.owner_id != owner_id:
                raise PermissionError("Interview does not belong to this connection")
            if interview.status not in {"preparing", "active"}:
                raise RuntimeError("Interview is no longer accepting commands")
            if command.type in {"prepare", "start"}:
                version = None
                if resume_version_id is not None:
                    version = ResumeVersion.objects.select_for_update().get(
                        pk=resume_version_id, owner_id=owner_id, status="ready"
                    )
                    if version.text != command.resume_text:
                        raise ValueError("Resume input changed")
                interview.resume_version = version
                interview.resume_text_snapshot = command.resume_text if version else ""
                interview.save(
                    update_fields=["resume_version", "resume_text_snapshot", "updated_at"]
                )
            AgentRequest.objects.create(
                id=command.request_id, interview=interview, kind=command.type
            )
            if command.type == "start":
                interview.job_title = command.job_title
                interview.save(update_fields=["job_title", "updated_at"])
    except IntegrityError as exc:
        if AgentRequest.objects.filter(id=command.request_id).exists():
            raise DuplicateRequest("Request was already accepted") from exc
        if AgentRequest.objects.filter(interview_id=interview_id, status="running").exists():
            raise PendingRequest("Interview has an unresolved request") from exc
        raise
    logger.info(
        "Agent request accepted interview=%s request=%s kind=%s",
        interview_id,
        command.request_id,
        command.type,
    )


@sync_to_async
def complete_request(interview_id, request_id, response, *, receipt, owner_id=None):
    """提交已检查响应；输入：面试/请求 ID、完整正文、安全凭据与认证归属；返回 None。

    必需 receipt 来自安全放行回调，不能由浏览器或 Agent 生成。事务内验证响应摘要、归属、
    当前版本及 running 状态；保存 _security 供历史接口验证，线上正文不包含该内部字段。
    最终报告成功保存后标为 completed；发送失败不撤销已完成工作，不重发或重试。
    """
    version = receipt.get("state_version")
    if (
        type(version) is not int
        or version < 0
        or "_security" in response
        or receipt != make_output_receipt(response, request_id, version)
    ):
        raise ValueError("invalid output approval receipt")
    now = timezone.now()
    with transaction.atomic():
        # 条件更新同时取得写锁；不在锁内等待模型，防止检查到提交间状态变化。
        matched = AgentInterview.objects.filter(
            id=interview_id,
            owner_id=owner_id,
            state_version=version,
            status__in=["preparing", "active"],
        ).update(updated_at=now)
        if matched != 1:
            raise SecurityContextChanged("output approval state changed")
        changed = AgentRequest.objects.filter(
            id=request_id, interview_id=interview_id, status="running"
        ).update(status="succeeded", response={**response, "_security": receipt}, finished_at=now)
        if changed != 1:
            raise RuntimeError("Request is not pending in this interview")
        if response["type"] == "finished":
            AgentInterview.objects.filter(id=interview_id, status="active").update(
                status="completed", closed_at=now, updated_at=now
            )
    logger.info("Agent request saved interview=%s request=%s", interview_id, request_id)


@sync_to_async
def fail_request(interview_id, request_id, *, error_code="agent_failed"):
    """以有限错误码结束请求；输入：面试/请求 ID 和固定代码；输出 None，不保存原始异常。

    默认仍为既有 agent_failed；安全失败使用专用码。已成功的请求不回退为失败。
    数据库不可用时传播异常，交由协议层记录；不会以保存成功掩盖失败。
    """
    if error_code not in {
        "agent_failed",
        "security_denied",
        "security_check_failed",
        "security_context_changed",
        "security_contract_failed",
    }:
        raise ValueError("unknown request failure code")
    now = timezone.now()
    with transaction.atomic():
        changed = AgentRequest.objects.filter(
            id=request_id, interview_id=interview_id, status="running"
        ).update(status="failed", error_code=error_code, finished_at=now)
        if changed:
            AgentInterview.objects.filter(
                id=interview_id, status__in=["preparing", "active"]
            ).update(status="failed", closed_at=now, updated_at=now)
    logger.warning("Agent request failed interview=%s request=%s", interview_id, request_id)


@sync_to_async
def interrupt_interview(interview_id):
    """连接退出时标记未完成请求和面试，不覆盖已保存的成功结果。

    输入为连接所属面试 ID；返回 None。须在本地业务任务取消并等待之后调用。
    中断不表示供应商已停止计费；进程强制退出无法执行本函数，遗留 running 不自动重放。
    """
    now = timezone.now()
    with transaction.atomic():
        AgentRequest.objects.filter(interview_id=interview_id, status="running").update(
            status="interrupted", error_code="connection_closed", finished_at=now
        )
        changed = AgentInterview.objects.filter(
            id=interview_id, status__in=["preparing", "active"]
        ).update(status="interrupted", closed_at=now, updated_at=now)
    if changed:
        logger.info("Agent interview interrupted interview=%s", interview_id)
