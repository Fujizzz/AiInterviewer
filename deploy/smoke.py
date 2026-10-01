"""职责：发布后验证 HTTP、PostgreSQL、Redis 和真实 Celery 任务消费。

实现：只读数据库、请求公开登录页、发布无用户数据探针；失败非零退出，不调用模型。
关联：deploy-release.py 在切换服务后以应用用户执行本文件。
目录：
- main：读取已加载生产环境，验证四个组件，打印固定成功信息。
关键变量：
（无模块级变量。）
"""

import os
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4


def main():
    """无外部参数；使用生产环境与当前源码，探针最长等待 20 秒，不重发任务。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.production")
    import django

    django.setup()
    from config.celery import app
    from django.conf import settings
    from django.db import connection
    from redis import Redis

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
    request = urllib.request.Request(
        "http://127.0.0.1:8765/login/",
        headers={"Host": "47.239.50.129", "X-Forwarded-Proto": "https"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200 and b'name="username"' in response.read()
    probe = uuid4().hex
    key = f"ai-interviewer:probe:{probe}"
    with Redis.from_url(settings.PDF_TASK_REDIS_URL, socket_timeout=5) as redis:
        assert redis.ping()
        app.send_task("interviews.worker_probe", args=[probe], retry=False, expires=20)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if redis.get(key) == b"ok":
                redis.delete(key)
                print("HTTP, PostgreSQL, Redis and Celery task execution verified")
                return
            time.sleep(0.25)
        raise TimeoutError("Celery did not consume deployment probe")


if __name__ == "__main__":
    main()
