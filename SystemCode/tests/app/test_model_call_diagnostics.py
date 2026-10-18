"""Real async/worker failure paths, without network requests."""

import asyncio
from threading import Event
from time import perf_counter
from unittest.mock import patch

import pytest

from agents.config import load_agent_settings
from agents.model_calls import ModelCall, current_model_call, run_model_call, safe_error_details
from agents.tracing import trace_sink
from app.adapters.llm import GeneratedText
from app.providers.llm import LLMError, OpenAILLM
from app.tracing import FileTrace, TracedLLM
from tests.app.test_qwen_pdf import response


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["model_call", "react_total"])
async def test_timeout_reports_question_step_and_correct_deadline(scope):
    events = []
    token = trace_sink.set(lambda event, data: events.append((event, data)))
    try:
        with pytest.raises(TimeoutError):
            await run_model_call(
                lambda: asyncio.sleep(0.1),
                operation="question",
                question_id="q1",
                step=2,
                timeout_seconds=0.01 if scope == "model_call" else 0.2,
                turn_deadline=perf_counter() + (0.01 if scope == "react_total" else 1),
            )
    finally:
        trace_sink.reset(token)
    errors = [data for event, data in events if event == "model.error"]
    assert len(errors) == 1
    assert errors[0]["scope"] == scope
    assert errors[0]["question_id"] == "q1"
    assert errors[0]["step"] == 2
    assert errors[0]["duration_ms"] >= 0
    assert errors[0]["call_id"]
    assert current_model_call.get() is None


@pytest.mark.asyncio
async def test_late_worker_error_cannot_pollute_next_question():
    events, entered, release, finished = [], Event(), Event(), Event()

    def slow_provider(*args):
        entered.set()
        try:
            release.wait(1)
            raise LLMError("SECRET provider body")
        finally:
            finished.set()

    token = trace_sink.set(lambda event, data: events.append((event, data)))
    try:
        with pytest.raises(TimeoutError):
            await run_model_call(
                lambda: asyncio.to_thread(TracedLLM(slow_provider), "", {}, GeneratedText),
                operation="question",
                question_id="q1",
                step=1,
                timeout_seconds=0.02,
            )
        assert entered.is_set()
        release.set()
        await asyncio.to_thread(finished.wait, 1)
        result = await run_model_call(
            lambda: asyncio.sleep(0, result="next"),
            operation="question",
            question_id="q2",
            step=1,
            timeout_seconds=0.1,
        )
        assert result == "next"
    finally:
        release.set()
        trace_sink.reset(token)
    errors = [data for event, data in events if event == "model.error"]
    assert len(errors) == 1
    assert errors[0]["question_id"] == "q1"
    assert "SECRET" not in str(events)


def test_provider_obeys_remaining_deadline_and_does_not_nest_question_repair():
    with (
        patch("app.providers.llm.load_dotenv"),
        patch.dict(
            "os.environ",
            {
                "LLM_PROVIDER": "dashscope",
                "DASHSCOPE_API_KEY": "test-key",
                "DASHSCOPE_MODEL": "test-model",
                "OPENAI_TEMPERATURE": "0",
            },
            clear=True,
        ),
        patch("app.providers.llm.OpenAI") as client,
    ):
        client.return_value.chat.completions.create.return_value = response("bad JSON")
        model = OpenAILLM()
        token = current_model_call.set(ModelCall("question", "q1", 1, perf_counter() + 2))
        try:
            with pytest.raises(LLMError) as captured:
                model("", {}, GeneratedText)
        finally:
            current_model_call.reset(token)
        assert captured.value.code == "invalid_json"
        create = client.return_value.chat.completions.create
        assert create.call_count == 1
        assert 0 < create.call_args.kwargs["timeout"] <= 2
        assert client.call_args.kwargs["max_retries"] == 0
        assert client.call_args.kwargs["timeout"] == 30
    assert load_agent_settings().question_agent.total_timeout_seconds == 90


def test_error_logging_only_contains_safe_labels(tmp_path):
    class ProviderError(Exception):
        status_code = 429

    error = LLMError("SECRET credential and body")
    error.__cause__ = ProviderError("SECRET response")
    details = safe_error_details(error)
    assert details["category"] == "http_error"
    assert details["status_code"] == 429
    assert "SECRET" not in str(details)
    with FileTrace(tmp_path) as trace:
        trace.emit("question.started", {"question_id": "q1"})
        trace.emit(
            "model.error",
            {
                **details,
                "operation": "question",
                "question_id": "q1",
                "call_id": "call1",
                "step": 2,
                "duration_ms": 12,
            },
        )
        trace.save_result({"final_report": {"overall_score": None, "summary": "No evidence"}})
    record = trace.path.read_text(encoding="utf-8")
    assert "HTTP：429" in record and "第 1 题 / question / 调用 2" in record
    assert "证据不足，暂不评分" in record
    assert "None / 5" not in record and "SECRET" not in record
