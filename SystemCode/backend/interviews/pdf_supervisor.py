"""Responsibilities: supervise a single bubblewrap worker process in Linux/WSL, controlling
timeouts, output, and parent connection interruption.

Implementation: use length-prefixed stdin for PDF, then keep open as cancellation channel; selectors
monitor both cancellation and output simultaneously.
Related Modules: pdf_sandbox launches this file; this file does not import parsing libraries, PDF
enters only isolated worker process.

Declaration Index:
- read_exact: read specified bytes from raw stdin, fail immediately on premature EOF.
- sandbox_command: build fixed read-only mounts and empty network namespace, do not mount parent
  project or user directories.
- supervise: bounded read of worker process output, kill and wait for process group on timeout or
  parent pipe closure.
- supervise.feed: write already bounded PDF into worker process pipeline, no temporary files saved.
- main: parse trusted startup arguments, read input frame, output supervision result.

Variable Index:
None
Constraints:
Only runs on Linux; all commands use argv, no shell invocation. stderr not returned to prevent
parsing library leakage of document content.
Parent connection closure triggers cancellation; wait for worker process exit before returning
normal result; no retry, no non-isolated parsing enabled.
"""

import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def read_exact(count):
    """Read specified bytes from raw stdin, fail immediately on premature EOF; only accepts
    previously checked bounded length from upstream.
    """
    result = bytearray()
    while len(result) < count:
        block = os.read(0, count - len(result))
        if not block:
            raise EOFError("Parent input closed")
        result.extend(block)
    return bytes(result)


def sandbox_command(runtime, source, agent_source, memory, cpu, mode):
    """Build fixed read-only mounts and empty network namespace, do not mount parent project or user
    directories.

    Input path and limits provided only by backend configuration; return argv, spaces in paths not
    interpreted by shell.
    agents mounted as namespace package containing only resume_cleanup, avoiding loading Agent
    initializers or .env.
    """
    return [
        str(Path(runtime) / "tools/usr/bin/bwrap"),
        "--unshare-all",
        "--die-with-parent",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--ro-bind",
        str(Path(runtime) / "lib"),
        "/runtime/lib",
        "--ro-bind",
        str(Path(source) / "pdf_worker.py"),
        "/worker/pdf_worker.py",
        "--ro-bind",
        str(Path(source) / "resume_pdf.py"),
        "/worker/resume_pdf.py",
        "--ro-bind",
        str(agent_source),
        "/worker/agents/resume_cleanup.py",
        "--remount-ro",
        "/",
        "--chdir",
        "/worker",
        "/usr/bin/python3",
        "-I",
        "-B",
        "/worker/pdf_worker.py",
        str(memory),
        str(cpu),
        mode,
    ]


def supervise(command, data, wall_seconds, output_limit):
    """Bounded read of worker process output, kill and wait for process group on timeout or parent
    pipe closure.

    Return raw JSON bytes; limit exceeded, crash, or timeout return fixed error JSON. Maximum
    output_limit bytes per single read,
    no infinite output loaded into memory. CPU/memory limits set by worker process before parsing.
    """
    child = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )

    def feed():
        """Write already bounded PDF into worker process pipeline; BrokenPipe from premature process
        exit detected by main loop.
        """
        try:
            child.stdin.write(data)
            child.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            try:
                child.stdin.close()
            except BrokenPipeError:
                # close may flush buffer again; main loop responsible for reporting process exit, no
                # repeated thread exceptions generated.
                pass

    writer = threading.Thread(target=feed)
    writer.start()
    output = bytearray()
    deadline = time.monotonic() + wall_seconds
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(0, selectors.EVENT_READ, "parent")
            selector.register(child.stdout, selectors.EVENT_READ, "worker")
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b'{"error":"sandbox_timeout"}'
                for key, _ in selector.select(remaining):
                    if key.data == "parent":
                        return b'{"error":"sandbox_cancelled"}'
                    block = os.read(child.stdout.fileno(), 65536)
                    if not block:
                        code = child.wait(timeout=max(0.01, deadline - time.monotonic()))
                        return bytes(output) if code == 0 else b'{"error":"sandbox_worker_failed"}'
                    output.extend(block)
                    if len(output) > output_limit:
                        return b'{"error":"sandbox_output_limit"}'
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        writer.join()
        child.stdout.close()


def main():
    """Parse trusted startup arguments, read input frame, output supervision result; no document or
    system environment output on error.
    """
    runtime, source, agent_source, memory, cpu, wall, output_limit, mode = sys.argv[1:]
    length = int.from_bytes(read_exact(8), "big")
    if not 0 <= length <= 10 * 1024 * 1024:
        raise ValueError("Invalid PDF frame length")
    data = read_exact(length)
    try:
        result = supervise(
            sandbox_command(runtime, source, agent_source, int(memory), int(cpu), mode),
            data,
            int(wall),
            int(output_limit),
        )
    except (OSError, subprocess.TimeoutExpired):
        result = json.dumps({"error": "sandbox_unavailable"}).encode()
    sys.stdout.buffer.write(result)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
