"""职责：显式执行真实 Linux/WSL PDF 沙箱联调，不调用付费视觉模型。
实现：合成 PDF 对照既有算法；受控慢工作进程验证超时和取消，固定异常验证失败脱敏。
关联：pdf_sandbox、pdf_worker 及测试 PDF 构造器；需先按 sandbox/README.md 装配环境。

目录：
- exercise_worker：用临时受控工作进程验证监督器失败和取消后实际退出。
- exercise_worker.configured：只替换测试挂载与测试墙钟上限，保持真实隔离启动参数。
- exercise_worker.observe：标记桥接进程已启动，再委派原有有界读取器。
- exercise_worker.launch：记录真实桥接进程，供清理后的退出断言使用。
- exercise_worker.worker_present：从真实 Linux 进程快照确认本次临时沙箱是否存在。
- main：验证真实隔离证据、两页解析一致性、无效 PDF、超时与取消。

关键变量：
- ROOT：后端根目录，也是测试临时源码的唯一父目录。

约束：
受控 worker 仅存在于忽略的 test-results 临时目录，生产没有模拟开关或备用实现。
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


async def exercise_worker(source, *, cancel=False, crash=False):
    """用临时受控工作进程验证监督器失败和取消后实际退出；输入为原工作源码及测试模式。"""
    from interviews import pdf_sandbox

    original_command = pdf_sandbox.command
    original_reader = pdf_sandbox.read_bounded
    original_launch = asyncio.create_subprocess_exec
    entered = asyncio.Event()
    processes = []
    with tempfile.TemporaryDirectory(prefix="pdf-sandbox-", dir=ROOT / "test-results") as directory:
        path = Path(directory)
        suffix = (
            "raise SystemExit(3)"
            if crash
            else "print('ready', flush=True)\nimport time\ntime.sleep(60)"
        )
        worker = source.split('if __name__ == "__main__":')[0]
        worker += "install_limits(int(sys.argv[1]), int(sys.argv[2]))\n" + suffix + "\n"
        (path / "pdf_worker.py").write_text(worker, encoding="utf-8")
        (path / "resume_pdf.py").write_bytes((ROOT / "interviews/resume_pdf.py").read_bytes())

        def configured(mode):
            """只替换测试挂载与测试墙钟上限，保持真实隔离启动参数；返回命令、输出限额和秒数。"""
            args, limit, _ = original_command(mode)
            wall = 10 if cancel else 1
            args[-7] = pdf_sandbox.linux_path(path)
            args[-3] = str(wall)
            return args, limit, wall

        async def observe(reader, limit):
            """标记桥接进程已启动，再委派原有有界读取器；不把桥接启动等同于解析就绪。"""
            # 监督器只在工作进程结束后交付结果，不能用 stdout 就绪作为取消前提。
            entered.set()
            return await original_reader(reader, limit)

        async def launch(*args, **kwargs):
            """记录真实桥接进程，供清理后的退出断言使用；不替换操作系统隔离行为。"""
            process = await original_launch(*args, **kwargs)
            processes.append(process)
            return process

        async def worker_present():
            """从真实 Linux 进程快照确认本次临时沙箱是否存在；只匹配唯一测试路径，不输出进程表。"""
            args, _, _ = original_command("parse")
            prefix = args[:4] if os.name == "nt" else []
            snapshot = await original_launch(
                *prefix,
                "/bin/ps",
                "-eo",
                "args",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await asyncio.wait_for(snapshot.communicate(), 5)
            assert snapshot.returncode == 0
            return any(
                b"/tools/usr/bin/bwrap" in line and pdf_sandbox.linux_path(path).encode() in line
                for line in output.splitlines()
            )

        with (
            patch.object(pdf_sandbox, "command", configured),
            patch.object(pdf_sandbox, "read_bounded", observe),
            patch.object(asyncio, "create_subprocess_exec", launch),
        ):
            task = asyncio.create_task(pdf_sandbox.run_sandbox(b"controlled test"))
            if cancel:
                observed = False
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    for _ in range(10):
                        if await worker_present():
                            observed = True
                            break
                        await asyncio.sleep(0.1)
                finally:
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("Cancellation did not propagate")
                assert observed, "Test worker was never observed before cancellation"
                assert not await worker_present(), "Sandbox survived cancellation"
            else:
                try:
                    await task
                except pdf_sandbox.PdfInputError as exc:
                    expected = "失败" if crash else "超时"
                    assert expected in str(exc), str(exc)
                else:
                    raise AssertionError("Controlled worker must not succeed")
            assert len(processes) == 1 and processes[0].returncode == 0
            print(
                "PASS sandbox",
                "cancellation" if cancel else "crash" if crash else "timeout",
                "supervisor exited after worker cleanup",
            )


async def main():
    """验证真实隔离证据、两页解析一致性、无效 PDF、超时与取消；所有输入均为合成数据。"""
    import sys

    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from interviews.pdf_sandbox import parse_pdf, run_sandbox
    from interviews.resume_pdf import PdfInputError, extract_pdf
    from interviews.tests.test_resume_pdf import make_pdf

    evidence = await run_sandbox(b"", mode="probe")
    for name in (
        "host_hidden",
        "secrets_absent",
        "socket_blocked",
        "fork_blocked",
        "memory_enforced",
    ):
        assert evidence.get(name) is True, name
    assert evidence["memory_bytes"] == int(os.getenv("PDF_SANDBOX_MEMORY_MIB", "768")) * 1024 * 1024
    assert evidence["cpu_seconds"] == int(os.getenv("PDF_SANDBOX_CPU_SECONDS", "20"))
    print("PASS sandbox real isolation probe:", evidence)
    data = make_pdf(2)
    pages = await parse_pdf(data)
    baseline = extract_pdf(data)
    assert [(p.number, p.raw_text, p.text) for p in pages] == [
        (p.number, p.raw_text, p.text) for p in baseline
    ]
    assert all(page.image_png.startswith(b"\x89PNG") for page in pages)
    print("PASS sandbox two-page extraction equals original algorithm, PNG returned")
    for data in (b"broken PDF", make_pdf(encrypted=True), make_pdf(11)):
        try:
            await parse_pdf(data)
        except PdfInputError:
            pass
        else:
            raise AssertionError("Invalid PDF accepted")
    print("PASS sandbox corrupt, encrypted and excessive-page PDF rejected")
    source = (ROOT / "interviews/pdf_worker.py").read_text(encoding="utf-8")
    (ROOT / "test-results").mkdir(exist_ok=True)
    await exercise_worker(source)
    await exercise_worker(source, crash=True)
    await exercise_worker(source, cancel=True)


if __name__ == "__main__":
    asyncio.run(main())
