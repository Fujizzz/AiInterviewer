"""Responsibilities: Explicitly exercise the real Linux/WSL PDF sandbox without calling a paid
vision model.
Implementation: Compare synthetic PDF extraction with the existing algorithm and test worker
timeout, cancellation, crash, and error redaction.
Related Modules: interviews.pdf_sandbox and pdf_worker; synthetic PDF construction comes from
test_resume_pdf.py.
Declaration Index:
- exercise_worker: Run a controlled temporary worker and verify supervisor cleanup after failure or
  cancellation.
- exercise_worker.configured: Substitute only test mount paths and wall-clock limits while
  preserving isolation launch arguments.
- exercise_worker.observe: Record bridge startup and delegate to the existing bounded output reader.
- exercise_worker.launch: Retain the real bridge process for exit assertions after cleanup.
- exercise_worker.worker_present: Detect this temporary sandbox in a real Linux process snapshot.
- main: Verify isolation evidence, extraction parity, invalid PDFs, timeout, crash, and
  cancellation.
Variable Index:
- ROOT: Backend root and parent for the ignored test-results directory.

Constraints:
The controlled worker is confined to the ignored test-results directory; production has no
simulation switch or alternate implementation.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


async def exercise_worker(source, *, cancel=False, crash=False):
    """Functionality: Verify the sandbox supervisor exits after a controlled worker failure or
    cancellation.
    Inputs: Existing worker source and mutually exclusive test-mode flags.
    Outputs: None; assertions verify expected failure, observed process cleanup, and bridge exit
    status.
    Logic: Write a temporary worker variant and exercise it through the real sandbox launch path.
    Constraints: Uses real operating-system isolation; temporary test files are confined to
    test-results.
    """
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
            """Functionality: Adapt the sandbox command for a temporary worker test.
            Inputs: Sandbox operation mode.
            Outputs: Command arguments, output limit, and bounded test wall-clock seconds.
            Logic: Reuse the production command and replace only the temporary bind path and wall
            limit.
            Constraints: Preserve the operating-system isolation arguments and process behavior.
            """
            args, limit, _ = original_command(mode)
            wall = 10 if cancel else 1
            args[-7] = pdf_sandbox.linux_path(path)
            args[-3] = str(wall)
            return args, limit, wall

        async def observe(reader, limit):
            """Functionality: Mark bridge-process startup and delegate to the original bounded
            reader.
            Inputs: The process output reader and byte limit.
            Outputs: The original bounded-reader result.
            Logic: Set the entered event before awaiting the existing reader.
            Constraints: Bridge startup is not treated as worker readiness; cancellation does not
            depend on stdout.
            """
            # The supervisor delivers results only after worker exit, so stdout readiness cannot
            # gate cancellation.
            entered.set()
            return await original_reader(reader, limit)

        async def launch(*args, **kwargs):
            """Functionality: Record each real subprocess created by the sandbox launch path.
            Inputs: The original subprocess arguments and keyword arguments.
            Outputs: The newly created asyncio process.
            Logic: Delegate unchanged to the original launcher and retain the returned handle.
            Constraints: Does not replace or simulate operating-system isolation.
            """
            process = await original_launch(*args, **kwargs)
            processes.append(process)
            return process

        async def worker_present():
            """Functionality: Check whether the temporary sandbox worker exists in a real Linux
            process snapshot.
            Inputs: The current temporary test path captured by exercise_worker.
            Outputs: True only when a sandbox process command includes the unique test path.
            Logic: Run the original process snapshot command and inspect its argument rows.
            Constraints: Do not print the process table; Windows prefixes are preserved for the host
            command.
            """
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
                    expected = (
                        "The PDF parsing process failed or reached a resource limit; processing was stopped."
                        if crash else
                        "PDF extraction and rendering timed out; processing was stopped."
                    )
                    assert str(exc) == expected, str(exc)
                else:
                    raise AssertionError("Controlled worker must not succeed")
            assert len(processes) == 1 and processes[0].returncode == 0
            print(
                "PASS sandbox",
                "cancellation" if cancel else "crash" if crash else "timeout",
                "supervisor exited after worker cleanup",
            )


async def main():
    """Functionality: Verify real sandbox isolation, extraction parity, invalid inputs, timeouts,
    crashes, and cancellation.
    Inputs: Synthetic PDFs and controlled worker source; deployment sandbox configuration comes from
    the environment.
    Outputs: None; assertions cover isolation evidence and expected parse/worker outcomes.
    Logic: Initialize Django, probe isolation, compare two-page extraction with the baseline, reject
    invalid PDFs, and run worker lifecycle cases.
    Constraints: All document data is synthetic and no paid vision model is invoked.
    """
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
