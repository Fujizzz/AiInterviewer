#!/usr/bin/python3
"""职责：接收 GitHub Actions 签名 SSH 通道内的源码包并部署确定的 main 提交。

实现：强制命令只接受 deploy + 完整 SHA；有界安全解包、独立依赖、备份、迁移和健康检查。
关联：仓库 workflow 通过受限 ai-deploy 密钥调用；脚本安装为 root 所有的固定发布入口。
目录：
- run：执行固定参数命令，失败终止发布，不静默重试。
- app_command：加载 root 私有环境后降权执行 Python 应用命令。
- wait_for_http_ready：有限等待 ASGI 登录页就绪，不重发业务或模型探针。
- drain_archive：有界消费已切换版本的重复上传，避免再次解包和迁移。
- extract_release：验证归档路径与类型并在新目录中解包。
- main：加发布锁，预检、停止服务、迁移、切换并验证；失败保留现场和日志。
关键变量：
- ROOT：固定应用根目录，不从 SSH 输入读取路径。
- logger：发布阶段日志，只记录版本标识和状态，不打印环境或密码。
"""

import fcntl
import logging
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path("/opt/ai-interviewer")
logger = logging.getLogger("ai-interviewer.deploy")
HTTP_READY_TIMEOUT = 30
HTTP_ATTEMPT_TIMEOUT = 2
HTTP_RETRY_INTERVAL = 0.25
ARCHIVE_LIMIT = 128 * 1024 * 1024


def run(arguments):
    """输入固定程序与参数列表；继承标准输出，非零抛异常，不使用 shell 拼接输入。"""
    subprocess.run(arguments, check=True)


def app_command(release, arguments):
    """输入已验证发布目录和参数；加载私有配置后以应用用户执行该版本 Python。"""
    command = shlex.join(
        ["runuser", "-u", "aiinterviewer", "--", str(release / ".venv/bin/python"), *arguments]
    )
    run(
        [
            "/bin/bash",
            "-c",
            "set -a; . /etc/ai-interviewer/app.env; set +a; "
            + "cd "
            + shlex.quote(str(release / "backend"))
            + "; exec "
            + command,
        ]
    )


def wait_for_http_ready():
    """只重试登录页 GET 的暂时连接失败；退出、HTTP/内容错误或 30 秒期限均阻断发布。"""
    request = urllib.request.Request(
        "http://127.0.0.1:8765/login/",
        headers={"Host": "47.239.50.129", "X-Forwarded-Proto": "https"},
    )
    deadline = time.monotonic() + HTTP_READY_TIMEOUT
    last_error = None
    logger.info("Waiting for ASGI HTTP readiness")
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ASGI HTTP was not ready within 30 seconds") from last_error
        state = subprocess.run(
            ["systemctl", "show", "--property=ActiveState", "--value", "ai-interviewer"],
            check=True,
            capture_output=True,
            text=True,
            timeout=min(HTTP_ATTEMPT_TIMEOUT, remaining),
        ).stdout.strip()
        if state not in {"active", "activating", "reloading"}:
            raise RuntimeError(f"ASGI service is not running: {state}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ASGI HTTP was not ready within 30 seconds") from last_error
        try:
            with urllib.request.urlopen(
                request, timeout=min(HTTP_ATTEMPT_TIMEOUT, remaining)
            ) as response:
                if response.status != 200 or b'name="username"' not in response.read(64 * 1024):
                    raise RuntimeError("ASGI readiness login page is invalid")
            if time.monotonic() >= deadline:
                raise TimeoutError("ASGI HTTP was not ready within 30 seconds")
            logger.info("ASGI HTTP readiness verified")
            return
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as error:
            if not isinstance(error.reason, (ConnectionError, TimeoutError)):
                raise
            last_error = error
        except (ConnectionError, TimeoutError) as error:
            last_error = error
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(HTTP_RETRY_INTERVAL, remaining))


def drain_archive():
    """有界消费已切换版本的重复上传；不解包、不重复迁移或重启服务。"""
    size = 0
    while block := sys.stdin.buffer.read(1024 * 1024):
        size += len(block)
        if size > ARCHIVE_LIMIT:
            raise ValueError("Release archive exceeds limit")


def extract_release(archive, destination):
    """验证归档总量、路径和普通文件/目录类型；拒绝链接、设备、秘密文件和路径穿越。"""
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if sum(member.size for member in members) > 256 * 1024 * 1024:
            raise ValueError("Release uncompressed size exceeds limit")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or not (member.isfile() or member.isdir())
                or any(part in {".git", ".venv"} for part in path.parts)
                or (path.name.startswith(".env") and path.name != ".env.example")
            ):
                raise ValueError("Unsafe release member")
        # 所有成员先经过上述白名单；不允许链接或归档提供所有者/权限。
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as src, target.open("xb") as dst:
                    while block := src.read(1024 * 1024):
                        dst.write(block)
                target.chmod(0o644)


def main():
    """读取受限 SSH 命令及 stdin 归档；只发布新 SHA，无自动回滚或重放迁移。

    停服前完成依赖与预检；备份成功才迁移。迁移或切换后失败保持明确 failed 状态，
    旧发布与备份保留，避免数据库变更后静默切回不兼容源码。
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    match = re.fullmatch(r"deploy ([0-9a-f]{40})", os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    if not match or os.geteuid() != 0:
        raise PermissionError("Only the restricted deployment command is allowed")
    revision = match.group(1)
    with (ROOT / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release = ROOT / "releases" / revision
        deployed = ROOT / "deployed-revision"
        current = ROOT / "current"
        is_current = current.is_symlink() and current.resolve() == release
        if (deployed.exists() and deployed.read_text().strip() == revision) or is_current:
            if not is_current:
                raise RuntimeError("Recorded deployment is not the current release")
            drain_archive()
            wait_for_http_ready()
            app_command(release, [str(release / "deploy/smoke.py")])
            deployed.write_text(revision + "\n")
            logger.info("Existing deployment verified revision=%s", revision)
            return
        release.mkdir(mode=0o755)
        archive = release / "source.tar.gz"
        size = 0
        with archive.open("xb") as target:
            while block := sys.stdin.buffer.read(1024 * 1024):
                size += len(block)
                if size > ARCHIVE_LIMIT:
                    raise ValueError("Release archive exceeds limit")
                target.write(block)
        logger.info("Preparing release revision=%s", revision)
        extract_release(archive, release)
        archive.unlink()
        uv = str(ROOT / "bootstrap/bin/uv")
        run([uv, "venv", "--python", str(ROOT / "venv/bin/python"), str(release / ".venv")])
        run(
            [
                uv,
                "pip",
                "sync",
                "--python",
                str(release / ".venv/bin/python"),
                str(release / "deploy/requirements-linux.lock.txt"),
            ]
        )
        run([uv, "pip", "check", "--python", str(release / ".venv/bin/python")])
        app_command(release, ["manage.py", "check", "--deploy"])
        app_command(release, ["manage.py", "makemigrations", "--check", "--dry-run"])
        run(["bash", str(release / "deploy/backup-postgresql.sh")])
        # ASGI 先停止接收/取消流，worker 再退出；升级不保留未确认的业务操作。
        run(["systemctl", "stop", "ai-interviewer"])
        if Path("/etc/systemd/system/ai-interviewer-celery.service").exists():
            run(["systemctl", "stop", "ai-interviewer-celery"])
        app_command(release, ["manage.py", "migrate", "--noinput"])
        pending = ROOT / "next-release"
        pending.symlink_to(release)
        pending.replace(ROOT / "current")
        for source, name in [
            ("ai-interviewer.service", "ai-interviewer.service"),
            ("celery.service", "ai-interviewer-celery.service"),
        ]:
            run(
                [
                    "install",
                    "-m",
                    "644",
                    str(release / "deploy" / source),
                    "/etc/systemd/system/" + name,
                ]
            )
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", "ai-interviewer-celery", "ai-interviewer"])
        # Type=simple 只确认进程已启动；有界等待安全 GET 就绪，再执行一次完整业务验收。
        wait_for_http_ready()
        app_command(release, [str(release / "deploy/smoke.py")])
        (ROOT / "deployed-revision").write_text(revision + "\n")
        logger.info("Deployment verified revision=%s", revision)


if __name__ == "__main__":
    main()
