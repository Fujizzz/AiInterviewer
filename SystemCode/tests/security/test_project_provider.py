"""Responsibilities: 验证模型适配器的请求、单次调用、资源释放及脱敏边界，不访问真实供应商。
Implementation: 用异步 SDK 替身模拟正常返回、截断、拒绝、错误及取消，检查私有审查响应。
Related Modules: project_provider 配置读取通过临时模拟隔离；behavior_semantic 验证违规引用。

Declaration Index:
- provider_config: 提供虚构供应商配置。
- provider_policy: 将行为许可用于供应商测试，保持既有 0.1 秒取消测试时限。
- sdk_stub: 创建可观测的异步 SDK/HTTP客户端替身，无真实网络资源。
- test_provider_request: 检查两供应商的参数、数据隔离、用量和客户端释放。
- test_provider_failure: 检查拒绝、截断、空响应、非法 JSON 与错误不重试。
- test_provider_cancellation: 检查超时取消进入 SDK 并释放客户端。
- test_provider_cancellation.wait_response: 接受 SDK 参数并等待取消。
- test_factory_precedence: 确认只读取统一根目录配置，进程环境覆盖文件且不改变环境。
- test_missing_credentials: 缺少凭据时立即失败。
- test_keepalive_configuration: Reject invalid idle lifetimes before any SDK/client initialization.
- test_keepalive_override: Verify explicit idle expiry reaches the real HTTP pool with SDK limits.

Variable Index:
None
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_security import BehaviorEngine
from ai_security import project_provider as module
from ai_security.behavior_semantic import ProjectBehaviorReviewer


@pytest.fixture
def provider_config():
    """功能：提供隔离配置；输入：无；输出：虚构密钥和模型；约束：不可用于真实网络请求。"""
    return {
        "LLM_PROVIDER": "dashscope",
        "DASHSCOPE_API_KEY": "PRIVATE-TEST-KEY",
        "DASHSCOPE_MODEL": "test-model",
        "OPENAI_TEMPERATURE": "0",
        "OPENAI_API_KEY": "PRIVATE-TEST-KEY",
        "OPENAI_MODEL": "test-model",
    }


@pytest.fixture
def provider_policy(policy):
    """输入行为测试策略；输出独立副本，保留供应商测试原有 0.1 秒超时，不改变生产策略。"""
    return policy.model_copy(update={"semantic_timeout_seconds": 0.1})


def sdk_stub(monkeypatch, raw):
    """功能：模拟 SDK；输入：monkeypatch 和响应文本；输出：客户端/构造器；不发送网络请求。"""
    client = MagicMock()
    client.close = AsyncMock()
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=raw, refusal=None)
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            input_tokens=10,
            output_tokens=5,
        ),
        status="completed",
        output=[],
        output_text=raw,
        model="resolved-test-model",
    )
    client.chat.completions.create = AsyncMock(return_value=response)
    client.responses.create = AsyncMock(return_value=response)
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(module, "AsyncOpenAI", constructor)
    monkeypatch.setattr(module, "DefaultAsyncHttpxClient", MagicMock())
    return client, constructor


@pytest.mark.parametrize("provider", ["dashscope", "openai"])
async def test_provider_request(monkeypatch, provider_config, behavior_request, provider):
    """功能：检查供应商请求；输入：模拟 compliant 响应；输出：单次生成、独立指令和脱敏配置/用量。"""
    provider_config["LLM_PROVIDER"] = provider
    client, constructor = sdk_stub(
        monkeypatch,
        json.dumps(
            {
                "basis": "The output satisfies the selected requirements.",
                "verdict": "compliant",
                "checked": list(
                    range(
                        len(
                            next(
                                p.requirement_ids
                                for p in behavior_request.boundary.permits
                                if p.operation == behavior_request.proposal.operation
                            )
                        )
                    )
                ),
                "witness": None,
            }
        ),
    )
    reviewer = ProjectBehaviorReviewer(module.ProjectModelTransport(provider_config, 30))
    result = await reviewer.assess(behavior_request)
    await reviewer.aclose()
    assert result.verdict == "compliant"
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert constructor.call_args.kwargs["timeout"] == 30
    limits = module.DefaultAsyncHttpxClient.call_args.kwargs["limits"]
    assert limits.keepalive_expiry == 60
    assert limits.max_connections == module.DEFAULT_CONNECTION_LIMITS.max_connections
    assert (
        limits.max_keepalive_connections
        == module.DEFAULT_CONNECTION_LIMITS.max_keepalive_connections
    )
    method = client.chat.completions.create if provider == "dashscope" else client.responses.create
    method.assert_awaited_once()
    kwargs = method.call_args.kwargs
    messages = kwargs["messages"] if provider == "dashscope" else kwargs["input"]
    assert behavior_request.evidence[0].text not in messages[0]["content"]
    assert "actor_id" not in messages[1]["content"]
    assert kwargs["temperature"] == 0 and "tools" not in kwargs
    if provider == "dashscope":
        assert kwargs["extra_body"] == {"enable_thinking": False}
    else:
        assert kwargs["store"] is False and kwargs["text"]["format"]["strict"] is True
    client.close.assert_awaited_once()
    metadata = reviewer.metadata()
    assert metadata["calls"][0]["usage"]["total_tokens"] == 15
    assert "PRIVATE-TEST-KEY" not in json.dumps(metadata)
    metadata["calls"][0]["usage"]["total_tokens"] = 0
    assert reviewer.metadata()["calls"][0]["usage"]["total_tokens"] == 15


@pytest.mark.parametrize("failure", ["truncate", "refuse", "empty", "invalid_json", "network"])
async def test_provider_failure(
    monkeypatch, provider_config, provider_policy, behavior_request, caplog, failure
):
    """功能：验证错误拒绝；输入：五类模拟异常；输出：error、无重试、关闭客户端且日志不含原文。"""
    client, _ = sdk_stub(monkeypatch, "PRIVATE-BAD-JSON")
    response = client.chat.completions.create.return_value
    if failure == "truncate":
        response.choices[0].finish_reason = "length"
    elif failure == "refuse":
        response.choices[0].message.refusal = "PRIVATE-REFUSAL"
    elif failure == "empty":
        response.choices = []
    elif failure == "network":
        client.chat.completions.create.side_effect = RuntimeError("PRIVATE-NETWORK-ERROR")
    reviewer = ProjectBehaviorReviewer(module.ProjectModelTransport(provider_config, 30))
    result = await BehaviorEngine(provider_policy, reviewer).check(behavior_request)
    await reviewer.aclose()
    assert result.status == "error" and result.error_code == "semantic_failure"
    client.chat.completions.create.assert_awaited_once()
    client.close.assert_awaited_once()
    assert "PRIVATE" not in caplog.text + json.dumps(reviewer.metadata())


async def test_provider_cancellation(
    monkeypatch, provider_config, provider_policy, behavior_request
):
    """功能：验证超时取消；输入：永久等待的模拟调用；输出：timeout 且 SDK 退出，无后台同步线程。"""

    async def wait_response(**kwargs):
        """功能：模拟在途请求；输入：SDK 关键字参数；输出：不主动返回，仅由外层超时取消。"""
        await asyncio.Event().wait()

    client, _ = sdk_stub(monkeypatch, "")
    client.chat.completions.create.side_effect = wait_response
    reviewer = ProjectBehaviorReviewer(module.ProjectModelTransport(provider_config, 30))
    result = await BehaviorEngine(provider_policy, reviewer).check(behavior_request)
    await reviewer.aclose()
    assert result.error_code == "semantic_timeout"
    assert reviewer.metadata()["calls"][0]["status"] == "cancelled"
    client.close.assert_awaited_once()


def test_factory_precedence(monkeypatch, provider_config):
    """功能：核对根目录路径与配置优先级；输入：模拟文件和环境。
    输出：只读取根目录 .env、环境模型优先，源配置不被写回；不读取真实凭据或联网。
    """
    file_config = MagicMock(return_value=dict(provider_config))
    monkeypatch.setattr(module, "dotenv_values", file_config)
    monkeypatch.setattr(module.os, "environ", {"DASHSCOPE_MODEL": "environment-model"})
    monkeypatch.setattr(
        module,
        "load_agent_settings",
        lambda: SimpleNamespace(timeouts=SimpleNamespace(llm_generation_seconds=30)),
    )
    reviewer = module.create_transport()
    file_config.assert_called_once_with(module.Path(module.__file__).resolve().parents[1] / ".env")
    assert reviewer.model == "environment-model"
    assert provider_config["DASHSCOPE_MODEL"] == "test-model"
    assert dict(module.os.environ) == {"DASHSCOPE_MODEL": "environment-model"}


def test_missing_credentials(provider_config):
    """功能：核对启动失败；输入：无凭据配置；输出：ValueError；不进入默认模型或匿名请求路径。"""
    provider_config["DASHSCOPE_API_KEY"] = ""
    with pytest.raises(ValueError, match="API_KEY"):
        ProjectBehaviorReviewer(module.ProjectModelTransport(provider_config, 30))


@pytest.mark.parametrize("value", ["", None, "nan", "inf", "-1", "0", "invalid"])
def test_keepalive_configuration(monkeypatch, provider_config, value):
    """Functionality: Reject invalid network configuration. Inputs: Dummy provider plus malformed,
    nonfinite or nonpositive idle lifetime. Outputs: Explicit ValueError before resource setup.
    Logic: Guard the SDK constructor and trust-loader against use. Constraints: No credential
    content in errors, no default substitution for an explicit invalid value, no network traffic.
    """
    provider_config["AI_SECURITY_KEEPALIVE_SECONDS"] = value
    constructor = MagicMock()
    trust = MagicMock()
    monkeypatch.setattr(module, "AsyncOpenAI", constructor)
    monkeypatch.setattr(module, "_verified_tls_context", trust)
    with pytest.raises(ValueError, match="AI_SECURITY_KEEPALIVE_SECONDS"):
        module.ProjectModelTransport(provider_config, 30)
    constructor.assert_not_called()
    trust.assert_not_called()


async def test_keepalive_override(provider_config):
    """Functionality: Verify explicit configuration reaches the installed SDK HTTP pool.
    Inputs: Dummy provider configuration with the original five-second control value.
    Outputs: Real pool limits and exported metadata match that value; client closes explicitly.
    Logic: Construct the actual client without issuing a request and inspect its pool configuration.
    Constraints: No external service validation or model call; loop ownership/cleanup stay original.
    """
    provider_config["AI_SECURITY_KEEPALIVE_SECONDS"] = "5"
    transport = module.ProjectModelTransport(provider_config, 30)
    try:
        pool = transport._client_for_call()._client._transport._pool
        assert pool._keepalive_expiry == 5
        assert pool._max_connections == module.DEFAULT_CONNECTION_LIMITS.max_connections
        assert (
            pool._max_keepalive_connections
            == module.DEFAULT_CONNECTION_LIMITS.max_keepalive_connections
        )
        assert transport.metadata()["http_connection_limits"]["keepalive_expiry_seconds"] == 5
        assert transport.metadata()["sdk_max_retries"] == 0
        assert transport.metadata()["sdk_timeout_seconds"] == 30
    finally:
        await transport.aclose()
