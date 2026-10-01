"""职责：把 Django 数据库 session 身份应用到 WebSocket，防止绕过网页登录。

实现：在容量准入前读取 Cookie、验证 session 与密码哈希；每条消息重新核验注销和到期状态。
关联：config.asgi 调用包装器；agent_socket 从 scope.user 保存创建者，原同源检查继续执行。

目录：
- session_user：在同步线程中解析 Cookie、装配 session 并调用 Django get_user。
- authenticated_socket：拒绝生产匿名连接，向下游注入经过验证的用户。
- authenticated_socket.checked_receive：在交付消息前校验会话，失效时关闭并触发原清理。

关键变量：
- logger：记录拒绝原因和用户 ID，不记录 Cookie 或认证秘密。

约束：
无 Cookie 时不读数据库；数据库错误明确失败，不退回匿名身份或重试。
已发送的同步模型请求沿用原取消边界；退出阻止该连接继续提交新消息。
"""

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.db import close_old_connections
from django.http import HttpRequest, parse_cookie

logger = logging.getLogger(__name__)


@sync_to_async
def session_user(scope):
    """输入 ASGI scope；按 Django session 规则返回用户，不信任客户端自行声明的 user ID。

    同步数据库工作在专用线程执行，前后清理失效连接；无效或过期 session 返回匿名用户。
    不吞数据库异常，不记录 Cookie 内容。
    """
    close_old_connections()
    try:
        cookies = b"; ".join(
            value for key, value in scope.get("headers", []) if key.lower() == b"cookie"
        )
        request = HttpRequest()
        request.session = SessionStore(
            session_key=parse_cookie(cookies.decode("latin1")).get(settings.SESSION_COOKIE_NAME)
        )
        return get_user(request)
    finally:
        close_old_connections()


async def authenticated_socket(app, scope, receive, send):
    """输入下游 ASGI 应用及事件函数；HTTP/生命周期原样委派，WS 先验证身份再准入。

    生产匿名握手以 1008 拒绝；本地默认模式仅允许未归属开发数据，不假造用户。
    存储故障记录异常类别并以 1011 拒绝；不输出可能包含凭据的异常正文。
    """
    if scope["type"] != "websocket":
        return await app(scope, receive, send)
    try:
        user = await session_user(scope)
    except Exception as exc:
        logger.error("WebSocket session lookup failed exception=%s", type(exc).__name__)
        if (await receive())["type"] == "websocket.connect":
            await send({"type": "websocket.close", "code": 1011})
        return
    if settings.INTERVIEW_REQUIRE_LOGIN and not user.is_authenticated:
        logger.info("Anonymous WebSocket handshake rejected")
        if (await receive())["type"] == "websocket.connect":
            await send({"type": "websocket.close", "code": 1008})
        return

    async def checked_receive():
        """读取下一事件；已登录连接在交付消息前重新认证，注销/到期后返回断线事件以清理资源。"""
        event = await receive()
        if event["type"] == "websocket.receive" and user.is_authenticated:
            try:
                current = await session_user(scope)
            except Exception as exc:
                logger.error("WebSocket session recheck failed exception=%s", type(exc).__name__)
                await send({"type": "websocket.close", "code": 1011})
                return {"type": "websocket.disconnect", "code": 1011}
            if not current.is_authenticated or current.pk != user.pk:
                logger.info("WebSocket session ended user_id=%s", user.pk)
                await send({"type": "websocket.close", "code": 1008})
                return {"type": "websocket.disconnect", "code": 1008}
        return event

    await app({**scope, "user": user}, checked_receive, send)
