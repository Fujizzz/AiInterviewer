"""Responsibilities: Reuse project LLM configuration for single-shot safety and benchmark calls.
Implementation: Preserve provider, model, options, timeout and response checks; share transport
between structured safety assessments and explicit plain-text public benchmark requests.
Prepare verified trust/resources once; reuse one loop-bound client with explicit owner cleanup.
Set a finite safety-only idle lifetime explicitly while retaining the SDK connection-count limits.
Related Modules: behavior_semantic requests assessments; security_evaluation.public.collect
requests native benchmark responses; BehaviorEngine owns the safety deadline.

Declaration Index:
- ProjectModelTransport: 与审查语义解耦的单次模型传输。
- ProjectModelTransport.__init__: 验证配置并初始化脱敏调用统计。
- _verified_tls_context: Load HTTPX-default certificate trust once with verification enabled.
- ProjectModelTransport.generate: 单次调用，不重试、不修复、不执行工具。
- ProjectModelTransport.generate_text: Send original benchmark messages without JSON instructions.
- ProjectModelTransport._request: Perform one SDK call and record status without hiding failures.
- ProjectModelTransport._client_for_call: Create/reuse the owned client in exactly one event loop.
- ProjectModelTransport.aclose: Explicitly release the client after all owner tasks stop.
- ProjectModelTransport._await_response: Measure SDK await duration on completion or cancellation.
- ProjectModelTransport._http_request: Bind finite per-call HTTP trace events without request data.
- ProjectModelTransport._http_request.trace: Record allowed event names and monotonic elapsed times.
- ProjectModelTransport._http_response: Record response-header timing without reading its body.
- ProjectModelTransport.metadata: 返回可复现实验配置和调用统计，不返回凭据或正文。
- create_transport: 从统一根目录配置和进程环境创建供行为审查器使用的模型传输。

Variable Index:
- logger: 仅记录模型、请求序号、异常类型和 HTTP 状态，不记录正文或服务商错误文本。
- TRACE_EVENTS: Finite TCP/TLS and HTTP phase names; trace info values are never retained.
- DEFAULT_KEEPALIVE_SECONDS: Default finite 60-second safety-pool idle lifetime.

Constraints:
AI_SECURITY_KEEPALIVE_SECONDS is an optional positive finite idle lifetime, not a model deadline.
复用 LLM_PROVIDER、对应 API_KEY/MODEL/BASE_URL、OPENAI_TEMPERATURE；不修改环境文件。
DashScope 沿用业务 enable_thinking=False；OpenAI 沿用 Responses 且 store=False。
sdk_timeout_seconds 为已有 Agent 请求超时；外层安全策略时限继续生效，不自动增大。
_api_key 仅用于 SDK；_calls 记录无正文的响应模型、token 用量及调用状态，不含远端请求 ID。
每个传输实例复用自己的连接池；首次调用绑定事件循环，禁止跨循环使用或关闭后重建。
连接空闲期限显式配置，默认60秒；对端关闭仍由原HTTP栈处理，不增加重试或保活请求。
CLI、评测和连接网关负责停止在途任务后显式aclose；关闭成本单独记录，不混入单次推理。
证书环境与凭据一样在实例初始化时读取；变更证书需重建传输。初始化耗时单独记录，
不计入请求延迟，不能视为消除启动成本；成本统计可能缺失超时远端用量。
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import ssl
from contextvars import ContextVar
from copy import deepcopy
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import certifi
import httpx
from dotenv import dotenv_values
from openai import DEFAULT_CONNECTION_LIMITS, AsyncOpenAI, DefaultAsyncHttpxClient

from agents.config import load_agent_settings

logger = logging.getLogger(__name__)
DEFAULT_KEEPALIVE_SECONDS = 60.0
TRACE_EVENTS = frozenset(
    f"{phase}.{state}"
    for phase in (
        "connection.connect_tcp",
        "connection.start_tls",
        "http11.send_request_headers",
        "http11.send_request_body",
        "http11.receive_response_headers",
        "http11.receive_response_body",
        "http2.send_request_headers",
        "http2.send_request_body",
        "http2.receive_response_headers",
        "http2.receive_response_body",
    )
    for state in ("started", "complete", "failed")
)


def _verified_tls_context() -> ssl.SSLContext:
    """Functionality: Prepare reusable certificate trust with HTTPX 0.28 default semantics.
    Inputs: SSL_CERT_FILE/SSL_CERT_DIR process environment, otherwise certifi's CA bundle.
    Outputs: Hostname-checking CERT_REQUIRED SSLContext. Logic: Preserve file-before-directory
    precedence using Python's public SSL API. Constraints: Trust errors propagate;
    directory certificates may load lazily and fail at handshake. No verification bypass, global
    cache, retries or alternate trust-store fallback occurs.
    Only the transport uses the context; it never mutates it after initialization.
    """
    if os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
    if os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context(capath=os.environ["SSL_CERT_DIR"])
    return ssl.create_default_context(cafile=certifi.where())


class ProjectModelTransport:
    """功能：对接真实模型；逻辑：配置复用、单次生成；约束：不继承业务重试、不操作业务资源。"""

    def __init__(self, configuration: dict, sdk_timeout_seconds: float):
        """功能：初始化；输入：显式配置与 SDK 超时；输出：传输实例；缺配置报错且不发请求。

        provider/model/options 与主业务配置相同；空温度不发送参数。统计仅记录本实例的调用。
        预加载SDK资源模块、验证TLS信任并分别记录一次性成本；不创建客户端或网络连接。
        模块与证书初始化在实例构建时完成；不在限时审查中重载证书或首次加载资源模块。
        AI_SECURITY_KEEPALIVE_SECONDS只控制空闲连接保留；未配置为60秒，空值/非有限/非正值
        明确报错。原SDK最大连接数、模型参数和外层时限不变，不发送额外保活或重试请求。
        """
        self.provider = configuration.get("LLM_PROVIDER", "").strip().lower()
        if self.provider not in {"dashscope", "openai"}:
            raise ValueError("LLM_PROVIDER must be dashscope or openai")
        prefix = "DASHSCOPE" if self.provider == "dashscope" else "OPENAI"
        self._api_key = (configuration.get(f"{prefix}_API_KEY") or "").strip()
        self.model = (configuration.get(f"{prefix}_MODEL") or "").strip()
        if not self._api_key or not self.model:
            raise ValueError("configured provider requires API_KEY and MODEL")
        default_url = (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if self.provider == "dashscope"
            else "https://api.openai.com/v1"
        )
        self._base_url = (configuration.get(f"{prefix}_BASE_URL") or default_url).strip()
        temperature = (configuration.get("OPENAI_TEMPERATURE", "0") or "").strip()
        self.options = {"temperature": float(temperature)} if temperature else {}
        if self.options and not math.isfinite(self.options["temperature"]):
            raise ValueError("temperature must be finite")
        if not math.isfinite(sdk_timeout_seconds) or sdk_timeout_seconds <= 0:
            raise ValueError("SDK timeout must be positive and finite")
        self.sdk_timeout_seconds = sdk_timeout_seconds
        try:
            self.keepalive_expiry_seconds = float(
                configuration.get("AI_SECURITY_KEEPALIVE_SECONDS", DEFAULT_KEEPALIVE_SECONDS)
            )
        except (TypeError, ValueError):
            raise ValueError("AI_SECURITY_KEEPALIVE_SECONDS must be positive and finite") from None
        if not math.isfinite(self.keepalive_expiry_seconds) or self.keepalive_expiry_seconds <= 0:
            raise ValueError("AI_SECURITY_KEEPALIVE_SECONDS must be positive and finite")
        self._calls = []
        # Task-local timing context isolates concurrent calls sharing this owned client. It holds
        # only a statistics record and monotonic origin; no messages, secrets or HTTP headers.
        self._call_context = ContextVar("security_call_timing", default=None)
        initialized = perf_counter()
        import_module("openai.resources")
        self._sdk_initialization_ms = (perf_counter() - initialized) * 1000
        initialized = perf_counter()
        self._ssl_context = _verified_tls_context()
        self._tls_initialization_ms = (perf_counter() - initialized) * 1000
        self._client = None
        self._loop = None
        self._closed = False
        self._close_ms = None
        self._close_status = None

    async def generate(self, instructions: str, payload: dict, schema: dict) -> str:
        """功能：生成检测 JSON；输入：固定指令、不可信数据与 Schema；输出：原始 JSON 字符串。

        模型拒绝、截断或缺响应均抛错，格式和证据由行为审查器及引擎校验。
        取消向上传播；SDK取消当前HTTP请求，实例拥有者停止所有任务后显式关闭连接池。
        调用统计区分收到完整响应、异常和取消；received 不代表已通过语义契约验证。
        """
        return await self._request(instructions, json.dumps(payload, ensure_ascii=False), schema)

    async def generate_text(self, instructions: str, user_text: str) -> str:
        """Functionality: Generate a native public benchmark response using existing configuration.
        Inputs: Original trusted system prompt and original untrusted user text. Outputs: Raw text.
        Logic: Use the shared single-shot transport without a JSON schema or formatting instruction.
        Constraints: Explicit benchmark API, never an assessment fallback. No tools, retries,
        repair, parameter changes or writes; failures and cancellation propagate unchanged.
        """
        return await self._request(instructions, user_text, None)

    async def _request(self, instructions: str, user_text: str, schema: dict | None) -> str:
        """Functionality: Perform one asynchronous provider request and record diagnostic metadata.
        Inputs: System text, user text, and optional explicit schema. Outputs: Complete raw text.
        Logic: Schema presence selects structured formatting; both modes share SDK options,
        response validation, token accounting and cancellation. Timings separate client setup,
        SDK await and local work. The owner explicitly closes the pool after its tasks stop.
        Constraints: Missing/truncated/refused responses raise; statistics exclude contents,
        credentials and remote error bodies. Received means transport success, not safe behavior.
        """
        started = perf_counter()
        call = {"sequence": len(self._calls), "status": "started"}
        self._calls.append(call)
        logger.info("Security model start sequence=%s model=%s", call["sequence"], self.model)
        try:
            call["client_reused"] = self._client is not None
            client = self._client_for_call()
            call["client_setup_ms"] = (perf_counter() - started) * 1000
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_text},
            ]
            if self.provider == "dashscope":
                formatting = {}
                if schema is not None:
                    messages[0]["content"] += "\nJSON schema:\n" + json.dumps(schema)
                    formatting["response_format"] = {"type": "json_object"}
                response = await self._await_response(
                    client.chat.completions.create,
                    call,
                    model=self.model,
                    messages=messages,
                    **formatting,
                    extra_body={"enable_thinking": False},
                    **self.options,
                )
                usage = response.usage
                call["usage"] = (
                    None
                    if usage is None
                    else {
                        "input_tokens": usage.prompt_tokens,
                        "output_tokens": usage.completion_tokens,
                        "total_tokens": usage.total_tokens,
                    }
                )
                if not response.choices:
                    raise ValueError("security_model_empty_choices")
                choice = response.choices[0]
                if choice.finish_reason != "stop" or choice.message.refusal:
                    raise ValueError("security_model_incomplete_or_refused")
                raw = choice.message.content
            else:
                formatting = {}
                if schema is not None:
                    formatting["text"] = {
                        "format": {
                            "type": "json_schema",
                            "name": "security_assessment",
                            "schema": schema,
                            "strict": True,
                        }
                    }
                response = await self._await_response(
                    client.responses.create,
                    call,
                    model=self.model,
                    input=messages,
                    **formatting,
                    store=False,
                    **self.options,
                )
                usage = response.usage
                call["usage"] = (
                    None
                    if usage is None
                    else {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                    }
                )
                if response.status != "completed" or any(
                    part.type == "refusal"
                    for item in response.output
                    if item.type == "message"
                    for part in item.content
                ):
                    raise ValueError("security_model_incomplete_or_refused")
                raw = response.output_text
            if not isinstance(raw, str) or not raw:
                raise ValueError("security_model_empty_content")
            call.update(status="received", response_model=response.model)
            logger.info("Security model received sequence=%s", call["sequence"])
            return raw
        except asyncio.CancelledError:
            call["status"] = "cancelled"
            logger.info("Security model cancelled sequence=%s", call["sequence"])
            raise
        except Exception as exc:
            call.update(status="failed", exception_type=type(exc).__name__)
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int):
                call["http_status"] = status_code
            logger.warning(
                "Security model failed sequence=%s exception=%s http_status=%s",
                call["sequence"],
                type(exc).__name__,
                call.get("http_status"),
            )
            raise
        finally:
            call["total_ms"] = (perf_counter() - started) * 1000
            call["local_and_cleanup_ms"] = max(
                0, call["total_ms"] - call.get("client_setup_ms", 0) - call.get("sdk_await_ms", 0)
            )

    async def _await_response(self, operation, call: dict, **arguments):
        """Functionality: Measure one SDK await without changing response or failure semantics.
        Inputs: Async SDK operation, mutable statistics record and original request arguments.
        Outputs: Original response. Logic: Record monotonic elapsed time in finally on every exit.
        Constraints: No retry, logging of arguments, or deadline change. SDK await includes network,
        remote queue/inference and SDK response handling. HTTP hooks add finite phase timestamps,
        but cannot separate remote queue from inference or transmission time before headers.
        """
        started = perf_counter()
        token = self._call_context.set((call, started))
        try:
            return await operation(**arguments)
        finally:
            call["sdk_await_ms"] = (perf_counter() - started) * 1000
            self._call_context.reset(token)

    async def _http_request(self, request):
        """Functionality: Attach content-free HTTP observability. Inputs: Owned SDK HTTP request
        and task-local call statistics/origin. Outputs: Installed async trace callback. Logic: Count
        HTTP sends, attach a bounded recorder of finite event names/times. Constraints: Request
        body, URL, headers and trace info are never read or saved. Unknown events are counted only;
        instrumentation adds no model requests, retries, redirects, timeout or trust changes.
        """
        context = self._call_context.get()
        if context is None:
            raise RuntimeError("security HTTP timing context missing")
        call, started = context
        call["http_requests"] = call.get("http_requests", 0) + 1
        events = call.setdefault("http_trace", [])

        async def trace(event, info):
            """Functionality: Record finite timing evidence. Inputs: HTTPCore event/info and
            enclosing call/origin. Outputs: Appended event/time or an explicit omitted-event count.
            Logic: Retain at most 64 allowlisted events; disregard every info value. Constraints:
            No raw exception, server name, key, token, headers or body is retained or logged;
            missing/changed event vocabularies limit diagnosis, not behavior authorization.
            """
            if event not in TRACE_EVENTS:
                call["http_unrecorded_events"] = call.get("http_unrecorded_events", 0) + 1
            elif len(events) >= 64:
                call["http_trace_overflow"] = call.get("http_trace_overflow", 0) + 1
            else:
                events.append({"event": event, "ms": (perf_counter() - started) * 1000})

        request.extensions["trace"] = trace

    async def _http_response(self, response):
        """Functionality: Record time to received HTTP headers. Inputs: Owned response event and
        task-local statistics/origin. Outputs: Header elapsed time. Logic: Read timing context only.
        Constraints: Does not inspect headers/status/body, consume streams, relax certificate
        checks or authorize output. Absence on timeout is retained as missing timing evidence.
        """
        context = self._call_context.get()
        if context is None:
            raise RuntimeError("security HTTP timing context missing")
        call, started = context
        call["http_response_headers_ms"] = (perf_counter() - started) * 1000

    def _client_for_call(self):
        """Functionality: Reuse one client without global or cross-loop state. Inputs: Transport
        configuration and current event loop. Outputs: Owned SDK client. Logic: Bind the first
        call's loop, create once with original model options and SDK connection-count limits;
        use the validated finite idle expiry and reject later foreign loops or closed state.
        Constraints: No implicit retry, failed-call client replacement or recreation after close.
        Connection reuse is HTTP pooling,
        not a replacement model. Client creation is synchronous and does not issue a model call.
        A longer idle lifetime permits reuse; it cannot prevent peer EOF or TCP window restart.
        """
        if self._closed:
            raise RuntimeError("security transport already closed")
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("security transport event loop changed")
        if self._client is None:
            self._loop = loop
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self.sdk_timeout_seconds,
                max_retries=0,
                http_client=DefaultAsyncHttpxClient(
                    verify=self._ssl_context,
                    limits=httpx.Limits(
                        max_connections=DEFAULT_CONNECTION_LIMITS.max_connections,
                        max_keepalive_connections=DEFAULT_CONNECTION_LIMITS.max_keepalive_connections,
                        keepalive_expiry=self.keepalive_expiry_seconds,
                    ),
                    event_hooks={
                        "request": [self._http_request],
                        "response": [self._http_response],
                    },
                ),
            )
            logger.info(
                "Security pool initialized keepalive_s=%s max_connections=%s max_keepalive=%s",
                self.keepalive_expiry_seconds,
                DEFAULT_CONNECTION_LIMITS.max_connections,
                DEFAULT_CONNECTION_LIMITS.max_keepalive_connections,
            )
        return self._client

    async def aclose(self):
        """Functionality: Release this transport's owned pool. Inputs: Client/loop/lifecycle state.
        Outputs: None. Logic: Close once in the same loop after the owner awaits all pending calls;
        reject future requests, record shutdown time/status. Constraints: Cancellation/exceptions
        propagate with finite diagnostics; no automatic recreation or cleanup retry occurs.
        """
        if self._closed:
            return
        if self._client is not None and asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("security transport close event loop changed")
        self._closed = True
        started = perf_counter()
        try:
            if self._client is not None:
                await self._client.close()
            self._close_status = "closed"
        except BaseException as exc:
            self._close_status = "failed"
            logger.warning("Security pool close failed exception=%s", type(exc).__name__)
            raise
        finally:
            self._close_ms = (perf_counter() - started) * 1000
            logger.info("Security pool close status=%s ms=%.1f", self._close_status, self._close_ms)

    def metadata(self) -> dict:
        """功能：导出实验记录；输入：实例配置和累计状态；输出：独立副本；不含密钥、URL 或正文。"""
        return {
            "provider": self.provider,
            "model": self.model,
            "options": dict(self.options),
            "enable_thinking": False if self.provider == "dashscope" else None,
            "sdk_timeout_seconds": self.sdk_timeout_seconds,
            "sdk_max_retries": 0,
            "sdk_version": version("openai"),
            "httpx_version": version("httpx"),
            "httpcore_version": version("httpcore"),
            "tls_policy": "verified-context-per-transport-httpx-default-trust-v1",
            "tls_initialization_ms": self._tls_initialization_ms,
            "sdk_initialization_ms": self._sdk_initialization_ms,
            "connection_strategy": "owned-client-same-event-loop-explicit-keepalive-v2",
            "http_connection_limits": {
                "max_connections": DEFAULT_CONNECTION_LIMITS.max_connections,
                "max_keepalive_connections": DEFAULT_CONNECTION_LIMITS.max_keepalive_connections,
                "keepalive_expiry_seconds": self.keepalive_expiry_seconds,
            },
            "pool_close_ms": self._close_ms,
            "pool_close_status": self._close_status,
            "endpoint_sha256": hashlib.sha256(self._base_url.encode()).hexdigest(),
            "calls": deepcopy(self._calls),
        }


def create_transport() -> ProjectModelTransport:
    """功能：创建端口；输入：根目录 .env、进程环境及已有 Agent 超时。
    输出：不携带提示词的模型传输。

    进程环境优先，与后端一致；读取文件但不修改全局环境，不加载 Django 或业务会话。
    """
    configuration = dict(dotenv_values(Path(__file__).resolve().parents[1] / ".env"))
    configuration.update(os.environ)
    return ProjectModelTransport(
        configuration, load_agent_settings().timeouts.llm_generation_seconds
    )
