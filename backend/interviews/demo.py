"""统一主页与开发测试页的资源交付模块，供 config.urls 的页面和资源路由复用。

实现：按 ASSETS 白名单读取 frontend 内文件（包括面试页本地摄像头模块），并返回明确的内容类型与禁用缓存响应。

目录：
- demo_asset：
  返回白名单资源，并显式禁止 HTTP 缓存。

关键变量：
- ASSETS：
  允许通过静态资源接口读取的文件名白名单，排除 .env 等秘密文件。
"""

from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse

ASSETS = {
    "index.html", "app.js", "view.js", "media.js", "stream-client.js", "style.css",
    "agent.html", "agent.js", "agent.css", "home.html", "home.css", "resume-pdf.js",
    "interview-camera.js",
}


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
        (settings.BASE_DIR / "frontend" / name).read_bytes(),
        content_type=content_type,
    )
    response["Cache-Control"] = "no-store"
    return response
