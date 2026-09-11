"""真实服务器联调入口。临时 SQLite 验证业务持久化，流式部分验证原样回传及数据库不变。

目录：
- request
- check_rest
- check_rest.start_once
- check_wav
- main
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
    """发送一次有超时限制的 JSON HTTP 请求，返回解码数据；网络错误不重试。"""
    req = urllib.request.Request(
        base + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with HTTP.open(req, timeout=5) as response:
        return json.load(response)


def check_rest(base):
    """通过实际 HTTP 完成场次生命周期，并发相同版本更新必须分别得到 200/409。"""
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
    """在内存生成 WAV、分片回传并重组解码，验证媒体字节和计数一致。"""
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


def main():
    """功能：创建临时业务数据库、启动 Uvicorn，执行 HTTP、WAV 与 Node 联调。
    方法：仅启动就绪探测允许重复检查；测试调用不重试。流式前后比较数据库字节。
    副作用：临时业务库和服务日志在临时目录，finally 停止子进程后自动清理。"""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js 22+ is required to test the actual frontend StreamClient.")
    with tempfile.TemporaryDirectory(prefix="interview-e2e-") as temporary:
        task_dir = Path(temporary).resolve()
        env = {
            **os.environ,
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
            "INTERVIEW_DB_PATH": str(task_dir / "test.sqlite3"),
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
                    "config.asgi:application",
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
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
