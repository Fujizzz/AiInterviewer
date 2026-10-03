"""统一主页与开发测试页的资源交付模块，供 config.urls 的页面和资源路由复用。

实现：按 ASSETS 白名单读取 frontend、数字人 dist 构建及 diagnostics 内文件，
HTML 通过 Django 模板加入账号导航与 CSRF token，其他资源返回明确类型和禁止缓存响应。
个人中心和版本面试页无论本地门禁设置如何都要求登录，静态 HTML 别名执行相同校验。

目录：
- demo_asset：
  返回白名单资源，并显式禁止 HTTP 缓存。

关键变量：
- ASSETS：
  允许通过静态资源接口读取的文件名白名单，排除 .env 等秘密文件。
"""

from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse
from django.shortcuts import render

ASSETS = {
    "index.html",
    "app.js",
    "view.js",
    "media.js",
    "stream-client.js",
    "style.css",
    "agent.html",
    "agent.js",
    "interview-progress.js",
    "interview-history.js",
    "interview-history.css",
    "agent.css",
    "home.html",
    "home.css",
    "interview-camera.js",
    "i18n.js",
    "account.css",
    "interview-voice.js",
    "speech-capture.js",
    "speech-worklet.js",
    "pcm-resampler.js",
    "pixel-player.js",
    "digital-human-check.html",
    "digital-human-check.js",
    "resumes.html",
    "resumes.js",
    "resumes.css",
}


def demo_asset(request, name):
    """返回白名单资源，并显式禁止 HTTP 缓存。

    参数：request 为 Django 请求；name 为单一资源名，不是可遍历的相对路径。
    方法：先检查白名单；个人中心/面试 HTML 未登录跳转 /login/，HTML 渲染已转义账号上下文。
    返回：HttpResponse 或登录重定向；不在白名单中或资源/构建不存在时抛出 Http404。
    副作用：只读应用资源；不读取或持久化媒体数据。
    """
    if name not in ASSETS:
        raise Http404
    if name in {"resumes.html", "agent.html"} and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), login_url="/login/")
    content_type = {
        ".html": "text/html",
        ".js": "text/javascript",
        ".css": "text/css",
    }[Path(name).suffix]
    asset = settings.BASE_DIR / "frontend" / name
    if name == "pixel-player.js":
        asset = settings.BASE_DIR / "frontend" / "digital-human" / "dist" / name
    elif name in {"digital-human-check.html", "digital-human-check.js"}:
        asset = settings.BASE_DIR / "diagnostics" / name
    if not asset.is_file():
        raise Http404("Build backend/frontend/digital-human before connecting the avatar.")
    response = (
        render(request, name)
        if Path(name).suffix == ".html"
        else HttpResponse(
            asset.read_bytes(),
            content_type=content_type,
        )
    )
    response["Cache-Control"] = "no-store"
    return response
