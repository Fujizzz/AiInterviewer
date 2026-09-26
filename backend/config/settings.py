"""后端配置。集中声明应用、SQLite、JSON 接口和控制台日志；密钥必须由环境提供。

目录：
（无本地函数或类定义。）

关键变量：
- PDF_TASK_EXECUTION：开发默认 inline；显式 celery 模式使用 Redis，不自动回退。
- CELERY_BROKER_URL：Celery 私有 Redis 队列地址。
- PDF_TASK_REDIS_URL：PDF 短期正文、进度和取消标记的私有 Redis 地址。
- ALLOWED_HOSTS：
  本机 HTTP Host 白名单，与访问中间件共同限制服务入口。
- ASGI_APPLICATION：
  ASGI 协议入口的导入路径。
- BASE_DIR：
  backend 目录绝对路径，用于定位 .env、SQLite 默认路径和诊断页资源。
- DATABASES：
  SQLite 连接配置；环境可显式指定路径，锁等待超时保留既定值。
- DEBUG：
  Django 调试模式开关，当前固定关闭。
- DEFAULT_AUTO_FIELD：
  未显式声明主键类型时使用的 Django 默认字段。
- INSTALLED_APPS：
  Django 用户/会话、ORM 内容类型、DRF 与 interviews 应用的装配清单。
- LOGGING：
  控制台日志格式、级别和处理器；不配置文件日志。
- MIDDLEWARE：
  按顺序执行安全、来源、会话身份、部署登录门禁、CSRF 和通用 HTTP 处理。
- REST_FRAMEWORK：
  JSON 渲染/解析、分页、session 认证及统一错误处理；登录用户写请求校验 CSRF。
- TEMPLATES：页面模板目录和请求/身份上下文，不向模板提供密钥。
- INTERVIEW_REQUIRE_LOGIN：默认本地回环开发不强制登录，生产配置显式启用。
- AUTH_PASSWORD_VALIDATORS：注册不施加密码复杂度规则，哈希仍使用 Django 标准实现。
- ROOT_URLCONF：
  Django 根路由模块名称。
- SECRET_KEY：
  从进程环境或 backend/.env 取得的 Django 应用密钥，不是模型 API key。
- TIME_ZONE：
  服务器时间基准，保持 UTC。
- USE_TZ：
  是否启用带时区的时间处理。

设计说明：
装配说明：先加入仓库模块搜索路径，再读取 backend/.env，已存在的进程环境变量优先。
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# 后端可从自己的目录启动，同时复用仓库内的 Agent/App/Shared 模块。
sys.path.insert(0, str(BASE_DIR.parent))
load_dotenv(BASE_DIR / ".env", override=False)
# 环境缺少密钥时立即停止启动，避免以隐式默认密钥运行。
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "interviews",
]
INTERVIEW_REQUIRE_LOGIN = False
AUTH_PASSWORD_VALIDATORS = []
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ]
        },
    }
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "interviews.middleware.LocalOnlyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "interviews.accounts.AccountRequiredMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("INTERVIEW_DB_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {"timeout": 5},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "interviews.errors.api_exception_handler",
}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"context": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "context"}},
    "loggers": {"interviews": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}

PDF_TASK_EXECUTION = os.environ.get("PDF_TASK_EXECUTION", "inline")
if PDF_TASK_EXECUTION not in {"inline", "celery"}:
    raise ValueError("PDF_TASK_EXECUTION must be inline or celery")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
PDF_TASK_REDIS_URL = os.environ.get("PDF_TASK_REDIS_URL", "redis://127.0.0.1:6379/1")
