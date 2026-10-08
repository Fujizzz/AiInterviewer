"""Responsibilities: provides PDF upload and cancellable stage stream, without writing to business
database or saving resumes.
Implementation: default traditional extraction, advanced explicitly enabled for visual review;
executed in Celery or development process; multipart bounded reading; PDF library parses only in
isolated process; visual review at most three pages concurrent, progress sent by actual completion
count.
Related Modules:
- api.urls registers /api/resume/parse/;
- resumes.js consumes version extraction NDJSON;
- Agent validates transcription.

Declaration Index:
- event_line: encodes single event into NDJSON bytes.
- review_pages: schedules visual review with limited sliding window; cleans up all in-flight tasks
  on failure or closure.
- parse_resume_pdf: validates upload, returns stage stream or request error.
- resume_events: executes rules, visual review, final state, and resource release.
- resume_failure_code: identifies failure type from fixed error codes and safe diagnostics, without
  echoing original exception.

Variable Index:
- logger: logs request ID, stage, page count, and duration, without upload file name or text.
- VISION_CONCURRENCY: maximum concurrent visual review requests per PDF, set to 3.
- VISION_FAILURE_DETAILS: fixed English prompts for known visual failure codes; unknown exceptions
  use existing generic prompt.

Constraint Notes:
traditional: only traditional extraction;
advanced: must complete rule-based extraction and visual review; failure does not fall back to
traditional success.
Stream failure signaled via error event; cannot rely on HTTP 200 to determine completion.
Django upload processor may temporarily persist file; framework deletes temporary file upon response
close; business does not retain file.
Per PDF uses bounded concurrency; failure or disconnection cancels in-flight tasks and stops
scheduling, then closes client.
ASGI entry limits total number of uploads; requests already received by vendor are not guaranteed to
stop billing.
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
    "resume_correction_range_invalid": (
        "The vision model returned an out-of-range or invalid correction; it was rejected."
    ),
    "resume_overlapping_corrections": (
        "The vision model returned overlapping corrections; they were rejected."
    ),
    "resume_empty_correction": (
        "The vision model returned a correction without supporting evidence; it was rejected."
    ),
    "resume_page_mismatch": (
        "The page number returned by the vision model "
        "does not match the source page; it was rejected."
    ),
    "resume_page_omitted": (
        "The vision model's correction would remove an entire page; it was rejected."
    ),
    "resume_image_required": "Visual review requires page images. Check the PDF rendering logs.",
    "resume_vision_empty_choices": "The vision service returned no usable result.",
    "resume_vision_incomplete_or_refused": (
        "The vision service returned an incomplete result or refused the request; it was rejected."
    ),
    "timeout": "The vision service request timed out; no incomplete review result was applied.",
    "invalid_json": (
        "The vision service could not produce valid JSON within the configured attempt limit. "
        "No incomplete result was saved. Check the backend validation logs."
    ),
}


def resume_failure_code(error):
    """Inputs arbitrary exception, outputs limited failure code; fixed ValueError code takes
    precedence; others read from safe diagnostics, no body or state modification.
    """
    if isinstance(error, APITimeoutError):
        return "timeout"
    if isinstance(error, ValueError) and str(error) in VISION_FAILURE_DETAILS:
        return str(error)
    return safe_error_details(error)["category"] or "resume_processing_failed"


def event_line(kind: str, **data) -> bytes:
    """Inputs event type and JSON fields, outputs UTF-8 bytes ending with newline; no network
    request sent.
    """
    return (json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n").encode("utf-8")


async def review_pages(agent, pages):
    """Inputs Agent and original page order, produces review results in completion order; up to
    three tasks per document in flight.

    First checks if completed tasks in same batch failed, then delivers results and supplements
    window, avoiding submission after known failure.
    all_tasks stores all tasks in batch for exception retrieval; finally cancels and waits for
    unfinished items; caller can only close shared model client afterward.
    Async generator must be wrapped with aclosing by caller to ensure cleanup even on consumption
    interruption.
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
        # Business errors are propagated verbatim via task.result; here collect cancellation and
        # batch exceptions to avoid uncollected task exceptions.
        await asyncio.gather(*all_tasks, return_exceptions=True)


async def parse_resume_pdf(request):
    """Input is authenticated same-origin multipart POST, output is NDJSON stream or fixed format
    4xx error.

    Accepts one file and optional mode (traditional default or advanced); content is re-validated by
    rule layer.
    Bounded reading avoids loading oversized uploads into business memory; upload parsing occurs in
    worker threads to prevent blocking ASGI loop.
    Production hands off one-time input and progress to Redis/Celery, stream closure notifies worker
    cancellation.
    No changes to global JSON API parser, WebSocket message limit, or existing interview behavior.
    """
    if request.method != "POST":
        response = JsonResponse({"error": "POST is the only supported method."}, status=405)
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
            {"error": "Submit one file. mode must be traditional or advanced."}, status=400
        )
    upload = files["file"]
    if not 0 < upload.size <= MAX_BYTES:
        return JsonResponse(
            {"error": ("The PDF must be non-empty and no larger than 10 MiB.")}, status=413
        )
    data = await asyncio.to_thread(upload.read, MAX_BYTES + 1)
    # When production queue is unavailable, failure is explicitly signaled by queue layer;
    # development inline is an explicit mode, never a fallback for failure.
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
    """Input is bounded PDF and mode (default traditional), output is progress/page/result or error.

    Traditional directly returns rule text and prompts without constructing model; advanced performs
    original bounded visual verification.
    Page response does not include internal revision suggestions;
    Any page failure prevents result delivery and does not trigger automatic rollback; cancellation
    propagates upward and finally closes client.
    Error events carry limited code and fixed prompt; logs record stage, request identifier,
    exception type, and numeric status, but do not echo body text.
    """
    request_id = uuid4().hex
    started = perf_counter()
    vision = None
    stage = "rules"
    logger.info("resume_pdf start request=%s bytes=%d", request_id, len(data))
    try:
        if mode not in {"traditional", "advanced"}:
            raise ValueError("invalid extraction mode")
        yield event_line(
            "progress",
            stage=stage,
            detail=("Extracting PDF text and rendering pages in the isolated environment."),
        )
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
        yield event_line(
            "progress", stage=stage, detail=f"Visual review: 0/{len(pages)} pages completed."
        )
        async with aclosing(review_pages(agent, pages)) as reviews:
            async for result in reviews:
                # Revision suggestions are for internal validation only; HTTP delivers only revision
                # results and unconfirmed doubts.
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
                    detail=f"Visual review: {len(results_by_page)}/{len(pages)} pages completed.",
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
            else (
                "Check RESUME_VISION_PROVIDER, "
                "RESUME_VISION_MODEL, credentials, and optional "
                "settings."
            )
            if stage == "configuration"
            else VISION_FAILURE_DETAILS.get(
                code,
                (
                    "Visual review failed. Check model image "
                    "support, response format, provider quota, and "
                    "backend logs."
                ),
            )
            if stage == "vision"
            else "PDF processing failed. Check the file or export it again."
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
