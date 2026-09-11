"""开发测试页的资源交付模块。

目录：ASSETS（资源白名单）；demo_asset（响应构造）。
职责：提供少量固定资源；与业务 REST 接口分离，不开放任意目录文件访问。
"""

from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse

ASSETS = {"index.html", "app.js", "view.js", "media.js", "stream-client.js", "style.css"}


def demo_asset(request, name):
    """返回白名单资源，并显式禁止 HTTP 缓存。

    参数：request 为 Django 请求；name 为单一资源名，不是可遍历的相对路径。
    方法：先检查白名单，再按扩展名选择 MIME 类型，一次读取小型静态文件。
    返回：HttpResponse；不在白名单中的名称抛出 Http404。
    副作用：只读应用资源；不读取或持久化媒体数据。
    """
    if name not in ASSETS:
        raise Http404
    content_type = {
        ".html": "text/html",
        ".js": "text/javascript",
        ".css": "text/css",
    }[Path(name).suffix]
    response = HttpResponse(
        (settings.BASE_DIR / "diagnostics" / "web" / name).read_bytes(),
        content_type=content_type,
    )
    response["Cache-Control"] = "no-store"
    return response
