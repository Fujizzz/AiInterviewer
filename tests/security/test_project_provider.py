"""职责：验证项目模型适配器的请求、单次调用、资源释放及脱敏边界，不访问真实供应商。
实现：用异步 SDK 替身模拟正常返回、截断、拒绝、错误及取消，并经安全引擎执行契约检查。
关联：project_provider 读取真实配置的代码通过临时模拟隔离，测试不读取本地凭据。

目录：
- provider_config：提供虚构供应商配置。
- provider_policy：将行为许可用于供应商测试，保持既有 0.1 秒取消测试时限。
- sdk_stub：创建可观测的异步 SDK 替身。
- test_provider_request：检查两供应商的参数、数据隔离、用量和客户端释放。
- test_provider_failure：检查拒绝、截断、空响应、非法 JSON 与错误不重试。
- test_provider_cancellation：检查超时取消进入 SDK 并释放客户端。
- test_provider_cancellation.wait_response：接受 SDK 参数并等待取消。
- test_factory_precedence：确认只读取统一根目录配置，进程环境覆盖文件且不改变环境。
- test_missing_credentials：缺少凭据时立即失败。

关键变量：
（无）
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_security import BehaviorEngine
from ai_security import project_provider as module
from ai_security.behavior_semantic import ProjectBehaviorReviewer
from shared.contracts.behavior import BehaviorAssessment


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
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
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
    return client, constructor


@pytest.mark.parametrize("provider", ["dashscope", "openai"])
async def test_provider_request(monkeypatch, provider_config, behavior_request, provider):
    """功能：检查供应商请求；输入：模拟 compliant 响应；输出：单次生成、独立指令和脱敏配置/用量。"""
    provider_config["LLM_PROVIDER"] = provider
    client, constructor = sdk_stub(
        monkeypatch,
        BehaviorAssessment(
            verdict="compliant",
            checked_requirement_ids=tuple(
                p.requirement_ids
                for p in behavior_request.boundary.permits
                if p.operation == behavior_request.proposal.operation
            )[0],
            violated_requirement_ids=(),
        ).model_dump_json(),
    )
    reviewer = ProjectBehaviorReviewer(module.ProjectModelTransport(provider_config, 30))
    result = await reviewer.assess(behavior_request)
    assert result.verdict == "compliant"
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert constructor.call_args.kwargs["timeout"] == 30
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
    client.__aexit__.assert_awaited_once()
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
    assert result.status == "error" and result.error_code == "semantic_failure"
    client.chat.completions.create.assert_awaited_once()
    client.__aexit__.assert_awaited_once()
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
    assert result.error_code == "semantic_timeout"
    assert reviewer.metadata()["calls"][0]["status"] == "cancelled"
    client.__aexit__.assert_awaited_once()


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
