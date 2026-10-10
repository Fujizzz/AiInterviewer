"""Check DeepSeek routing and bounded schema repair without external requests."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ai_security.project_provider import ProjectModelTransport
from app.adapters.llm import GeneratedText
from app.providers.llm import OpenAILLM
from tests.app.test_qwen_pdf import response

CONFIG = {
    "LLM_PROVIDER": "deepseek",
    "DEEPSEEK_API_KEY": "test-only",
    "DEEPSEEK_MODEL": "deepseek-flash",
}


@pytest.mark.parametrize("backend", [False, True])
def test_chat_routing_and_repair(backend, monkeypatch):
    """Both entry points use the DeepSeek body, preserve repair and disable SDK retries."""
    if backend:
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "backend"))
        from interviews.agent_provider import BackendLLM

        factory, target = BackendLLM, "interviews.agent_provider.OpenAI"
    else:
        factory, target = OpenAILLM, "app.providers.llm.OpenAI"
    with patch.dict("os.environ", CONFIG, clear=True), patch(target) as sdk, patch(
        "app.providers.llm.load_dotenv"
    ):
        create = sdk.return_value.chat.completions.create
        create.side_effect = [response('{"wrong":true}'), response('{"text":"ok"}')]
        assert factory()("Return JSON", {}, GeneratedText).text == "ok"
        assert create.call_count == 2
        assert create.call_args.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert create.call_args.kwargs["timeout"] == 30
        assert sdk.call_args.kwargs["base_url"] == "https://api.deepseek.com"
        assert sdk.call_args.kwargs["max_retries"] == 0
        sdk.return_value.responses.parse.assert_not_called()


def test_safety_transport_uses_deepseek_chat():
    """Safety checks use the same provider without inheriting business repairs."""
    async def run():
        transport = ProjectModelTransport(CONFIG, 30)
        completion = response('{"text":"ok"}')
        completion.usage, completion.model = None, "deepseek-flash"
        with patch.object(transport, "_client_for_call") as client:
            create = AsyncMock(return_value=completion)
            client.return_value.chat.completions.create = create
            assert await transport.generate("Check JSON", {}, {"type": "object"})
            assert create.call_count == 1
            assert create.call_args.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
            assert transport.metadata()["thinking"] == "disabled"
        await transport.aclose()

    asyncio.run(run())
