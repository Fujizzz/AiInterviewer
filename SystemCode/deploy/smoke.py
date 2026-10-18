"""职责：预检数字人播放器归档，发布后验证播放器、HTTP、数据库/队列及既有模型/MCP。

实现：检查数据库、公开登录页和无用户数据 Celery 探针；用临时账号/简历/会话调用
运行中的 ASGI、推荐模型和单次 LLM 精排，并校验本地结束模型与真实 MCP 握手。
finally 清理探针记录；失败非零退出，不重试。
关联：workflow 以无模型 CLI 校验 bundle/归档；deploy-release.py 切换后执行完整验收。
目录：
- verify_frontend_bundle：不初始化 Django，校验非空 ESM 与现有导出。
- verify_avatar_player：通过临时认证会话核对播放器 HTTP 内容，finally 清理账号/会话。
- verify_release_bundle：发布前校验安全归档成员及单个播放器与同 run artifact 一致。
- verify_recommendations：
  用临时保存槽位和真实 Session/CSRF 验证 100 岗粗排/20 岗候选/5 岗精排及双语理由，再清理。
- verify_answer_completion：校验显式本地模型、真实认证 WebSocket 的 MCP 握手和工具注册。
- main：读取生产环境，验证基础组件、推荐和结束分支，打印固定成功信息。
关键变量：
（无模块级变量。）
"""

import hashlib
import json
import os
import re
import sys
import tarfile
import time
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path, PurePosixPath
from uuid import uuid4


def verify_frontend_bundle(bundle=None):
    """校验显式文件或本发布 dist；返回 bytes，不执行 JS、不调用模型、不读取环境秘密。

    文件必须非空、为普通文件、至多 16 MiB，UTF-8 ESM 必须导出网页使用的三个名称。
    缺失或错误导出均阻断发布；CLI 不初始化 Django、不访问服务、不读取环境秘密。
    """
    if bundle is None:
        bundle = (
            Path(__file__).resolve().parents[1]
            / "backend/frontend/digital-human/dist/pixel-player.js"
        )
    bundle = Path(bundle)
    limit = 16 * 1024 * 1024
    if bundle.is_symlink() or not bundle.is_file() or not 0 < bundle.stat().st_size <= limit:
        raise RuntimeError("Avatar browser bundle is missing, empty, or invalid")
    content = bundle.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("Avatar browser bundle is not UTF-8 JavaScript") from error
    exports = {
        entry.strip().split(" as ")[-1].strip()
        for block in re.findall(r"\bexport\s*\{([^}]+)\}", text)
        for entry in block.split(",")
    }
    if not {"AvatarPlayer", "PresentationController", "loadAvatarConfiguration"} <= exports:
        raise RuntimeError("Avatar browser bundle is missing required exports")
    return content


def verify_avatar_player(base_url="http://127.0.0.1:8765", bundle=None):
    """输入已初始化 Django/运行中服务，可选本发布文件；验证认证播放器且返回 None。

    使用随机不可登录账号与真实数据库 Session，要求 JavaScript MIME/精确文件字节。
    请求有界且不重试；失败也仅清理本探针账号及会话，不输出 Cookie/正文，不调用模型。
    """
    from django.contrib.auth import (
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
        SESSION_KEY,
        get_user_model,
    )
    from django.contrib.sessions.backends.db import SessionStore

    expected = verify_frontend_bundle(bundle)
    user = get_user_model().objects.create_user(username=f"avatar-probe-{uuid4().hex}")
    session = SessionStore()
    try:
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.save()
        request = urllib.request.Request(
            f"{base_url}/stream-demo/pixel-player.js",
            headers={
                "Host": "47.239.50.129",
                "X-Forwarded-Proto": "https",
                "Cookie": f"sessionid={session.session_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if (
                response.status != 200
                or response.headers.get_content_type()
                not in {"text/javascript", "application/javascript"}
                or response.read(16 * 1024 * 1024 + 1) != expected
            ):
                raise RuntimeError("The live avatar module does not match this release")
        print("Live authenticated avatar browser module verified")
    finally:
        try:
            user.delete()
        finally:
            if session.session_key:
                session.delete()


def verify_release_bundle(archive, bundle=None):
    """预检未压缩 tar 和已验证单文件 artifact；成功返回 None，不解包或访问远端。

    保留发布大小/路径/类型限制，并禁止 UE、依赖树及管理凭据进入包。
    播放器路径必须出现且只出现一次，并与同 run artifact 字节摘要完全一致。
    此检查在 SSH 上传前执行；服务器仍使用既有 root 安全解包入口。
    """
    expected = verify_frontend_bundle(bundle)
    expected_path = "backend/frontend/digital-human/dist/pixel-player.js"
    seen = set()
    with tarfile.open(archive, "r:") as source:
        members = source.getmembers()
        if sum(member.size for member in members) > 256 * 1024 * 1024:
            raise RuntimeError("Release uncompressed size exceeds limit")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or "\\" in member.name
                or member.size < 0
                or not (member.isfile() or member.isdir())
                or any(part in {".git", ".venv", "node_modules", ".infrastructure"}
                       for part in path.parts)
                or path.parts[0] == "DigitalHuman"
                or (path.name.startswith(".env") and path.name != ".env.example")
                or path.name == "server_info.txt"
                or path in seen
            ):
                raise RuntimeError("Unsafe or duplicate release member")
            seen.add(path)
        matches = [member for member in members if member.name == expected_path]
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size != len(expected):
            raise RuntimeError("Release must contain exactly one verified avatar module")
        actual = source.extractfile(matches[0]).read(len(expected) + 1)
        if hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
            raise RuntimeError("Release avatar module differs from the verified artifact")


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


def verify_answer_completion():
    """输入生产模型路径和已初始化 Django；输出真实本地加载/MCP 注册校验或显式异常。

    显式配置模型时验证文件契约、摘要和可执行推理；空路径仍是原有 Qwen-only 配置。
    创建临时用户/认证 Session，经生产 HTTPS WebSocket 验证 MCP 初始化及工具清单，
    不调用面试、结束工具、语音或 Qwen；finally 只清理本次用户及会话，清理失败仍传播。
    此检查证明部署接线可用，不能证明意图准确率、三秒静默或真实麦克风端到端行为。
    """
    from django.contrib.auth import (
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
        SESSION_KEY,
        get_user_model,
    )
    from django.contrib.sessions.backends.db import SessionStore
    from websockets.sync.client import connect

    model_path = os.getenv("ANSWER_COMPLETION_GATE_PATH", "").strip()
    if model_path:
        from agents.completion_gate import load_gate

        probability = load_gate(model_path).score("Fictional deployment transcript for inference.")
        assert 0 <= probability <= 1
    user = get_user_model().objects.create_user(username=f"completion-probe-{uuid4().hex}")
    session = SessionStore()
    try:
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.save()
        with connect(
            "wss://47.239.50.129/ws/agent/", origin="https://47.239.50.129",
            additional_headers={"Cookie": f"sessionid={session.session_key}"},
            open_timeout=10, close_timeout=5,
        ) as socket:
            ready = json.loads(socket.recv(timeout=10))
            assert "answer_completion_mcp" in ready["capabilities"]
            socket.send(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "deployment-probe", "version": "1"}},
            }))
            initialized = json.loads(socket.recv(timeout=10))
            assert initialized["id"] == 1 and "result" in initialized
            socket.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            socket.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
            tools = json.loads(socket.recv(timeout=10))
            assert tools["id"] == 2
            assert any(x["name"] == "finish_current_answer" for x in tools["result"]["tools"])
        print("Completion bundle and live MCP tool registration verified")
    finally:
        try:
            user.delete()
        finally:
            if session.session_key:
                session.delete()


def main():
    """无外部参数；使用生产环境与当前源码，Celery 最长等待 20 秒，不重发任务。

    先核对播放器文件，再初始化 Django 检查数据库与登录页及认证播放器、结束模型和 MCP，
    运行会清理临时记录的推荐探针，
    再验证 Redis/Celery；任何异常非零退出，不能以单项成功代替完整发布验收。
    """
    verify_frontend_bundle()
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
    verify_avatar_player()
    verify_recommendations()
    verify_answer_completion()
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
    if sys.argv[1:] == ["--check-frontend-bundle"]:
        verify_frontend_bundle()
        print("Avatar browser bundle contract verified")
    elif len(sys.argv) == 3 and sys.argv[1] == "--check-release-bundle":
        verify_release_bundle(sys.argv[2])
        print("Release archive and avatar artifact verified")
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("Use --check-frontend-bundle or --check-release-bundle ARCHIVE")
