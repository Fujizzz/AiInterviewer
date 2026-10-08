"""Responsibilities: Implement independent asynchronous multimodal model adapter for resume cleanup
Agent.
Implementation: Read dedicated configuration, send single-page evidence with an explicit JSON
contract, and regenerate schema-invalid responses using bounded, sanitized validation feedback.
Related Modules: agents.resume_cleanup defines port; resume_api manages lifecycle of this adapter.
Declaration Index:
- review_messages: Build page evidence, JSON rules and a task-specific correction/no-change example.
- ResumeVision: OpenAI-compatible vision client with bounded JSON validation retries.
- ResumeVision.__init__: Validate dedicated vision configuration, create async client.
- ResumeVision.review: Validate each response and retry only invalid JSON/schema output.
- ResumeVision.close: Asynchronously release client connection.
Variable Index:
- logger: Logs only page number, model, duration, and exception type, no image, resume, or key data.
- JSON_OUTPUT_RULES: Explicit field types and regeneration constraints supplement the Agent prompt.
Configuration Notes:
RESUME_VISION_PROVIDER must be dashscope or openai, RESUME_VISION_MODEL required;
Credentials reuse existing API_KEY from selected provider, BASE_URL matches provider’s existing
configuration.
RESUME_VISION_TIMEOUT_SECONDS defaults to 90 seconds per SDK request; SDK max_retries=0.
RESUME_VISION_JSON_RETRIES defaults to 2 (range 0..5), in addition to the initial page request.
Only local PageReview validation failures trigger a new request; exhausted retries propagate the
last ValidationError. Transport errors, refusals, truncation and cancellation are never retried.
RESUME_VISION_ENABLE_THINKING can be explicitly set true/false, only DashScope accepts this optional
parameter.
Unset value retains provider behavior; does not modify existing interview model, temperature, or
timeout configuration.
"""

import asyncio
import base64
import json
import logging
import os
from time import perf_counter

from openai import APITimeoutError, AsyncOpenAI
from pydantic import ValidationError

from agents.model_calls import safe_error_details, validation_issues
from agents.resume_cleanup import PageReview, ResumePage, source_lines

logger = logging.getLogger(__name__)

JSON_OUTPUT_RULES = """输出必须是符合下方 JSON schema 的单个 JSON 对象，所有必填字段均须出现。
顶层只允许 number、corrections、uncertainties；不得添加 text、summary、status 等字段。
number、start_line、end_line 必须是 JSON 整数，不能是字符串、小数或 null。
corrections 必须是数组；每项只包含 start_line、end_line、text、reason，四个字段均必填。
text 和 reason 必须是字符串；reason 简洁描述图像依据；uncertainties 必须是字符串数组，
不要返回对象、null 或单个字符串；没有修改或疑点时对应字段使用 []，不可省略。
字符串中的引号、反斜杠和换行必须按 JSON 转义；不加代码围栏、说明文字或尾随逗号。
格式示例中的空数组只适用于确实无需修改或没有疑点的页面，不能为通过校验而省略必要修改。
格式示例只说明字段结构，尖括号内是占位说明，必须替换为本页实际证据，不得照抄。
若收到 validation_feedback，依据原始图片和 rule_lines 重新生成完整 JSON，修正列出的
字段和类型错误；schema 路径中的 * 表示数组项或未知字段，$ 表示根对象。
不要返回局部修补、错误说明、反馈内容或先前无效结果；不要修改原有的校对标准。"""


def review_messages(page: ResumePage, prompt: str) -> list[dict]:
    """Functionality: Construct the immutable evidence shared by one page's request attempts.
    Inputs: The original page (number, rule text, PNG bytes) and the Agent's system prompt.
    Outputs: A fresh system/user message list containing JSON instructions and multimodal evidence.
    Logic: Enumerate original lines once and encode the same PNG for every attempt. Nonempty rule
    text gets a no-change example; empty/whitespace-only text gets an explicit transcription task
    and correction example so the absence of extracted text is not mistaken for a blank image.
    Constraints: No text normalization, external I/O or logging; examples are format guidance, not
    permission to discard visible corrections. Empty source text retains the virtual first line.
    """
    example = {"number": page.number, "corrections": [], "uncertainties": []}
    task = "本页已有规则文字，请对照图片校对。以下为无需修改且无疑点时的格式示例："
    if not page.text.strip():
        task = (
            "本页规则文本为空，这不代表图片空白。请先阅读图片：只要有可见文字，"
            "就必须用第 1 行的 correction 完整转写本页，不能返回空 corrections。"
            "只有确认图片没有文字时才返回空数组；无法辨认的文字按原校对规则标记并记录疑点。"
            "以下是有可见文字时的格式示例："
        )
        example["corrections"] = [
            {
                "start_line": 1,
                "end_line": 1,
                "text": "<按图片阅读顺序逐字转写本页可见文字，保留换行>",
                "reason": "<说明规则文字缺失，按图片补录的依据>",
            }
        ]
    return [
        {
            "role": "system",
            "content": prompt
            + "\n"
            + JSON_OUTPUT_RULES
            + "\n"
            + task
            + "\n"
            + json.dumps(example, ensure_ascii=False)
            + "\nJSON schema:\n"
            + json.dumps(PageReview.model_json_schema(), ensure_ascii=False),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "number": page.number,
                            "rule_lines": [
                                {"line": number, "text": line.rstrip("\r\n")}
                                for number, line in enumerate(source_lines(page.text), 1)
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(page.image_png).decode("ascii")
                    },
                },
            ],
        },
    ]


class ResumeVision:
    """Functionality: Implement the asynchronous vision port with bounded schema regeneration.
    Logic: Keep provider/client configuration on the instance and all attempt state local to review.
    Constraints: Concurrent pages never share feedback; there is no fallback model or parser.
    """

    def __init__(self):
        """Functionality: Validate configuration and create the dedicated async model client.
        Inputs: Provider credentials, model, timeout, thinking flag and JSON retry count from env.
        Outputs: Sets provider, model, json_retries, options and client; invalid settings raise
        ValueError before client creation.
        Logic: json_retries counts additional requests after local schema failures, while options
        contains only the explicit thinking setting and SDK transport retries remain disabled.
        Constraints: No network request is initiated; the caller must close the client in finally.
        Keys are never logged or returned in a response.
        """
        self.provider = os.environ.get("RESUME_VISION_PROVIDER", "")
        self.model = os.environ.get("RESUME_VISION_MODEL", "").strip()
        if self.provider not in {"dashscope", "openai"} or not self.model:
            raise ValueError("Set RESUME_VISION_PROVIDER and RESUME_VISION_MODEL.")
        prefix = "DASHSCOPE" if self.provider == "dashscope" else "OPENAI"
        key = os.environ.get(f"{prefix}_API_KEY", "")
        if not key:
            raise ValueError(
                "The selected vision provider has no API key. "
                "Check the backend environment configuration."
            )
        timeout = float(os.environ.get("RESUME_VISION_TIMEOUT_SECONDS", "90"))
        if not 0 < timeout <= 600:
            raise ValueError("RESUME_VISION_TIMEOUT_SECONDS must be between 0 and 600 seconds.")
        retries = os.environ.get("RESUME_VISION_JSON_RETRIES", "2")
        if retries not in {"0", "1", "2", "3", "4", "5"}:
            raise ValueError("RESUME_VISION_JSON_RETRIES must be an integer between 0 and 5.")
        self.json_retries = int(retries)
        thinking = os.environ.get("RESUME_VISION_ENABLE_THINKING", "")
        if thinking not in {"", "true", "false"}:
            raise ValueError("RESUME_VISION_ENABLE_THINKING must be true or false.")
        self.options = {}
        if thinking:
            if self.provider != "dashscope":
                raise ValueError("RESUME_VISION_ENABLE_THINKING is supported only by DashScope.")
            self.options["extra_body"] = {"enable_thinking": thinking == "true"}
        default_url = (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if self.provider == "dashscope"
            else "https://api.openai.com/v1"
        )
        self.client = AsyncOpenAI(
            api_key=key,
            base_url=os.environ.get(f"{prefix}_BASE_URL") or default_url,
            timeout=timeout,
            max_retries=0,
        )

    async def review(self, page: ResumePage, prompt: str) -> PageReview:
        """Functionality: Obtain a strictly validated PageReview, regenerating invalid JSON only.
        Inputs: Original page evidence, Agent prompt, and the instance's dedicated SDK settings.
        Outputs: The first schema-valid result, or the last validation/provider exception.
        Logic: Send fixed evidence on every attempt; after a local ValidationError, replace the
        previous feedback with bounded schema paths/codes and ask for a complete new result.
        Constraints: At most 1 + json_retries requests per page; no coercion, partial parsing,
        silent repair or schema relaxation. Transport errors, refusals and truncation propagate
        immediately; cancellation interrupts the active await. The Agent still validates patch
        semantics before applying anything. Logs omit response content and record attempt/token
        metadata so retry cost and failures remain diagnosable.
        """
        started = perf_counter()
        base_messages = review_messages(page, prompt)
        messages = base_messages
        attempts = self.json_retries + 1
        attempt = 0
        logger.info(
            "resume_vision start page=%s provider=%s model=%s max_attempts=%d",
            page.number,
            self.provider,
            self.model,
            attempts,
        )
        try:
            for attempt in range(1, attempts + 1):
                logger.info(
                    "resume_vision attempt page=%s attempt=%d max_attempts=%d",
                    page.number,
                    attempt,
                    attempts,
                )
                response = await self.client.chat.completions.create(
                    model=self.model,
                    response_format={"type": "json_object"},
                    messages=messages,
                    **self.options,
                )
                logger.info(
                    "resume_vision response page=%s attempt=%d input_tokens=%s output_tokens=%s",
                    page.number,
                    attempt,
                    getattr(getattr(response, "usage", None), "prompt_tokens", None),
                    getattr(getattr(response, "usage", None), "completion_tokens", None),
                )
                if not response.choices:
                    raise ValueError("resume_vision_empty_choices")
                choice = response.choices[0]
                if choice.finish_reason != "stop" or choice.message.refusal:
                    raise ValueError("resume_vision_incomplete_or_refused")
                try:
                    result = PageReview.model_validate_json(choice.message.content or "")
                except ValidationError as exc:
                    if attempt == attempts:
                        raise
                    issues = validation_issues(exc, PageReview)
                    logger.warning(
                        "resume_vision retry page=%s attempt=%d next_attempt=%d "
                        "max_attempts=%d schema_issues=%s",
                        page.number,
                        attempt,
                        attempt + 1,
                        attempts,
                        issues,
                    )
                    # Regenerate from original evidence; do not echo untrusted response values or
                    # accumulate failed outputs, which could consume the remaining context window.
                    messages = base_messages + [
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "validation_feedback": issues,
                                    "instruction": (
                                        "上一轮 JSON 校验失败。请按 schema 修正错误，"
                                        "重新核对同一页原始证据，返回完整合法 JSON；"
                                        "保留所有必要修改，不要为通过校验而返回空结果。"
                                    ),
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                    continue
                logger.info(
                    "resume_vision validated page=%s attempt=%d corrections=%d uncertainties=%d",
                    page.number,
                    attempt,
                    len(result.corrections),
                    len(result.uncertainties),
                )
                return result
        except asyncio.CancelledError:
            logger.info("resume_vision cancelled page=%s attempt=%d", page.number, attempt)
            raise
        except Exception as exc:
            details = safe_error_details(exc)
            logger.warning(
                "resume_vision failed page=%s attempt=%d max_attempts=%d "
                "error=%s code=%s status=%s schema_issues=%s",
                page.number,
                attempt,
                attempts,
                type(exc).__name__,
                "timeout" if isinstance(exc, APITimeoutError) else details["category"],
                details["status_code"],
                validation_issues(exc, PageReview) if isinstance(exc, ValidationError) else [],
            )
            raise
        finally:
            logger.info(
                "resume_vision end page=%s duration_ms=%d",
                page.number,
                (perf_counter() - started) * 1000,
            )

    async def close(self):
        """Functionality: Close the async HTTP client.
        Inputs: The instance's client; no external arguments.
        Outputs: None; propagates SDK close errors.
        Logic: Await SDK connection cleanup after all page tasks have ended.
        Constraints: Does not delete files or modify model configuration.
        """
        await self.client.close()
