"""后端配置。集中声明应用、SQLite、JSON 接口和控制台日志；密钥必须由环境提供。

目录：
- BASE_DIR、SECRET_KEY、ALLOWED_HOSTS：路径、显式密钥与允许的主机。
- INSTALLED_APPS、MIDDLEWARE、ROOT_URLCONF、ASGI_APPLICATION：应用装配。
- DATABASES、DEFAULT_AUTO_FIELD、USE_TZ、TIME_ZONE：存储和时间语义。
- REST_FRAMEWORK：JSON 输入输出、分页和异常适配。
- LOGGING：带上下文的控制台日志，不配置文件处理器。
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# 环境缺少密钥时立即停止启动，避免以隐式默认密钥运行。
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
INSTALLED_APPS = ["django.contrib.contenttypes", "rest_framework", "interviews"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "interviews.middleware.LocalOnlyMiddleware",
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
    "DEFAULT_AUTHENTICATION_CLASSES": [],
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
