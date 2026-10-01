"""职责：为简历整理 Agent 实现独立的异步多模态模型适配器。
实现：读取专用配置，以 Chat Completions JSON 模式传递单页 PNG 和规则文本，严格校验结构。
关联：agents.resume_cleanup 定义端口；resume_api 管理本适配器生命周期。

目录：
- ResumeVision：OpenAI 兼容视觉客户端，显式配置，无自动重试或文字模型回退。
- ResumeVision.__init__：验证专用视觉配置，创建异步客户端。
- ResumeVision.review：传递单页图文并验证模型终态及结构。
- ResumeVision.close：异步释放客户端连接。

关键变量：
- logger：只记录页码、模型、耗时和异常类型，不记录图像、简历或密钥。

配置说明：
RESUME_VISION_PROVIDER 必须为 dashscope 或 openai，RESUME_VISION_MODEL 必填；
凭据复用所选供应商已有 API_KEY，BASE_URL 与对应供应商现有配置一致。
RESUME_VISION_TIMEOUT_SECONDS 默认 90 秒；每页一次请求、max_retries=0。
RESUME_VISION_ENABLE_THINKING 可显式填 true/false，仅 DashScope 发送此可选参数。
未设置时保持供应商行为；不修改已有面试模型、温度或超时配置。
"""

import base64
import json
import logging
import os
from time import perf_counter

from openai import AsyncOpenAI

from agents.resume_cleanup import PageReview, ResumePage

logger = logging.getLogger(__name__)


class ResumeVision:
    """功能：实现异步视觉端口；逻辑：每页单次调用；约束：错误和取消直接传播。"""

    def __init__(self):
        """读取进程环境配置，输出客户端实例；缺配置抛 ValueError，不发起网络请求。

        model/provider 保存选定视觉配置，options 仅含显式 thinking 设置；
        client 由调用者在 finally 中关闭，密钥不进入日志或响应。
        """
        self.provider = os.environ.get("RESUME_VISION_PROVIDER", "")
        self.model = os.environ.get("RESUME_VISION_MODEL", "").strip()
        if self.provider not in {"dashscope", "openai"} or not self.model:
            raise ValueError("请配置 RESUME_VISION_PROVIDER 和 RESUME_VISION_MODEL。")
        prefix = "DASHSCOPE" if self.provider == "dashscope" else "OPENAI"
        key = os.environ.get(f"{prefix}_API_KEY", "")
        if not key:
            raise ValueError("所选视觉服务缺少 API key，请检查后端环境配置。")
        timeout = float(os.environ.get("RESUME_VISION_TIMEOUT_SECONDS", "90"))
        if not 0 < timeout <= 600:
            raise ValueError("RESUME_VISION_TIMEOUT_SECONDS 应在 0 至 600 秒之间。")
        thinking = os.environ.get("RESUME_VISION_ENABLE_THINKING", "")
        if thinking not in {"", "true", "false"}:
            raise ValueError("RESUME_VISION_ENABLE_THINKING 只能填 true 或 false。")
        self.options = {}
        if thinking:
            if self.provider != "dashscope":
                raise ValueError("RESUME_VISION_ENABLE_THINKING 仅用于 DashScope。")
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
        """输入带图像页面及系统提示，输出 schema 校验结果；不尝试修复或重发。

        图片使用内嵌 PNG，不上传到文件服务；模型必须支持图片与 JSON 模式。
        拒绝、截断、空输出及非 JSON 均失败。
        日志仅含诊断元数据；供应商响应正文即使异常也不写日志。
        """
        started = perf_counter()
        logger.info("resume_vision start page=%s model=%s", page.number, self.model)
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": prompt
                        + "\nJSON schema:\n"
                        + json.dumps(PageReview.model_json_schema(), ensure_ascii=False),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {"number": page.number, "rule_text": page.text},
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
                ],
                **self.options,
            )
            if not response.choices:
                raise ValueError("resume_vision_empty_choices")
            choice = response.choices[0]
            if choice.finish_reason != "stop" or choice.message.refusal:
                raise ValueError("resume_vision_incomplete_or_refused")
            return PageReview.model_validate_json(choice.message.content or "")
        except Exception as exc:
            logger.warning("resume_vision failed page=%s error=%s", page.number, type(exc).__name__)
            raise
        finally:
            logger.info(
                "resume_vision end page=%s duration_ms=%d",
                page.number,
                (perf_counter() - started) * 1000,
            )

    async def close(self):
        """读取 client 并关闭异步 HTTP 连接；不删除文件、不修改模型配置。"""
        await self.client.close()
