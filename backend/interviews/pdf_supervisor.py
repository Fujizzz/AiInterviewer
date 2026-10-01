"""职责：在 Linux/WSL 中监督一次 bubblewrap 工作进程，控制超时、输出与父连接中断。

实现：stdin 使用长度前缀传 PDF，之后保持打开作取消通道；selectors 同时监听取消和输出。
关联：pdf_sandbox 启动本文件；本文件不导入解析库，PDF 只进入隔离工作进程。

目录：
- read_exact：从原始 stdin 读取指定字节，提前 EOF 明确失败。
- sandbox_command：构建固定只读挂载与空网络命名空间，不挂载父项目或用户目录。
- supervise：有界读取工作进程输出，超时或父管道关闭时杀死并等待进程组。
- supervise.feed：将已经有界的 PDF 写入工作进程管道，不保存临时文件。
- main：解析可信启动参数，读取输入帧，输出监督结果。

关键变量：
（无模块级变量。）
约束：
只在 Linux 执行；所有命令使用 argv，不调用 shell。stderr 不回传以避免解析库泄露文档正文。
父连接关闭触发取消，正常结果返回前等待工作进程退出；不重试、不启用非隔离解析。
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
    """从原始 stdin 读取指定字节，提前 EOF 明确失败；仅接受上游已检查的有界长度。"""
    result = bytearray()
    while len(result) < count:
        block = os.read(0, count - len(result))
        if not block:
            raise EOFError("Parent input closed")
        result.extend(block)
    return bytes(result)


def sandbox_command(runtime, source, agent_source, memory, cpu, mode):
    """构建固定只读挂载与空网络命名空间，不挂载父项目或用户目录。

    输入路径和限额只由后端配置提供；返回 argv，路径空格不会被 shell 解释。
    agents 以只含 resume_cleanup 的命名空间包挂载，避免加载 Agent 初始化器或 .env。
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
    """有界读取工作进程输出，超时或父管道关闭时杀死并等待进程组。

    返回原始 JSON bytes；限额、崩溃和超时返回固定错误 JSON。单次最多 output_limit 字节，
    不把工作进程无限输出载入内存。CPU/内存限制由工作进程在解析前设置。
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
        """将已经有界的 PDF 写入工作进程管道；进程提前退出的 BrokenPipe 由主循环判定。"""
        try:
            child.stdin.write(data)
            child.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            try:
                child.stdin.close()
            except BrokenPipeError:
                # close 可能再次刷新缓冲；主循环负责报告进程退出，不重复产生线程异常。
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
    """解析可信启动参数，读取输入帧，输出监督结果；错误不输出文档或系统环境。"""
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
