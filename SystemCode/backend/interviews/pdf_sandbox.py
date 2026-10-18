"""Responsibilities: start a Linux/WSL PDF sandbox from backend and validate bounded output data,
without executing local parsing fallback.

Implementation: fixed argv and minimal environment to launch supervisor; keep stdin as cancellation
channel, read bounded output and verify PNG header.
Related Modules: resume_api calls parse_pdf; pdf_supervisor manages isolated worker processes,
visual models remain in backend.

Declaration Index:
- linux_path: convert resolved local path to WSL mount path, reject unsupported network share paths.
- command: validate dedicated configuration and construct supervisor command and output limit.
- read_bounded: accumulate limited stdout, prohibit unbounded communicate buffer.
- run_sandbox: manage input, result, cancellation, and supervisor exit, return JSON object.
- parse_pdf: validate page count, text, page numbers, and PNG dimensions, convert sandbox output to
  ResumePage.

Variable Index:
- logger: logs only execution phase, output byte count, and exception type, does not save document
  or subprocess stderr.

Configuration notes:
PDF_SANDBOX_RUNTIME must point to Linux dependency directory; Windows also requires
PDF_SANDBOX_DISTRO configuration.
New default security limits: 768 MiB address space, 20 CPU seconds, 30 seconds wall clock, 160 MiB
output; old extraction parameters unchanged.
Supervisor has additional 10 seconds for startup/cleanup margin; sandbox unavailable or
misconfigured results in explicit failure, no thread-based parsing fallback path available.
"""

import asyncio
import base64
import binascii
import json
import logging
import os
import struct
import subprocess
from pathlib import Path

from agents.resume_cleanup import ResumePage

from .resume_pdf import IMAGE_EDGE, MAX_BYTES, MAX_PAGES, MAX_TEXT, PdfInputError

logger = logging.getLogger(__name__)


def linux_path(path):
    """Convert resolved local path to WSL mount path, reject unsupported network share paths; no
    shell execution.
    """
    path = Path(path).resolve()
    if os.name != "nt":
        return str(path)
    if len(path.drive) != 2 or path.drive[1] != ":":
        raise ValueError("Sandbox source must be on a local drive")
    return "/mnt/" + path.drive[0].lower() + "/" + "/".join(path.parts[1:])


def command(mode):
    """Validate dedicated configuration and construct supervisor command and output limit; mode only
    allows internal parse/probe.
    """
    runtime = os.environ.get("PDF_SANDBOX_RUNTIME", "")
    if not runtime.startswith("/") or mode not in {"parse", "probe"}:
        raise ValueError("Configure PDF_SANDBOX_RUNTIME as an absolute Linux path")
    memory = int(os.getenv("PDF_SANDBOX_MEMORY_MIB", "768"))
    cpu = int(os.getenv("PDF_SANDBOX_CPU_SECONDS", "20"))
    wall = int(os.getenv("PDF_SANDBOX_WALL_SECONDS", "30"))
    if min(memory, cpu, wall) < 1:
        raise ValueError("Sandbox resource limits must be positive")
    output_limit = 160 * 1024 * 1024
    source = Path(__file__).resolve().parent
    args = [
        "/usr/bin/python3",
        "-I",
        linux_path(source / "pdf_supervisor.py"),
        runtime,
        linux_path(source),
        linux_path(source.parents[1] / "agents/resume_cleanup.py"),
        str(memory),
        str(cpu),
        str(wall),
        str(output_limit),
        mode,
    ]
    if os.name == "nt":
        distro = os.getenv("PDF_SANDBOX_DISTRO", "")
        if not distro:
            raise ValueError("Configure PDF_SANDBOX_DISTRO")
        args = [
            str(Path(os.environ["SystemRoot"]) / "System32/wsl.exe"),
            "-d",
            distro,
            "--exec",
            *args,
        ]
    return args, output_limit, wall


async def read_bounded(reader, limit):
    """Accumulate limited stdout, prohibit unbounded communicate buffer; exceed limit throws fixed
    error, no partial result returned.
    """
    result = bytearray()
    while block := await reader.read(65536):
        result.extend(block)
        if len(result) > limit:
            raise PdfInputError("The PDF sandbox output exceeded its limit.")
    return bytes(result)


async def run_sandbox(data, mode="parse"):
    """Manage input, result, cancellation, and supervisor exit, return JSON object; no shell or
    parent process full environment used.

    data is PDF not exceeding MAX_BYTES, probe is only internal test entry. On cancellation, close
    control pipe and wait for supervisor to kill worker process; continue draining stdout to avoid
    subprocess blocking when cancellation coincides with large output.
    """
    if len(data) > MAX_BYTES:
        raise PdfInputError("The PDF must be non-empty and no larger than 10 MiB.")
    try:
        args, output_limit, wall = command(mode)
    except (ValueError, KeyError) as exc:
        raise PdfInputError(
            "The PDF sandbox configuration is invalid. Check "
            "its dedicated runtime and resource limits."
        ) from exc
    env = {
        key: os.environ[key]
        for key in ("SystemRoot", "WINDIR", "USERPROFILE", "PATH")
        if key in os.environ
    }
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as exc:
        logger.error(
            "PDF supervisor launch failed exception=%s; check Linux/WSL runtime", type(exc).__name__
        )
        raise PdfInputError(
            "The PDF sandbox could not start. Check the Linux/WSL runtime environment."
        ) from exc
    output = asyncio.create_task(read_bounded(process.stdout, output_limit))
    try:
        # Same deadline covers input pipe blocking, startup, and result reading; cannot restrict
        # only post-parsing read phase.
        async with asyncio.timeout(wall + 10):
            process.stdin.write(len(data).to_bytes(8, "big") + data)
            await process.stdin.drain()
            raw = await asyncio.shield(output)
            await process.wait()
        if process.returncode:
            raise PdfInputError("The PDF sandbox could not run. Check the isolated environment.")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid sandbox response")
        if "error" in result:
            messages = {
                "invalid_pdf": (
                    "The PDF could not be parsed or exceeds the "
                    "existing page or text limits. Check the file."
                ),
                "sandbox_timeout": (
                    "PDF extraction and rendering timed out; processing was stopped."
                ),
                "sandbox_worker_failed": (
                    "The PDF parsing process failed or reached a "
                    "resource limit; processing was stopped."
                ),
                "sandbox_output_limit": (
                    "The PDF sandbox output exceeded its limit; processing was stopped."
                ),
            }
            raise PdfInputError(
                messages.get(result["error"], "The PDF sandbox could not complete processing.")
            )
        logger.info("PDF sandbox completed mode=%s output_bytes=%d", mode, len(raw))
        return result
    except asyncio.CancelledError:
        logger.info("PDF sandbox cancelled; closing supervisor control pipe")
        raise
    except TimeoutError as exc:
        raise PdfInputError("The PDF sandbox response timed out; processing was stopped.") from exc
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            logger.error("PDF supervisor did not exit within cleanup deadline; terminating bridge")
            process.kill()
            await process.wait()
        await asyncio.gather(output, return_exceptions=True)


async def parse_pdf(data):
    """Validate page count, text, page numbers, and PNG dimensions, convert sandbox output to
    ResumePage.

    Backend does not use image parsing library to decode untrusted output; only validates fixed
    PNG/IHDR bytes and bounded base64.
    Invalid structure, extra fields, or out-of-order pages cause immediate failure; no cropping, no
    page ignoring, no local parsing enabled.
    """
    result = await run_sandbox(data)
    pages = result.get("pages")
    if set(result) != {"pages"} or not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise PdfInputError("The PDF sandbox returned an invalid page structure.")
    checked = []
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict) or set(page) != {
            "number",
            "raw_text",
            "text",
            "warnings",
            "image_png",
        }:
            raise PdfInputError("The PDF sandbox returned invalid page fields.")
        if type(page["number"]) is not int or page["number"] != number:
            raise PdfInputError("The PDF sandbox returned pages in the wrong order.")
        # Original algorithm expands ffi/ffl ligatures into three characters; here only accepts
        # maximum length possible from existing normalization.
        if any(
            not isinstance(page[key], str)
            or len(page[key]) > MAX_TEXT * (3 if key == "text" else 1)
            for key in ("raw_text", "text")
        ):
            raise PdfInputError("The PDF sandbox returned text that exceeds the limit.")
        warnings = page["warnings"]
        if (
            not isinstance(warnings, list)
            or len(warnings) > 10
            or any(not isinstance(item, str) or len(item) > 1000 for item in warnings)
        ):
            raise PdfInputError("The PDF sandbox returned an invalid warning.")
        encoded = page["image_png"]
        if not isinstance(encoded, str) or len(encoded) > 20 * 1024 * 1024:
            raise PdfInputError("The PDF sandbox returned an oversized image.")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PdfInputError("The PDF sandbox returned an invalid image encoding.") from exc
        if len(image) < 33 or image[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
            raise PdfInputError("The PDF sandbox returned an invalid image.")
        if any(not 1 <= size <= IMAGE_EDGE for size in struct.unpack(">II", image[16:24])):
            raise PdfInputError("The PDF sandbox returned image dimensions that exceed the limit.")
        checked.append(ResumePage(number, page["raw_text"], page["text"], warnings, image))
    return checked
