"""职责：提供本地 PDF 上传及可取消的阶段流，不写业务数据库或保存简历。
实现：multipart 有界读取；规则工作在线程执行，视觉最多三页并发并按实际完成数发进度。
关联：api.urls 注册 /api/resume/parse/；resume-pdf.js 消费 NDJSON；Agent 校验转写。

目录：
- event_line：将单个事件编码为 NDJSON 字节。
- review_pages：按有限滑动窗口调度校对，失败或关闭时清理所有在途任务。
- parse_resume_pdf：验证上传，返回阶段流或请求错误。
- resume_events：执行规则后视觉校对、终态和资源释放。

关键变量：
- logger：记录请求标识、阶段、页数与耗时，不记录上传文件名或文本。
- VISION_CONCURRENCY：单份 PDF 最多同时执行的视觉校对请求数，设为 3。

约束说明：
每次上传必须依次完成规则提取和视觉校对，不能选择仅规则成功。
流开始后失败通过 error 事件表示，不能依靠 HTTP 200 判定完成。
Django 上传处理器可能临时落盘，响应关闭时框架删除临时文件；业务不保留文件。
单份 PDF 使用有界并发；失败或断连取消在途任务并停止调度，随后关闭客户端。
多份上传各自拥有窗口；已被供应商接收的请求不保证停止计费。
"""

import asyncio
import json
import logging
from contextlib import aclosing
from time import perf_counter
from uuid import uuid4

from django.http import JsonResponse, StreamingHttpResponse

from agents.resume_cleanup import ResumeCleanupAgent

from .resume_pdf import MAX_BYTES, PdfInputError, extract_pdf, render_pages
from .resume_vision import ResumeVision

logger = logging.getLogger(__name__)
VISION_CONCURRENCY = 3


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
    """输入本机同源 multipart POST，输出 NDJSON 流或固定格式 4xx 错误。

    仅接受一个 file，不接受模式选择；内容由规则层再校验。
    有界读取避免把超限上传载入业务内存；上传解析在工作线程，避免阻塞 ASGI 循环。
    未修改全局 JSON API 解析器、WebSocket 消息上限和已有面试行为。
    """
    if request.method != "POST":
        response = JsonResponse({"error": "仅支持 POST。"}, status=405)
        response["Allow"] = "POST"
        return response
    files = await asyncio.to_thread(getattr, request, "FILES")
    if request.POST or set(files) != {"file"} or len(files.getlist("file")) != 1:
        return JsonResponse(
            {"error": "请仅提交一个 file；解析将自动执行提取和视觉校对。"}, status=400
        )
    upload = files["file"]
    if not 0 < upload.size <= MAX_BYTES:
        return JsonResponse({"error": "PDF 必须非空且不超过 10 MiB。"}, status=413)
    data = await asyncio.to_thread(upload.read, MAX_BYTES + 1)
    response = StreamingHttpResponse(
        resume_events(data), content_type="application/x-ndjson; charset=utf-8"
    )
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response


async def resume_events(data: bytes):
    """输入有界 PDF，产生 progress/page/result 或 error。

    传统提取后进行有界并发视觉校对，按完成数显示进度，最终按原页序合并。
    页面响应不包含内部修改建议；
    任一页失败不发送 result、不自动回退；取消向上传播且 finally 关闭客户端。
    """
    request_id = uuid4().hex
    started = perf_counter()
    vision = None
    stage = "rules"
    logger.info("resume_pdf start request=%s bytes=%d", request_id, len(data))
    try:
        yield event_line("progress", stage=stage, detail="正在本地提取 PDF 文本与布局")
        pages = await asyncio.to_thread(extract_pdf, data)
        stage = "configuration"
        vision = ResumeVision()
        agent = ResumeCleanupAgent(vision)
        stage = "rendering"
        yield event_line("progress", stage=stage, detail="正在渲染简历页面")
        pages = await asyncio.to_thread(render_pages, data, pages)
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
            text=text,
            pages=results,
            changed_pages=sum(result["changed"] for result in results),
        )
    except asyncio.CancelledError:
        logger.info("resume_pdf cancelled request=%s stage=%s", request_id, stage)
        raise
    except Exception as exc:
        logger.warning(
            "resume_pdf failed request=%s stage=%s error=%s", request_id, stage, type(exc).__name__
        )
        detail = (
            str(exc)
            if isinstance(exc, PdfInputError)
            else "请检查 RESUME_VISION_PROVIDER、RESUME_VISION_MODEL、凭据和可选参数。"
            if stage == "configuration"
            else "视觉校对失败，请检查模型图片能力、响应格式、服务额度与后端日志。"
            if stage == "vision"
            else "PDF 处理失败，请检查或重新导出文件。"
        )
        yield event_line("error", stage=stage, detail=detail, request_id=request_id)
    finally:
        if vision is not None:
            await vision.close()
        logger.info(
            "resume_pdf end request=%s stage=%s duration_ms=%d",
            request_id,
            stage,
            (perf_counter() - started) * 1000,
        )
