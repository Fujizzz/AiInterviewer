"""后端模型装配：复用 MVP 调用实现，只从后端环境取得配置。

目录：
- BackendLLM：
  复用 OpenAI/Qwen 的结构化输出逻辑，记录不含输入与密钥的调用信息。
- BackendLLM.__init__：
  使用已装载的后端环境建立同步模型客户端，不发送推理请求。
- BackendLLM.__call__：
  以计数保护客户端生命周期，并委派 MVP 执行一次结构化模型调用。
- BackendLLM.close：
  标记不再接受新调用，并在无在途调用时释放同步客户端。

关键变量：
- logger：
  当前模块的控制台日志入口；上下文标识及异常处理方式见相应函数。

关键状态说明：
BackendLLM._lock 保护 _active 与 _closing；_active 是在途调用数。
_closing 阻止新调用，由最后一个在途调用释放 client。
provider/model/options 控制原 MVP 推理参数；interview_id 只用于日志关联。
capacity_lease 由 ASGI 准入传入；同步调用借用它，确保断线后实际调用未返回时不释放名额。
"""

import logging
import os
import threading
from time import perf_counter

from openai import OpenAI

from agents.config import load_agent_settings
from app.providers.llm import LLMError, OpenAILLM

logger = logging.getLogger(__name__)


class BackendLLM(OpenAILLM):
    """复用 OpenAI/Qwen 的结构化输出逻辑，记录不含输入与密钥的调用信息。"""

    def __init__(self, *, interview_id=None):
        """使用已装载的后端环境建立同步模型客户端，不发送推理请求。

        输入：可选 interview_id，仅用于连接模型日志与会话日志，不参与提示词或评分。
        逻辑：验证供应商、对应密钥与模型名→读取推理选项→建立 SDK→初始化并发计数。
        依赖：跳过父类构造器以避免读取根目录 .env；仍复用其 __call__ 和千问结构化实现。
        参数：复用 Agent 的请求超时预算；禁用 SDK 自动重试，结构化修复由对应调用层负责。
        异常：缺少配置抛 LLMError；温度转换或 SDK 参数错误直接传播，供协议层统一处理。
        """
        self.interview_id = interview_id
        self.provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        if self.provider not in {"openai", "dashscope"}:
            raise LLMError("Set LLM_PROVIDER to openai or dashscope in backend/.env.")
        prefix = "DASHSCOPE" if self.provider == "dashscope" else "OPENAI"
        key = os.getenv(f"{prefix}_API_KEY", "").strip()
        self.model = os.getenv(f"{prefix}_MODEL", "").strip()
        if not key or not self.model:
            raise LLMError(f"Set {prefix}_API_KEY and {prefix}_MODEL in backend/.env.")
        temperature = os.getenv("OPENAI_TEMPERATURE", "0").strip()
        self.options = {"temperature": float(temperature)} if temperature else {}
        client_options = {}
        if self.provider == "dashscope":
            client_options["base_url"] = os.getenv(
                "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).strip()
        self.request_timeout = load_agent_settings().timeouts.llm_generation_seconds
        self.client = OpenAI(
            api_key=key, timeout=self.request_timeout, max_retries=0, **client_options
        )
        self._lock = threading.Lock()
        self._active = 0
        self._closing = False
        self.capacity_lease = None

    def __call__(self, prompt, data, schema):
        """以计数保护客户端生命周期，并委派 MVP 执行一次结构化模型调用。

        输入：prompt 为任务指令，data 为候选人数据，schema 为期望的 Pydantic 输出模型。
        返回：父类验证后的 schema 实例；原始异常记录类型与状态码后重新抛出。
        并发不变量：_active 统计已进入但未退出的调用；_closing 后拒绝新的调用入口。
        锁仅保护计数和关闭状态，不覆盖网络等待，避免取消线程被整个请求阻塞。
        日志记录面试 ID、模型、schema、耗时、文本长度和空格分词数，不记录正文或密钥。
        repair_errors 仅接受已知错误码；此处不重新校验或改变 Agent 的生成规则。
        日志准备、父类调用或结果处理异常均经 finally 归还在途计数和借用的服务名额。
        """
        with self._lock:
            if self._closing:
                raise LLMError("Interview connection has closed.")
            lease = self.capacity_lease.retain() if self.capacity_lease is not None else None
            self._active += 1
        started = perf_counter()
        try:
            repair_codes = []
            for code in data.get("repair_errors") or ():
                if code in {
                    "EMPTY_TEXT",
                    "TOO_SHORT",
                    "TOO_LONG",
                    "MULTIPLE_PRIMARY_QUESTIONS",
                    "RUBRIC_OR_EXPECTED_ANSWER_LEAK",
                    "INVALID_DIFFICULTY",
                }:
                    repair_codes.append(code)
                elif isinstance(code, str) and code.startswith("GENERATION_ERROR:"):
                    repair_codes.append("GENERATION_ERROR")
            logger.info(
                "Agent model call interview=%s provider=%s schema=%s model=%s repair_codes=%s",
                self.interview_id,
                self.provider,
                schema.__name__,
                self.model,
                repair_codes,
            )
            result = super().__call__(prompt, data, schema)
            logger.info(
                "Agent model completed interview=%s schema=%s duration_ms=%d chars=%d words=%d",
                self.interview_id,
                schema.__name__,
                (perf_counter() - started) * 1000,
                len(getattr(result, "text", "")),
                len(getattr(result, "text", "").split()),
            )
            return result
        except Exception as exc:
            cause = exc.__cause__ or exc
            logger.error(
                "Agent model failed interview=%s schema=%s exception=%s status=%s duration_ms=%d; "
                "check backend/.env, network, quota and structured-output support",
                self.interview_id,
                schema.__name__,
                type(cause).__name__,
                getattr(cause, "status_code", None),
                (perf_counter() - started) * 1000,
            )
            raise
        finally:
            # 即使父类失败也归还计数；最后一个在途调用负责完成先前提出的关闭请求。
            try:
                with self._lock:
                    self._active -= 1
                    if self._closing and not self._active:
                        self.client.close()
            finally:
                if lease is not None:
                    lease.release()

    def close(self):
        """标记不再接受新调用，并在无在途调用时释放同步客户端。

        方法：在同一把锁内检查幂等标志与活动计数，防止关闭和新调用入口交错。
        有在途调用时只置 _closing，由最后一个调用的 finally 释放客户端；不会等待其返回。
        返回 None；不保证已发出的远端请求取消，SDK 关闭异常不被静默吞掉。
        """
        with self._lock:
            if self._closing:
                return
            self._closing = True
            if not self._active:
                self.client.close()
