"""职责：发布后验证 HTTP、PostgreSQL、Redis、Celery 和实际个人岗位推荐。

实现：检查数据库、公开登录页和无用户数据 Celery 探针；用临时账号/简历/会话调用
运行中的 ASGI、冻结模型和单次 LLM 精排，finally 清理探针记录；失败非零退出，不重试。
关联：deploy-release.py 在切换服务后以应用用户执行本文件。
目录：
- verify_recommendations：
  用临时保存槽位和真实 Session/CSRF 验证 100 岗粗排/20 岗候选/5 岗精排及双语理由，再清理。
- main：读取已加载生产环境，验证基础组件与岗位推荐，打印固定成功信息。
关键变量：
（无模块级变量。）
"""

import json
import os
import sys
import time
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from uuid import uuid4


def verify_recommendations(base_url="http://127.0.0.1:8765"):
    """输入已初始化 Django 和运行中服务的 base_url；成功返回 None，错误传播以阻断验收。

    创建随机用户名、不可登录密码、含 Python 技能的 ready 简历和标准数据库会话。
    通过回环代理头及会话请求简历页取得 CSRF，再调用真实推荐路由，
    核对 100 岗粗排、20 岗候选、5 个合法 ID、双语理由
    和体验/实验标记；不覆盖应用配置、评分、用户资料或模型。所有探针记录在 finally 删除，
    清理异常也明确失败；Cookie、CSRF 和响应正文不输出。
    此探针发起一次真实 API 请求（产生服务商用量），但不评价推荐质量。
    HTTP 读取使用既有 30 秒预算，不增加 API 超时或重发。
    """
    from django.contrib.auth import (
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
        SESSION_KEY,
        get_user_model,
    )
    from django.contrib.sessions.backends.db import SessionStore
    from django.db import transaction
    from interviews.resume_models import ResumeVersion

    user = get_user_model().objects.create_user(username=f"deployment-probe-{uuid4().hex}")
    version = None
    session = SessionStore()
    try:
        version = ResumeVersion.objects.create(
            owner=user,
            label="Deployment probe",
            status="ready",
            text="Synthetic deployment probe",
            recommendation_slots={"skills": ["Python"]},
        )
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.save()
        headers = {
            "Host": "47.239.50.129",
            "X-Forwarded-Proto": "https",
            "Cookie": f"sessionid={session.session_key}",
        }
        request = urllib.request.Request(f"{base_url}/resumes/", headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200 or b'id="recommendation-state"' not in response.read():
                raise RuntimeError("Recommendation probe could not access the authenticated page")
            cookies = SimpleCookie()
            for cookie in response.headers.get_all("Set-Cookie", []):
                cookies.load(cookie)
        token = cookies["csrftoken"].value
        request = urllib.request.Request(
            f"{base_url}/api/resume-versions/{version.pk}/recommendations/",
            data=b"{}",
            headers={
                **headers,
                "Cookie": f"sessionid={session.session_key}; csrftoken={token}",
                "X-CSRFToken": token,
                "Origin": "https://47.239.50.129",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
            if response.status != 200:
                raise RuntimeError("Recommendation probe received an unsuccessful HTTP status")
        jobs = result.get("results", [])
        if (
            result.get("source_kind") != "experience"
            or result.get("experimental") is not True
            or result.get("release_gate_passed") is not False
            or result.get("sorted_by") != "llm_order"
            or result.get("pipeline", {}).get("catalog_count") != 100
            or result.get("pipeline", {}).get("topk1") != 20
            or result.get("pipeline", {}).get("topk2") != 5
            or result.get("pipeline", {}).get("shortlist_count") != 20
            or len(jobs) != 5
            or len({job["job_id"] for job in jobs}) != 5
            or not {job["job_id"] for job in jobs}.issubset(
                {f"J{number:04}" for number in range(1, 101)}
            )
            or [job.get("rank") for job in jobs] != [1, 2, 3, 4, 5]
            or any(
                not job.get("recommendation_reason", {}).get(lang)
                for job in jobs
                for lang in ("zh", "en")
            )
            or any(job.get("status") != "scored" for job in jobs)
        ):
            raise RuntimeError(
                "Recommendation probe failed the complete experimental catalog contract"
            )
        print(
            "Live authenticated recommendation verified: "
            "100 catalog jobs -> 20 shortlist -> 5 LLM jobs with reasons"
        )
    finally:
        # 清理仅作用于本次持有的探针记录；数据库删除失败仍尝试撤销会话，错误保持可见。
        try:
            with transaction.atomic():
                if version is not None:
                    version.delete()
                user.delete()
        finally:
            if session.session_key:
                session.delete()


def main():
    """无外部参数；使用生产环境与当前源码，Celery 最长等待 20 秒，不重发任务。

    Django 初始化后检查数据库与登录页，运行会清理临时记录的实际岗位推荐探针，
    再验证 Redis/Celery；任何异常非零退出，不能以单项成功代替完整发布验收。
    """
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
    verify_recommendations()
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
