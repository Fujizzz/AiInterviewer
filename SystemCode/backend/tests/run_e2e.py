"""Responsibilities: Launch isolated end-to-end checks against a real local server.
Implementation: Create a temporary SQLite database, start Uvicorn, and verify HTTP, WebSocket audio,
and Node client behavior.
Related Modules: run_agent_e2e reuses this launcher; Django migrations, config.asgi, and the
frontend StreamClient are exercised.
Declaration Index:
- request: Send one bounded JSON HTTP request and return decoded data without retrying network
  errors.
- check_rest: Exercise persisted session lifecycle and concurrent stale-version rejection over HTTP.
- check_rest.start_once: Submit one versioned start action and return success or HTTP error status
  for assertions.
- check_wav: Generate a WAV in memory, round-trip chunks through WebSocket, and verify bytes and
  counts.
- stop_windows_tree: Capture and stop the spawned Windows process tree, then wait for handles to
  exit.
- main: Create isolated resources, launch the server, run protocol checks, and clean up the process
  tree.
Variable Index:
- ROOT: Backend root used as the subprocess working directory.
- HTTP: Local HTTP opener configured to bypass system proxies.
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
    """Functionality: Send one JSON HTTP request to the local service.
    Inputs: Base URL, resource path, optional JSON body, and optional HTTP method.
    Outputs: The decoded JSON response value.
    Logic: Serialize an optional body and open the request through the proxy-disabled HTTP opener.
    Constraints: Uses a five-second timeout and propagates HTTP, timeout, and decoding errors
    without retry.
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
    """Functionality: Exercise a persisted practice-session lifecycle and optimistic concurrency
    over HTTP.
    Inputs: Base URL for a server migrated against an isolated database containing the two seed
    questions.
    Outputs: None; assertions verify status, answer persistence, and the 200/409 concurrent-update
    result.
    Logic: Create a session, race two updates using one version, submit an answer, and finish the
    session.
    Constraints: Failures propagate; no retry occurs and writes are limited to the launcher's
    temporary database.
    """
    session = request(base, "/api/sessions/", {})
    assert len(session["items"]) == 2
    assert (session["prep_seconds"], session["answer_seconds"]) == (10, 90)
    path = f"/api/sessions/{session['id']}/items/{session['items'][0]['id']}/"

    # Two requests with the same version: exactly one may change state.
    def start_once():
        """Functionality: Attempt one start transition at the fixed session-item version.
        Inputs: Captured base URL and item path from check_rest.
        Outputs: HTTP status code, including an error response code.
        Logic: Send one PATCH request and convert only HTTPError into its status code.
        Constraints: Other exceptions propagate and the request is not retried.
        """
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
    """Functionality: Verify the audio echo protocol using an in-memory synthetic WAV.
    Inputs: Base URL for the ready local server; the fixture is 16 kHz mono silence.
    Outputs: None; assertions compare echoed frames, hashes, byte counts, and decoded WAV frame
    count.
    Logic: Encode chunks with sequence numbers, verify acknowledgements, finish the stream, and
    decode the reconstructed bytes.
    Constraints: Reads no real recording, writes no media file, and propagates protocol/network
    failures.
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
    """Functionality: Stop this test's Windows server process tree and wait for native process exit.
    Inputs: The Popen object returned for the launched server.
    Outputs: None; raises if termination or a bounded process wait fails.
    Logic: Snapshot descendants by parent PID, capture process handles, invoke taskkill, and wait on
    each handle.
    Constraints: Operates only on this server tree, waits at most ten seconds per process, and
    deletes no files.
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
    """Functionality: Launch the real ASGI service with temporary resources and run HTTP, WAV, and
    Node client checks.
    Inputs: Optional ASGI application path and optional Agent-specific check callback.
    Outputs: None; reports successful stages and raises on failed assertions or cleanup.
    Logic: Migrate a temporary SQLite database, start Uvicorn, poll readiness, run checks, then
    terminate the server in finally.
    Constraints: Only readiness probing repeats; business requests are not retried, streaming must
    leave database bytes unchanged, and cleanup failures remain visible.
    """
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
                # Agent persistence is expected; only the WAV/echo diagnostic stage must leave
                # database bytes unchanged.
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
                # The Windows virtualenv python.exe may be a launcher; terminating it alone can
                # leave Uvicorn holding server.log.
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
