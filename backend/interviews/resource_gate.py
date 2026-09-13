"""职责：在上传正文读取和 Agent 握手前执行服务容量及上传传输字节限制。

实现：ASGI 包装器取得跨进程租约，完成或取消后释放；同步模型可延后归还连接名额。
关联：config.asgi 包装原路由；agent_socket 把私有 scope 租约交给后端模型适配器。

目录：
- reject_http：发送固定 JSON 错误与禁止缓存头，不回显请求。
- limited_application：仅限制 PDF POST 和 Agent WebSocket，其他接口按原路由运行。
- limited_application.bounded_receive：累计上传 ASGI 分片，超出传输上限时停止交付正文。
- limited_application.observed_send：记录 HTTP 响应开始，避免发送第二组响应头。
- UploadTooLarge：正文累计超限时中断下游上传解析。

关键变量：
- logger：记录拒绝类别，不记录文件名、正文、Origin 或密钥。

配置说明：
SERVICE_AGENT_CONNECTIONS 默认 4，SERVICE_PDF_UPLOADS 默认 2；均是新增服务安全限额。
PDF_BODY_BYTES 为 10 MiB 文件加 64 KiB multipart 包装的传输上限，不修改文件本身上限。
满额不排队、不触发模型、不执行隐式回退。连接上限包括空闲面试和取消后仍在途的同步调用。
"""

import json
import logging
import os

from .capacity import CapacityExceeded, take_slot
from .resume_pdf import MAX_BYTES

logger = logging.getLogger(__name__)


class UploadTooLarge(Exception):
    """正文累计超限时中断下游上传解析；仅携带固定错误，不含正文。"""


async def reject_http(send, status, code):
    """发送固定 JSON 错误与禁止缓存头，不回显请求；返回 None，不重试发送。"""
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": json.dumps({"error": code}).encode()})


async def limited_application(app, scope, receive, send):
    """仅限制 PDF POST 和 Agent WebSocket，其他接口按原路由运行。

    输入为下游 ASGI 应用与标准事件函数。租约在读取正文前取得，防止 Django 先缓存整个上传。
    内容长度只作提前拒绝，实际仍累计分片；配置和锁文件错误明确返回 503/1013，不降级。
    """
    is_pdf = (
        scope["type"] == "http"
        and scope.get("path") == "/api/resume/parse/"
        and scope.get("method") == "POST"
    )
    is_agent = scope["type"] == "websocket" and scope.get("path") == "/ws/agent/"
    if not (is_pdf or is_agent):
        return await app(scope, receive, send)
    body_limit = MAX_BYTES + 65536
    resource = "pdf" if is_pdf else "agent"
    option = "SERVICE_PDF_UPLOADS" if is_pdf else "SERVICE_AGENT_CONNECTIONS"
    try:
        lease = take_slot(resource, int(os.getenv(option, "2" if is_pdf else "4")))
    except (CapacityExceeded, OSError, ValueError):
        logger.warning("Resource admission rejected resource=%s", resource)
        if is_pdf:
            await reject_http(send, 503, "service_capacity_unavailable")
        elif (await receive())["type"] == "websocket.connect":
            await send({"type": "websocket.close", "code": 1013})
        return
    received = 0
    response_started = False

    async def bounded_receive():
        """累计上传 ASGI 分片，超出传输上限时停止交付正文；断线事件原样传递。"""
        nonlocal received
        event = await receive()
        if event["type"] == "http.request":
            received += len(event.get("body", b""))
            if received > body_limit:
                raise UploadTooLarge("PDF upload body exceeded its limit")
        return event

    async def observed_send(event):
        """记录 HTTP 响应开始，避免发送第二组响应头；其他事件及异常原样传递。"""
        nonlocal response_started
        if event["type"] == "http.response.start":
            response_started = True
        await send(event)

    try:
        if is_pdf:
            lengths = [v for k, v in scope.get("headers", []) if k.lower() == b"content-length"]
            if lengths and (len(lengths) != 1 or not lengths[0].isdigit()):
                return await reject_http(send, 400, "invalid_content_length")
            if lengths:
                # 比较十进制字节串，避免超长数字触发 Python 的整数转换异常。
                length = lengths[0].lstrip(b"0") or b"0"
                maximum = str(body_limit).encode()
                if len(length) > len(maximum) or (len(length) == len(maximum) and length > maximum):
                    return await reject_http(send, 413, "pdf_upload_too_large")
        await app(
            {**scope, "interview.capacity_lease": lease},
            bounded_receive if is_pdf else receive,
            observed_send,
        )
    except UploadTooLarge:
        if response_started:
            raise
        await reject_http(send, 413, "pdf_upload_too_large")
    finally:
        lease.release()
