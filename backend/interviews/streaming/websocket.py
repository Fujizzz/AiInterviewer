"""ASGI WebSocket 生命周期与协议调度。

目录：
- send_json：
  将响应字典编码为紧凑 JSON，并交给 ASGI send；不缓存响应正文。
- dispatch_control：
  将控制消息映射为协议响应。
- echo_socket：
  执行单连接回传循环，按 ACK → 原始二进制消息顺序输出。

关键变量：
- logger：
  当前模块的控制台日志入口；上下文标识及异常处理方式见相应函数。
"""

import asyncio
import json
import logging
import time

from ..access import websocket_allowed
from .protocol import IDLE_TIMEOUT_SECONDS, EchoState, ProtocolError, parse_control

logger = logging.getLogger(__name__)


async def send_json(send, data):
    """将响应字典编码为紧凑 JSON，并交给 ASGI send；不缓存响应正文。"""
    await send({"type": "websocket.send", "text": json.dumps(data, separators=(",", ":"))})


def dispatch_control(state, message):
    """将控制消息映射为协议响应。

    参数：state 为当前连接状态，message 为已通过 JSON 校验的字典。
    方法：ping 验证关联 ID；start/finish 委派状态对象；未知类型显式失败。
    返回：响应字典。是否关闭连接由主循环根据响应类型判断。
    """
    kind = message.get("type")
    if kind == "ping":
        ident = message.get("id")
        if not isinstance(ident, str) or not 1 <= len(ident) <= 64:
            raise ProtocolError("invalid_ping", "ping.id must be a string of 1-64 characters.")
        return {"type": "pong", "id": ident, "server_time_ms": time.time_ns() // 1000000}
    if kind == "start":
        response = state.start(message)
        logger.info("Stream started connection=%s mode=%s", state.connection_id, state.mode)
        return response
    if kind == "finish":
        return state.finish(message)
    raise ProtocolError("unknown_type", "Supported control types: ping, start, finish.")


async def echo_socket(scope, receive, send):
    """执行单连接回传循环，按 ACK → 原始二进制消息顺序输出。

    输入：ASGI scope 以及异步 receive/send 回调。
    逻辑：验证握手→公告限制→接收控制或二进制帧→完成或显式关闭。
    异常：协议错误返回 error 和相应关闭码；系统错误记录上下文并以 1011 关闭。
    副作用：仅网络发送和控制台日志；日志不包含音视频正文。
    """
    event = await receive()
    if event["type"] != "websocket.connect":
        return
    if not websocket_allowed(scope):
        logger.warning("Stream handshake rejected: non-local peer or foreign origin")
        await send({"type": "websocket.close", "code": 1008})
        return

    state = EchoState()
    try:
        await send({"type": "websocket.accept"})
        logger.info("Stream opened connection=%s", state.connection_id)
        await send_json(send, state.hello())
        while True:
            try:
                event = await asyncio.wait_for(receive(), timeout=IDLE_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise ProtocolError(
                    "idle_timeout",
                    "No messages received for 30 seconds.",
                ) from exc
            if event["type"] == "websocket.disconnect":
                break
            if event["type"] != "websocket.receive":
                continue
            binary = event.get("bytes")
            if binary is not None:
                await send_json(send, state.accept_chunk(binary))
                await send({"type": "websocket.send", "bytes": binary})
                continue
            response = dispatch_control(state, parse_control(event.get("text", "")))
            await send_json(send, response)
            if response["type"] == "finished":
                await send({"type": "websocket.close", "code": 1000})
                break
    except ProtocolError as exc:
        state.status, state.error_code = "error", exc.code
        logger.warning(
            "Stream protocol rejected connection=%s code=%s chunks=%d bytes=%d",
            state.connection_id,
            exc.code,
            state.chunk_count,
            state.byte_count,
        )
        await send_json(send, {"type": "error", "code": exc.code, "detail": exc.detail})
        await send({"type": "websocket.close", "code": exc.close_code})
    except (OSError, asyncio.CancelledError):
        logger.info("Stream transport closed connection=%s", state.connection_id)
        raise
    except Exception:
        state.status, state.error_code = "error", "internal_error"
        logger.exception("Stream failure connection=%s; inspect ASGI server", state.connection_id)
        await send({"type": "websocket.close", "code": 1011})
    finally:
        logger.info(
            "Stream closed connection=%s status=%s chunks=%d bytes=%d",
            state.connection_id,
            state.status,
            state.chunk_count,
            state.byte_count,
        )
