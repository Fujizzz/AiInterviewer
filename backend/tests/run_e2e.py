"""真实服务器联调入口。临时 SQLite 验证业务持久化，流式部分验证原样回传及数据库不变。

实现与关联：由 run_agent_e2e 复用启动器；隔离模型、数据库与服务容量目录后执行既有断言。
Windows 虚拟环境解释器可能派生实际运行进程，清理必须结束本测试启动的整棵进程树。
taskkill 返回不代表每个子进程已释放句柄，删除临时目录前须等待已捕获的进程句柄退出。

目录：
- request：
  发送一次有超时限制的 JSON HTTP 请求，返回解码数据；网络错误不重试。
- check_rest：
  通过实际 HTTP 完成场次生命周期，并发相同版本更新必须分别得到 200/409。
- check_rest.start_once：
  提交一次固定版本的开始动作，将成功或 HTTP 错误统一转换为状态码供断言。
- check_wav：
  在内存生成 WAV、分片回传并重组解码，验证媒体字节和计数一致。
- stop_windows_tree：
  捕获测试启动进程及后代的句柄，结束后等待全部退出再清理临时日志。
- main：
  功能：创建临时业务数据库、启动 Uvicorn，执行 HTTP、WAV 与 Node 联调。

关键变量：
- HTTP：
  禁用代理的本机 HTTP opener，避免测试请求经过系统代理。
- ROOT：
  后端根目录，作为联调子进程工作目录。
"""

import concurrent.futures
import hashlib
import io
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(base, path, data=None, method=None):
    """发送一次有超时限制的 JSON HTTP 请求，返回解码数据；网络错误不重试。

    输入：本机 base、资源 path、可选 JSON 请求体和 HTTP 方法；不使用系统代理。
    返回：反序列化后的 JSON；HTTP 错误、超时和非 JSON 响应直接传播给联调用例。
    """
    req = urllib.request.Request(
        base + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with HTTP.open(req, timeout=5) as response:
        return json.load(response)


def check_rest(base):
    """通过实际 HTTP 完成场次生命周期，并发相同版本更新必须分别得到 200/409。

    前置条件：base 指向已迁移两道种子题的隔离测试数据库。
    方法：创建场次→并发竞争同一版本→提交答案→结束场次，并核对状态与持久化字段。
    返回 None；失败以断言或请求异常终止，不自动重试。写入只发生在启动器创建的临时库。
    """
    session = request(base, "/api/sessions/", {})
    assert len(session["items"]) == 2
    assert (session["prep_seconds"], session["answer_seconds"]) == (10, 90)
    path = f"/api/sessions/{session['id']}/items/{session['items'][0]['id']}/"

    # Two requests with the same version: exactly one may change state.
    def start_once():
        """提交一次固定版本的开始动作，将成功或 HTTP 错误统一转换为状态码供断言。"""
        try:
            request(base, path, {"action": "start", "version": 1}, "PATCH")
            return 200
        except urllib.error.HTTPError as exc:
            return exc.code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start_once(), range(2)))
    assert sorted(results) == [200, 409], results
    updated = request(
        base,
        path,
        {"action": "complete", "version": 2, "answer_text": "Test answer", "duration_ms": 90000},
        "PATCH",
    )
    finished = request(
        base, f"/api/sessions/{session['id']}/finish/", {"version": updated["version"]}
    )
    assert finished["status"] == "completed"
    assert finished["items"][0]["answer_text"] == "Test answer"
    print("PASS HTTP: persisted session lifecycle and concurrent stale-version rejection")


def check_wav(base):
    """在内存生成 WAV、分片回传并重组解码，验证媒体字节和计数一致。

    输入：已就绪的本机服务 base；构造 16 kHz 单声道静音测试数据，不读取真实录音。
    方法：编码四字节序号→核对 ACK 摘要及回传→提交累计数→重新解码 WAV 帧数。
    返回 None；不写媒体文件，协议错误以断言或网络异常传播给启动器。
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)
    original = buffer.getvalue()
    chunks = [original[offset : offset + 4096] for offset in range(0, len(original), 4096)]
    recovered = bytearray()
    with connect(base.replace("http://", "ws://") + "/ws/echo/", origin=base, proxy=None) as ws:
        hello = json.loads(ws.recv(timeout=5))
        ws.send(json.dumps({"type": "start", "mode": "audio", "mime_type": "audio/wav"}))
        assert json.loads(ws.recv(timeout=5))["type"] == "started"
        for index, chunk in enumerate(chunks, 1):
            frame = struct.pack("!I", index) + chunk
            ws.send(frame)
            ack = json.loads(ws.recv(timeout=5))
            assert ack["sha256"] == hashlib.sha256(chunk).hexdigest()
            echoed = ws.recv(timeout=5)
            assert echoed == frame
            recovered.extend(echoed[4:])
        assert bytes(recovered) == original
        ws.send(
            json.dumps(
                {"type": "finish", "verified_chunks": len(chunks), "verified_bytes": len(original)}
            )
        )
        summary = json.loads(ws.recv(timeout=5))
        assert summary["status"] == "finished"
        assert summary["connection_id"] == hello["connection_id"]
        assert summary["byte_count"] == len(original)
    with wave.open(io.BytesIO(recovered), "rb") as decoded:
        assert decoded.getnframes() == 16000
    print(f"PASS WAV: {len(chunks)} chunks, {len(original)} bytes, in-memory round-trip decode")


def stop_windows_tree(server):
    """输入本测试 Popen，结束其 Windows 进程树并等待原生进程真正退出。

    先按父 PID 快照收集后代并打开句柄，再 taskkill；句柄等待不受 PID 复用影响。
    每个进程最多等待 10 秒，异常直接传播；不忽略临时文件占用，不重跑业务测试。
    PowerShell 隐藏运行，只操作该 Popen 的进程树，不删除任何文件。
    """
    script = r"""
$ErrorActionPreference = 'Stop'
$taskRoot = TASK_ROOT
$snapshot = @(Get-CimInstance Win32_Process)
$ids = [System.Collections.Generic.HashSet[int]]::new()
$null = $ids.Add($taskRoot)
do {
    $changed = $false
    foreach ($item in $snapshot) {
        if ($ids.Contains([int]$item.ParentProcessId)) {
            if ($ids.Add([int]$item.ProcessId)) { $changed = $true }
        }
    }
} while ($changed)
$handles = @()
try {
    foreach ($taskId in $ids) {
        try {
            $process = [System.Diagnostics.Process]::GetProcessById($taskId)
            $null = $process.Handle
            $handles += $process
        } catch [System.ArgumentException] {
            # Snapshot member already exited: no process remains to wait for.
        }
    }
    & taskkill.exe /PID $taskRoot /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Test server process-tree termination failed.' }
    foreach ($process in $handles) {
        if (-not $process.WaitForExit(10000)) { throw 'Test server descendant did not exit.' }
    }
} finally {
    foreach ($process in $handles) { $process.Dispose() }
}
"""
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script.replace("TASK_ROOT", str(server.pid)),
        ],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def main(asgi_app="config.asgi:application", agent_check=None):
    """功能：创建临时业务数据库、启动 Uvicorn，执行 HTTP、WAV 与 Node 联调。
    方法：仅启动就绪探测允许重复检查；测试调用不重试。流式前后比较数据库字节。
    输入：可显式指定离线 Agent 测试入口与检查函数；默认仍测试生产入口。
    副作用：临时业务库、容量锁和服务日志在临时目录；finally 在 Windows 结束本次启动的
    进程树，其他平台结束直接子进程；清理失败显式报错，不忽略文件占用或削弱测试断言。"""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js 22+ is required to test the actual frontend StreamClient.")
    with tempfile.TemporaryDirectory(prefix="interview-e2e-") as temporary:
        task_dir = Path(temporary).resolve()
        env = {
            **os.environ,
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
            "INTERVIEW_DB_PATH": str(task_dir / "test.sqlite3"),
            "SERVICE_CAPACITY_DIR": str(task_dir / "capacity"),
            "PYTHONNOUSERSITE": "1",
        }
        subprocess.run(
            [sys.executable, "-s", "manage.py", "migrate", "--noinput"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        log_path = task_dir / "server.log"
        with log_path.open("w", encoding="utf-8") as logfile:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-s",
                    "-m",
                    "uvicorn",
                    asgi_app,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--ws",
                    "websockets-sansio",
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=env,
                stdout=logfile,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                deadline = time.monotonic() + 15
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("ASGI server exited during startup")
                    try:
                        request(base, "/api/health/")
                        break
                    except urllib.error.URLError as exc:
                        if time.monotonic() > deadline:
                            raise TimeoutError(
                                "ASGI server was not ready within 15 seconds"
                            ) from exc
                        time.sleep(0.1)
                check_rest(base)
                if agent_check is not None:
                    agent_check(base)
                # Agent 持久化是预期写入；仅 WAV/echo 诊断阶段应保持数据库字节不变。
                database_before_streaming = (task_dir / "test.sqlite3").read_bytes()
                check_wav(base)
                subprocess.run(
                    [node, "--test", "tests/stream-client.test.mjs"], cwd=ROOT, check=True
                )
                subprocess.run(
                    [node, "tests/stream-smoke.mjs"],
                    cwd=ROOT,
                    env={**env, "TEST_BASE_URL": base},
                    check=True,
                )
                assert (task_dir / "test.sqlite3").read_bytes() == database_before_streaming
                print("PASS streaming leaves SQLite file byte-for-byte unchanged")
                print(
                    "PASS all end-to-end checks; "
                    "temporary database and server are isolated from development data"
                )
            except Exception:
                logfile.flush()
                print(log_path.read_text(encoding="utf-8"), file=sys.stderr)
                raise
            finally:
                # Windows venv 的 python.exe 是启动器；只 terminate 启动器可能让真正的
                # Uvicorn 子进程继续持有 server.log，导致 TemporaryDirectory 清理失败。
                if os.name == "nt" and server.poll() is None:
                    stop_windows_tree(server)
                elif server.poll() is None:
                    server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
