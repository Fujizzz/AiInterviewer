"""职责：显式启用 PostgreSQL 与 HTTPS 反向代理部署，保留开发配置和业务参数。

实现：继承 settings 的应用装配；必填部署变量缺失时启动失败，不回退到 SQLite。
关联：deploy/systemd 与 Nginx 提供 HTTPS；Django session 负责 HTTP/WebSocket 身份。

目录：
（无本地函数或类定义。）

关键变量：
- ALLOWED_HOSTS：部署时显式指定的逗号分隔主机白名单，不接受通配符或 URL。
- DATABASES：PostgreSQL 连接；ASGI 请求不保持跨请求的持久连接。
- SECURE_PROXY_SSL_HEADER：仅信任同机 Nginx 覆写的协议头。
- SECURE_SSL_REDIRECT：HTTP 统一跳转到 HTTPS；本机检查也须声明代理协议。
- SESSION_COOKIE_SECURE：账号会话 Cookie 只通过 HTTPS 发送。
- CSRF_COOKIE_SECURE：CSRF Cookie 只通过 HTTPS 发送。
- SECURE_HSTS_SECONDS：HTTPS 响应声明一天 HSTS，不包含子域或预加载。
- SECURE_CONTENT_TYPE_NOSNIFF：禁止浏览器猜测响应类型。
- MIDDLEWARE：在继承的会话、登录门禁和 CSRF 检查后补充点击劫持保护。
- X_FRAME_OPTIONS：禁止页面被外站嵌入。
- INTERVIEW_REQUIRE_LOGIN：生产强制登录，API/面试和诊断 WebSocket 不允许匿名。

约束：
代理必须清除外部 X-Forwarded-For 并覆写 X-Forwarded-Proto；应用端口仅绑定回环。
账号使用 Django 数据库 session；写请求同时使用同源与 CSRF token 保护。
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403
from .settings import MIDDLEWARE as BASE_MIDDLEWARE

ALLOWED_HOSTS = [host.strip() for host in os.environ["DJANGO_ALLOWED_HOSTS"].split(",")]
if any(
    not host or any(char in host for char in "*/ \t\r\n") or "://" in host for host in ALLOWED_HOSTS
):
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS requires explicit hostnames or IP addresses")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ["POSTGRES_PORT"],
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"connect_timeout": 10},
    }
}
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 86400
SECURE_CONTENT_TYPE_NOSNIFF = True
MIDDLEWARE = [
    *BASE_MIDDLEWARE,
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
X_FRAME_OPTIONS = "DENY"
INTERVIEW_REQUIRE_LOGIN = True
