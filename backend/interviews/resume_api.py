"""职责：提供 PDF 上传及可取消的阶段流，不写业务数据库或保存简历。
实现：默认传统提取，advanced 显式启用视觉校对；在 Celery 或开发进程执行；multipart 有界读取；
PDF 库只在隔离进程解析，视觉最多三页并发并按实际完成数发进度。
关联：api.urls 注册 /api/resume/parse/；resumes.js 消费版本提取 NDJSON；Agent 校验转写。

目录：
- event_line：将单个事件编码为 NDJSON 字节。
- review_pages：按有限滑动窗口调度校对，失败或关闭时清理所有在途任务。
- parse_resume_pdf：验证上传，返回阶段流或请求错误。
- resume_events：执行规则后视觉校对、终态和资源释放。
- resume_failure_code：从固定异常码和安全诊断中识别失败类型，不回显原始异常。

关键变量：
- logger：记录请求标识、阶段、页数与耗时，不记录上传文件名或文本。
- VISION_CONCURRENCY：单份 PDF 最多同时执行的视觉校对请求数，设为 3。
- VISION_FAILURE_DETAILS：已知视觉失败码的固定中文提示；未知异常仍使用既有通用提示。

约束说明：
traditional 仅传统提取；advanced 必须完成规则提取和视觉校对，失败不回退为传统成功。
流开始后失败通过 error 事件表示，不能依靠 HTTP 200 判定完成。
Django 上传处理器可能临时落盘，响应关闭时框架删除临时文件；业务不保留文件。
单份 PDF 使用有界并发；失败或断连取消在途任务并停止调度，随后关闭客户端。
ASGI 入口限制多份上传总数；已被供应商接收的请求不保证停止计费。
"""

import asyncio
import json
import logging
from contextlib import aclosing
from time import perf_counter
from uuid import uuid4

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from openai import APITimeoutError

from agents.model_calls import safe_error_details
from agents.resume_cleanup import ResumeCleanupAgent

from .pdf_sandbox import parse_pdf
from .resume_pdf import MAX_BYTES, PdfInputError
from .resume_vision import ResumeVision

logger = logging.getLogger(__name__)
VISION_CONCURRENCY = 3
VISION_FAILURE_DETAILS = {
    "resume_correction_range_invalid": "视觉模型返回的修改行号越界或范围无效，已拒绝采用。",
    "resume_overlapping_corrections": "视觉模型返回了相互重叠的修改，已拒绝采用。",
    "resume_empty_correction": "视觉模型返回了缺少依据的修改，已拒绝采用。",
    "resume_page_mismatch": "视觉模型返回的页码与原页不一致，已拒绝采用。",
    "resume_page_omitted": "视觉模型的修改会删除整页内容，已拒绝采用。",
    "resume_image_required": "视觉校对缺少页面图像，请检查 PDF 渲染日志。",
    "resume_vision_empty_choices": "视觉服务未返回可用结果。",
    "resume_vision_incomplete_or_refused": "视觉服务返回不完整结果或拒绝了请求，已拒绝采用。",
    "timeout": "视觉服务请求超时；未采用任何未完成的校对结果。",
    "invalid_json": "视觉服务响应未通过 JSON 结构校验，已拒绝采用。",
}


def resume_failure_code(error):
    """输入任意异常，输出有限失败码；固定 ValueError 码优先，其余读取安全诊断，无正文或状态修改。"""
    if isinstance(error, APITimeoutError):
        return "timeout"
    if isinstance(error, ValueError) and str(error) in VISION_FAILURE_DETAILS:
        return str(error)
    return safe_error_details(error)["category"] or "resume_processing_failed"


def event_line(kind: str, **data) -> bytes:
    """输入事件类型和 JSON 字段，输出以换行结束的 UTF-8；不发送网络请求。"""
    return (json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n").encode("utf-8")


async def review_pages(agent, pages):
    """输入 Agent 和原序页面，按完成顺序产生校对结果；单份最多三项在途。

    先检查同批完成任务是否失败，再交付结果并补充窗口，避免已知失败后继续提交。
    all_tasks 保存本批全部任务以取回异常；finally 取消并等待未完成项，调用方随后才能
    关闭共享模型客户端。异步生成器须由调用者使用 aclosing 包裹，确保消费中断也清理。
    """
    remaining = iter(pages)
    pending = set()
    all_tasks = set()
    try:
        for _ in range(min(VISION_CONCURRENCY, len(pages))):
            task = asyncio.create_task(agent.clean_page(next(remaining)))
            pending.add(task)
            all_tasks.add(task)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            completed = [task.result() for task in done]
            for result in completed:
                yield result
            for _ in done:
                page = next(remaining, None)
                if page is None:
                    break
                task = asyncio.create_task(agent.clean_page(page))
                pending.add(task)
                all_tasks.add(task)
    finally:
        for task in all_tasks:
            if not task.done():
                task.cancel()
        # 业务错误由 task.result 原样传播；此处收集取消与同批异常，避免未取回任务异常。
        await asyncio.gather(*all_tasks, return_exceptions=True)


async def parse_resume_pdf(request):
    """输入已认证同源 multipart POST，输出 NDJSON 流或固定格式 4xx 错误。

    接受一个 file 和可选 mode（traditional 默认或 advanced）；内容由规则层再校验。
    有界读取避免把超限上传载入业务内存；上传解析在工作线程，避免阻塞 ASGI 循环。
    生产把一次性输入和进度交给 Redis/Celery，流关闭将通知 worker 取消。
    未修改全局 JSON API 解析器、WebSocket 消息上限和已有面试行为。
    """
    if request.method != "POST":
        response = JsonResponse({"error": "仅支持 POST。"}, status=405)
        response["Allow"] = "POST"
        return response
    files = await asyncio.to_thread(getattr, request, "FILES")
    mode = request.POST.get("mode", "traditional")
    if (
        set(request.POST) - {"mode"}
        or len(request.POST.getlist("mode")) > 1
        or mode not in {"traditional", "advanced"}
        or set(files) != {"file"}
        or len(files.getlist("file")) != 1
    ):
        return JsonResponse(
            {"error": "请提交一个 file，mode 仅支持 traditional 或 advanced。"}, status=400
        )
    upload = files["file"]
    if not 0 < upload.size <= MAX_BYTES:
        return JsonResponse({"error": "PDF 必须非空且不超过 10 MiB。"}, status=413)
    data = await asyncio.to_thread(upload.read, MAX_BYTES + 1)
    # 生产队列不可用时由队列层明确失败；开发 inline 是显式模式，绝非故障回退。
    if settings.PDF_TASK_EXECUTION == "celery":
        from .pdf_queue import queued_resume_events

        events = queued_resume_events(data, mode=mode)
    else:
        events = resume_events(data, mode=mode)
    response = StreamingHttpResponse(events, content_type="application/x-ndjson; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response


async def resume_events(data: bytes, *, mode="traditional"):
    """输入有界 PDF 和模式（默认 traditional），产生 progress/page/result 或 error。

    traditional 直接返回规则文本与提示，不构造模型；advanced 进行原有有界视觉校对。
    页面响应不包含内部修改建议；
    任一页失败不发送 result、不自动回退；取消向上传播且 finally 关闭客户端。
    错误事件附有限 code 与固定提示；日志记录阶段、请求标识、异常类型及数值状态，不回显正文。
    """
    request_id = uuid4().hex
    started = perf_counter()
    vision = None
    stage = "rules"
    logger.info("resume_pdf start request=%s bytes=%d", request_id, len(data))
    try:
        if mode not in {"traditional", "advanced"}:
            raise ValueError("invalid extraction mode")
        yield event_line("progress", stage=stage, detail="正在隔离环境中提取 PDF 文本并渲染页面")
        pages = await parse_pdf(data)
        if mode == "traditional":
            results = [
                {
                    "number": page.number,
                    "text": page.text,
                    "changed": False,
                    "uncertainties": page.warnings,
                }
                for page in pages
            ]
            yield event_line(
                "result",
                mode=mode,
                text="\n\n".join(page.text for page in pages),
                pages=results,
                changed_pages=0,
            )
            return
        stage = "configuration"
        vision = ResumeVision()
        agent = ResumeCleanupAgent(vision)
        results_by_page = {}
        stage = "vision"
        logger.info(
            "resume_pdf vision request=%s pages=%d concurrency=%d",
            request_id,
            len(pages),
            VISION_CONCURRENCY,
        )
        yield event_line("progress", stage=stage, detail=f"视觉校对：已完成 0/{len(pages)} 页")
        async with aclosing(review_pages(agent, pages)) as reviews:
            async for result in reviews:
                # 修改建议仅供内部定位校验；HTTP 只交付修改结果和无法确认的疑点。
                public = result.model_dump(exclude={"corrections"})
                results_by_page[result.number] = public
                logger.info(
                    "resume_pdf page_completed request=%s page=%d completed=%d total=%d",
                    request_id,
                    result.number,
                    len(results_by_page),
                    len(pages),
                )
                yield event_line("page", **public)
                yield event_line(
                    "progress",
                    stage=stage,
                    detail=f"视觉校对：已完成 {len(results_by_page)}/{len(pages)} 页",
                )
        results = [results_by_page[page.number] for page in pages]
        text = "\n\n".join(result["text"] for result in results)
        yield event_line(
            "result",
            mode=mode,
            text=text,
            pages=results,
            changed_pages=sum(result["changed"] for result in results),
        )
    except asyncio.CancelledError:
        logger.info("resume_pdf cancelled request=%s stage=%s", request_id, stage)
        raise
    except Exception as exc:
        code = resume_failure_code(exc)
        logger.warning(
            "resume_pdf failed request=%s stage=%s error=%s code=%s status=%s",
            request_id,
            stage,
            type(exc).__name__,
            code,
            getattr(exc, "status_code", None),
        )
        detail = (
            str(exc)
            if isinstance(exc, PdfInputError)
            else "请检查 RESUME_VISION_PROVIDER、RESUME_VISION_MODEL、凭据和可选参数。"
            if stage == "configuration"
            else VISION_FAILURE_DETAILS.get(
                code, "视觉校对失败，请检查模型图片能力、响应格式、服务额度与后端日志。"
            )
            if stage == "vision"
            else "PDF 处理失败，请检查或重新导出文件。"
        )
        yield event_line("error", stage=stage, code=code, detail=detail, request_id=request_id)
    finally:
        if vision is not None:
            await vision.close()
        logger.info(
            "resume_pdf end request=%s stage=%s duration_ms=%d",
            request_id,
            stage,
            (perf_counter() - started) * 1000,
        )
