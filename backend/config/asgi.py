"""服务启动与协议分派。HTTP 交给 Django，WebSocket 提供流式诊断和 Agent 面试。

目录：
- handle_lifespan：
  响应 ASGI 启停握手；当前无常驻资源，确认 startup/shutdown 后正常返回。
- application：
  功能：按 ASGI scope 类型选择处理器。

关键变量：
- django_application：
  初始化后的 Django ASGI HTTP 应用，由协议分流入口调用。
"""

import os

from django.core.asgi import get_asgi_application
from interviews.streaming.websocket import echo_socket

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_application = get_asgi_application()


async def handle_lifespan(receive, send):
    """响应 ASGI 启停握手；当前无常驻资源，确认 startup/shutdown 后正常返回。"""
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def application(scope, receive, send):
    """功能：按 ASGI scope 类型选择处理器。
    方法：/ws/echo/ 传输诊断、/ws/agent/ 文字面试；未知路径明确关闭。
    返回：异步任务结束；HTTP/流式错误由所属处理层保持既定语义。"""
    if scope["type"] == "http":
        await django_application(scope, receive, send)
    elif scope["type"] == "websocket":
        if scope["path"] == "/ws/echo/":
            await echo_socket(scope, receive, send)
        elif scope["path"] == "/ws/agent/":
            from interviews.agent_socket import agent_socket

            await agent_socket(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})
    elif scope["type"] == "lifespan":
        await handle_lifespan(receive, send)
