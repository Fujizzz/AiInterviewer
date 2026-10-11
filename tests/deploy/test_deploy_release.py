"""Verify deployment readiness and interrupted-release recovery without Linux services or I/O.

The Linux-only entry point is loaded with a temporary fcntl substitute on Windows. HTTP, systemd,
clock and application commands are controlled; only temporary release files are written.
"""

import importlib.util
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import pytest


@pytest.fixture
def deploy():
    path = Path(__file__).resolve().parents[2] / "deploy/deploy-release.py"
    specification = importlib.util.spec_from_file_location("deploy_release", path)
    module = importlib.util.module_from_spec(specification)
    if os.name == "nt":
        fcntl = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
        with patch.dict(sys.modules, {"fcntl": fcntl}):
            specification.loader.exec_module(module)
    else:
        specification.loader.exec_module(module)
    return module


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        assert 0 < duration <= 30 - self.now
        self.sleeps.append(duration)
        self.now += duration


@pytest.fixture
def readiness(deploy, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(deploy.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(deploy.time, "sleep", clock.sleep)
    systemd = Mock(return_value=SimpleNamespace(stdout="active\n"))
    monkeypatch.setattr(deploy.subprocess, "run", systemd)
    return clock, systemd


def login_response(status=200, content=b'<input name="username">'):
    response = Mock(status=status)
    response.read.return_value = content
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    return context


def test_readiness_retries_transport_failures_then_accepts_login(deploy, readiness, monkeypatch):
    clock, systemd = readiness
    http = Mock(side_effect=[
        URLError(ConnectionRefusedError("not bound")),
        URLError(TimeoutError("not ready")),
        ConnectionResetError("reset"),
        login_response(),
    ])
    monkeypatch.setattr(deploy.urllib.request, "urlopen", http)

    deploy.wait_for_http_ready()

    assert http.call_count == 4
    assert clock.sleeps == [0.25, 0.25, 0.25]
    request = http.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8765/login/"
    assert request.get_method() == "GET"
    assert request.get_header("Host") == "47.239.50.129"
    assert request.get_header("X-forwarded-proto") == "https"
    assert systemd.call_args.args[0] == [
        "systemctl", "show", "--property=ActiveState", "--value", "ai-interviewer"
    ]


def test_readiness_deadline_caps_every_attempt_and_sleep(deploy, readiness, monkeypatch):
    clock, systemd = readiness
    attempts = []

    def refused(request, timeout):
        remaining = 30 - clock.now
        assert remaining > 0
        assert 0 < timeout <= min(2, remaining)
        attempts.append((clock.now, timeout))
        clock.now += timeout
        raise URLError(ConnectionRefusedError("not bound"))

    monkeypatch.setattr(deploy.urllib.request, "urlopen", refused)

    with pytest.raises(TimeoutError, match="within 30 seconds"):
        deploy.wait_for_http_ready()

    assert clock.now == 30
    assert attempts[-1][1] < 2
    assert systemd.call_count == len(attempts)
    for command, (started, _) in zip(systemd.call_args_list, attempts, strict=True):
        assert 0 < command.kwargs["timeout"] <= min(2, 30 - started)


@pytest.mark.parametrize("status", [403, 500])
def test_http_errors_are_not_retried(deploy, readiness, monkeypatch, status):
    clock, _ = readiness
    error = HTTPError("http://127.0.0.1:8765/login/", status, "rejected", {}, None)
    http = Mock(side_effect=error)
    monkeypatch.setattr(deploy.urllib.request, "urlopen", http)

    with pytest.raises(HTTPError) as failure:
        deploy.wait_for_http_ready()

    assert failure.value is error
    assert http.call_count == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("response", [
    login_response(content=b"wrong page"),
    login_response(status=302),
])
def test_invalid_login_is_not_retried(deploy, readiness, monkeypatch, response):
    clock, _ = readiness
    http = Mock(return_value=response)
    monkeypatch.setattr(deploy.urllib.request, "urlopen", http)

    with pytest.raises(RuntimeError, match="login page is invalid"):
        deploy.wait_for_http_ready()

    assert http.call_count == 1
    assert clock.sleeps == []


def test_permanent_url_error_is_not_retried(deploy, readiness, monkeypatch):
    clock, _ = readiness
    error = URLError("unsupported protocol")
    http = Mock(side_effect=error)
    monkeypatch.setattr(deploy.urllib.request, "urlopen", http)

    with pytest.raises(URLError) as failure:
        deploy.wait_for_http_ready()

    assert failure.value is error
    assert http.call_count == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("state", ["inactive", "failed", "deactivating"])
def test_stopped_service_fails_before_http(deploy, readiness, monkeypatch, state):
    clock, systemd = readiness
    systemd.return_value.stdout = state + "\n"
    http = Mock()
    monkeypatch.setattr(deploy.urllib.request, "urlopen", http)

    with pytest.raises(RuntimeError, match="service is not running: " + state):
        deploy.wait_for_http_ready()

    http.assert_not_called()
    assert clock.sleeps == []


def test_response_after_deadline_cannot_establish_readiness(deploy, readiness, monkeypatch):
    clock, _ = readiness

    def late_response(request, timeout):
        clock.now = 30
        return login_response()

    http = Mock(side_effect=late_response)
    monkeypatch.setattr(deploy.urllib.request, "urlopen", http)

    with pytest.raises(TimeoutError, match="within 30 seconds"):
        deploy.wait_for_http_ready()

    assert http.call_count == 1
    assert clock.sleeps == []


@pytest.fixture
def release_context(deploy, monkeypatch, tmp_path):
    revision = "d" * 40
    release = tmp_path / "releases" / revision
    release.mkdir(parents=True)
    marker = tmp_path / "deployed-revision"
    marker.write_text("a" * 40 + "\n")
    current = tmp_path / "current"
    original_resolve = Path.resolve
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "deploy " + revision)
    monkeypatch.setattr(deploy.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(deploy.fcntl, "flock", Mock())
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == current)
    monkeypatch.setattr(
        Path, "resolve", lambda path: release if path == current else original_resolve(path)
    )
    upload = io.BytesIO(b"duplicate release archive")
    monkeypatch.setattr(deploy.sys, "stdin", SimpleNamespace(buffer=upload))
    ready = Mock()
    smoke = Mock()
    commands = Mock()
    extract = Mock()
    monkeypatch.setattr(deploy, "wait_for_http_ready", ready)
    monkeypatch.setattr(deploy, "app_command", smoke)
    monkeypatch.setattr(deploy, "run", commands)
    monkeypatch.setattr(deploy, "extract_release", extract)
    return SimpleNamespace(
        revision=revision, release=release, marker=marker, current=current, upload=upload,
        ready=ready, smoke=smoke, commands=commands, extract=extract,
    )


def test_current_release_with_stale_marker_is_verified_once(deploy, release_context):
    context = release_context
    events = []
    context.ready.side_effect = lambda: events.append("ready")

    def smoke(release, arguments):
        assert context.marker.read_text() == "a" * 40 + "\n"
        assert events == ["ready"]
        assert release == context.release
        assert arguments == [str(context.release / "deploy/smoke.py")]
        events.append("smoke")

    context.smoke.side_effect = smoke

    deploy.main()

    assert events == ["ready", "smoke"]
    context.ready.assert_called_once_with()
    context.smoke.assert_called_once()
    assert context.upload.read() == b""
    assert context.marker.read_text() == context.revision + "\n"
    context.commands.assert_not_called()
    context.extract.assert_not_called()


def test_current_release_smoke_failure_preserves_stale_marker(deploy, release_context):
    context = release_context
    context.smoke.side_effect = RuntimeError("business smoke failed")

    with pytest.raises(RuntimeError, match="business smoke failed"):
        deploy.main()

    context.ready.assert_called_once_with()
    context.smoke.assert_called_once()
    assert context.marker.read_text() == "a" * 40 + "\n"
    assert context.current.resolve() == context.release
    assert context.release.is_dir()
    context.commands.assert_not_called()
    context.extract.assert_not_called()


def test_current_release_readiness_failure_does_not_run_smoke(deploy, release_context):
    context = release_context
    context.ready.side_effect = TimeoutError("not ready")

    with pytest.raises(TimeoutError, match="not ready"):
        deploy.main()

    context.smoke.assert_not_called()
    assert context.marker.read_text() == "a" * 40 + "\n"
    context.commands.assert_not_called()


def test_oversized_reupload_does_not_verify_or_write_marker(deploy, release_context, monkeypatch):
    context = release_context
    monkeypatch.setattr(deploy, "ARCHIVE_LIMIT", 8)

    with pytest.raises(ValueError, match="archive exceeds limit"):
        deploy.main()

    context.ready.assert_not_called()
    context.smoke.assert_not_called()
    assert context.marker.read_text() == "a" * 40 + "\n"
    context.commands.assert_not_called()


def test_noncurrent_partial_release_is_rejected(deploy, release_context, monkeypatch):
    context = release_context
    monkeypatch.setattr(Path, "resolve", lambda path: context.release.parent / "other")

    with pytest.raises(FileExistsError):
        deploy.main()

    context.ready.assert_not_called()
    context.smoke.assert_not_called()
    context.commands.assert_not_called()
    context.extract.assert_not_called()
    assert context.marker.read_text() == "a" * 40 + "\n"


def test_recorded_release_must_still_be_current(deploy, release_context, monkeypatch):
    context = release_context
    context.marker.write_text(context.revision + "\n")
    monkeypatch.setattr(Path, "resolve", lambda path: context.release.parent / "other")

    with pytest.raises(RuntimeError, match="not the current release"):
        deploy.main()

    context.ready.assert_not_called()
    context.smoke.assert_not_called()
    context.commands.assert_not_called()


def test_new_release_waits_before_single_full_smoke(deploy, release_context, monkeypatch):
    context = release_context
    context.release.rmdir()
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    monkeypatch.setattr(Path, "symlink_to", Mock())
    monkeypatch.setattr(Path, "replace", Mock())
    events = []
    context.commands.side_effect = lambda arguments: events.append(arguments)
    context.ready.side_effect = lambda: events.append("ready")
    context.smoke.side_effect = lambda release, arguments: events.append(arguments)

    deploy.main()

    full_smoke = [str(context.release / "deploy/smoke.py")]
    start = ["systemctl", "enable", "--now", "ai-interviewer-celery", "ai-interviewer"]
    assert events.index(start) < events.index("ready") < events.index(full_smoke)
    assert events.count(full_smoke) == 1
    assert events.count("ready") == 1
    assert ["/bin/sleep", "2"] not in events
    assert context.marker.read_text() == context.revision + "\n"
