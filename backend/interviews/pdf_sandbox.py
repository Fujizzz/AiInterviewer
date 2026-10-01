"""职责：从后端启动 Linux/WSL PDF 沙箱并校验返回的有界数据，不执行本机解析回退。

实现：固定 argv 与最小环境启动监督器；保持 stdin 作取消通道，输出有界读取并验证 PNG 头。
关联：resume_api 调用 parse_pdf；pdf_supervisor 管理隔离工作进程，视觉模型留在后端。

目录：
- linux_path：将已解析本机路径转为 WSL 挂载路径，拒绝不支持的网络共享路径。
- command：校验专用配置并构造监督器命令与输出上限。
- read_bounded：累计有限 stdout，禁止无界 communicate 缓冲。
- run_sandbox：管理输入、结果、取消和监督器退出，返回 JSON 对象。
- parse_pdf：校验页数、文字、页码与 PNG 尺寸，将沙箱输出转为 ResumePage。

关键变量：
- logger：仅记录执行阶段、输出字节数与异常类型，不保存文档或子进程 stderr。

配置说明：
PDF_SANDBOX_RUNTIME 必须指向 Linux 依赖目录；Windows 还必须配置 PDF_SANDBOX_DISTRO。
新增默认安全限制：768 MiB 地址空间、20 CPU 秒、30 秒墙钟、160 MiB 输出；不改变旧提取参数。
监督器额外有 10 秒启动/清理余量；沙箱不可用或配置错误即明确失败，无线程解析备用路径。
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
    """将已解析本机路径转为 WSL 挂载路径，拒绝不支持的网络共享路径；不执行 shell。"""
    path = Path(path).resolve()
    if os.name != "nt":
        return str(path)
    if len(path.drive) != 2 or path.drive[1] != ":":
        raise ValueError("Sandbox source must be on a local drive")
    return "/mnt/" + path.drive[0].lower() + "/" + "/".join(path.parts[1:])


def command(mode):
    """校验专用配置并构造监督器命令与输出上限；mode 仅允许内部 parse/probe。"""
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
    """累计有限 stdout，禁止无界 communicate 缓冲；超限抛固定错误，不返回部分结果。"""
    result = bytearray()
    while block := await reader.read(65536):
        result.extend(block)
        if len(result) > limit:
            raise PdfInputError("PDF 沙箱输出超限。")
    return bytes(result)


async def run_sandbox(data, mode="parse"):
    """管理输入、结果、取消和监督器退出，返回 JSON 对象；不使用 shell 或父进程完整环境。

    data 是不超过 MAX_BYTES 的 PDF，probe 仅为内部测试入口。取消时关闭控制管道并等待监督器
    杀死工作进程；stdout 读取任务继续排空，避免取消恰逢大结果输出时子进程堵塞。
    """
    if len(data) > MAX_BYTES:
        raise PdfInputError("PDF 必须非空且不超过 10 MiB。")
    try:
        args, output_limit, wall = command(mode)
    except (ValueError, KeyError) as exc:
        raise PdfInputError("PDF 沙箱配置无效，请检查专用运行环境与资源限制。") from exc
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
        raise PdfInputError("PDF 沙箱无法启动，请检查 Linux/WSL 运行环境。") from exc
    output = asyncio.create_task(read_bounded(process.stdout, output_limit))
    try:
        # 同一截止时间涵盖输入管道阻塞、启动和结果读取，不能仅限制解析后的读取阶段。
        async with asyncio.timeout(wall + 10):
            process.stdin.write(len(data).to_bytes(8, "big") + data)
            await process.stdin.drain()
            raw = await asyncio.shield(output)
            await process.wait()
        if process.returncode:
            raise PdfInputError("PDF 沙箱未能运行，请检查隔离环境。")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid sandbox response")
        if "error" in result:
            messages = {
                "invalid_pdf": "PDF 无法解析或超出原有页数、文本限制，请检查文件。",
                "sandbox_timeout": "PDF 提取与渲染超时，处理已终止。",
                "sandbox_worker_failed": "PDF 解析进程失败或触发资源限制，处理已终止。",
                "sandbox_output_limit": "PDF 沙箱输出超限，处理已终止。",
            }
            raise PdfInputError(messages.get(result["error"], "PDF 沙箱未能完成处理。"))
        logger.info("PDF sandbox completed mode=%s output_bytes=%d", mode, len(raw))
        return result
    except asyncio.CancelledError:
        logger.info("PDF sandbox cancelled; closing supervisor control pipe")
        raise
    except TimeoutError as exc:
        raise PdfInputError("PDF 沙箱响应超时，处理已终止。") from exc
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
    """校验页数、文字、页码与 PNG 尺寸，将沙箱输出转为 ResumePage。

    后端不使用图片解析库解码不可信输出，只验证固定 PNG/IHDR 字节与有界 base64。
    非法结构、额外字段或错序页直接失败，不裁剪、不忽略页面或启用本机解析。
    """
    result = await run_sandbox(data)
    pages = result.get("pages")
    if set(result) != {"pages"} or not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise PdfInputError("PDF 沙箱返回了无效的页面结构。")
    checked = []
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict) or set(page) != {
            "number",
            "raw_text",
            "text",
            "warnings",
            "image_png",
        }:
            raise PdfInputError("PDF 沙箱返回了无效的页面字段。")
        if type(page["number"]) is not int or page["number"] != number:
            raise PdfInputError("PDF 沙箱返回了错序页面。")
        # 原算法将 ffi/ffl 连字展开为三个字符；这里只接受既有规范化可能产生的最大长度。
        if any(
            not isinstance(page[key], str)
            or len(page[key]) > MAX_TEXT * (3 if key == "text" else 1)
            for key in ("raw_text", "text")
        ):
            raise PdfInputError("PDF 沙箱返回了超限文本。")
        warnings = page["warnings"]
        if (
            not isinstance(warnings, list)
            or len(warnings) > 10
            or any(not isinstance(item, str) or len(item) > 1000 for item in warnings)
        ):
            raise PdfInputError("PDF 沙箱返回了无效提示。")
        encoded = page["image_png"]
        if not isinstance(encoded, str) or len(encoded) > 20 * 1024 * 1024:
            raise PdfInputError("PDF 沙箱返回了超限图像。")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PdfInputError("PDF 沙箱返回了无效图像编码。") from exc
        if len(image) < 33 or image[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
            raise PdfInputError("PDF 沙箱返回了无效图像。")
        if any(not 1 <= size <= IMAGE_EDGE for size in struct.unpack(">II", image[16:24])):
            raise PdfInputError("PDF 沙箱返回了超限图像尺寸。")
        checked.append(ResumePage(number, page["raw_text"], page["text"], warnings, image))
    return checked
