"""服务启动与协议分派。HTTP 交给 Django，WebSocket 交给流式诊断，lifespan 处理启停。

目录：
- handle_lifespan
- application
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
    方法：仅精确的 /ws/echo/ 路径可升级；未知 WebSocket 路径明确关闭。
    返回：异步任务结束；HTTP/流式错误由所属处理层保持既定语义。"""
    if scope["type"] == "http":
        await django_application(scope, receive, send)
    elif scope["type"] == "websocket":
        if scope["path"] == "/ws/echo/":
            await echo_socket(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})
    elif scope["type"] == "lifespan":
        await handle_lifespan(receive, send)
