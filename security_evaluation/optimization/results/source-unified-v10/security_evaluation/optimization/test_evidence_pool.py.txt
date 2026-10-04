"""Responsibilities: Verify complete proposal references and owned transport cancellation/cleanup.
Implementation: Inspect immutable spans offline; exercise real OpenAI/HTTPX response handling with
an in-memory HTTP transport, including a suspended response stream and a healthy pooled request.
Related Modules: behavior_evidence builds references; project_provider owns the client lifecycle.
Declaration Index:
- request_fixture: Rebind only a local fixture copy to explicit proposal/evidence text.
- test_plain_spans_complete: Verify all Unicode text/offsets and exclusion of input evidence.
- test_json_spans_complete: Verify all JSON leaves, escaped pointers, empty containers and scalars.
- test_ambiguous_text_view: Preserve complete text when strict JSON recognition is unavailable.
- test_span_index_rejection: Reject negative/bool/unknown proposal references.
- SuspendedStream: In-memory HTTP response stream waiting for cancellation.
- SuspendedStream.__init__: Bind an observable closure event.
- SuspendedStream.__aiter__: Wait until the SDK request is cancelled without external I/O.
- SuspendedStream.aclose: Mark actual HTTP stream release.
- test_pool_cancel_and_reuse: Verify SDK/HTTPX stream cleanup, reuse and explicit owner close.
- test_pool_cancel_and_reuse.handle: Return a suspended or complete in-memory HTTP response.
- test_pool_cancel_and_reuse.http_client: Preserve verified TLS configuration with MockTransport.
- test_trace_concurrent_isolation: Isolate simultaneous timing and discard sensitive trace info.
- test_trace_concurrent_isolation.operation: Synchronize two task-local hook invocations.
Variable Index:
- ROOT: Repository root containing unchanged frozen request structure.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import DefaultAsyncHttpxClient

from ai_security import project_provider
from ai_security.behavior_evidence import SPAN_CHARS, proposal_spans
from ai_security.behavior_semantic import JsonBehaviorReviewer
from shared.contracts.behavior import BehaviorRequest

ROOT = Path(__file__).resolve().parents[2]


def request_fixture(text):
    """Functionality: Prepare an isolated proposal-view fixture. Inputs: Explicit output text.
    Outputs: Strict request. Logic: Copy a frozen structure and add unique input-only marker.
    Constraints: Unit fixture only; no online labels, permissions or actual provider state changed.
    """
    value = json.loads(
        (ROOT / "security_evaluation/data/interview_cases_v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )["request"]
    value["proposal"]["content"]["text"] = text
    value["evidence"][0]["text"] = "PRIVATE_INPUT_ONLY_MARKER"
    return BehaviorRequest.model_validate_json(json.dumps(value))


def test_plain_spans_complete():
    """Functionality: Preserve every plain-text Unicode character. Inputs: Multi-chunk text.
    Outputs: Exact reconstruction, contiguous offsets and immutable references. Logic: Concatenate
    all content spans. Constraints: No truncation or input-derived witness; not model accuracy.
    """
    text = "完整原文\n" * 200 + "END"
    spans = [s for s in proposal_spans(request_fixture(text)) if s.source == "content"]
    assert "".join(s.text for s in spans) == text
    assert all(s.text == text[s.start : s.end] and len(s.text) <= SPAN_CHARS for s in spans)
    assert [s.start for s in spans] == list(range(0, len(text), SPAN_CHARS))
    assert all("PRIVATE_INPUT_ONLY" not in s.text for s in spans)
    with pytest.raises(ValueError):
        spans[0].text = "injected"


def test_json_spans_complete():
    """Functionality: Reference actual decoded JSON values without modifying original content.
    Inputs: Escaped string/pointer, integer/bool/null and empty list/object. Outputs: Exact leaves.
    Logic: Traverse complete valid JSON and preserve raw request separately. Constraints: All
    fields remain present; quoted input data is not promoted to policy or a backend requirement.
    """
    value = {
        "with/slash~key": 'text\n"quoted"\u2705',
        "n": 5,
        "b": False,
        "nil": None,
        "list": [],
        "object": {},
    }
    text = json.dumps(value, ensure_ascii=False)
    request = request_fixture(text)
    spans = {s.path: s.text for s in proposal_spans(request) if s.source == "content"}
    assert spans == {
        "/with~1slash~0key": value["with/slash~key"],
        "/n": "5",
        "/b": "false",
        "/nil": "null",
        "/list": "[]",
        "/object": "{}",
    }
    assert request.proposal.content.text == text


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', "NaN", "{unfinished", ""])
def test_ambiguous_text_view(text):
    """Functionality: Retain unrecognized free-form content completely. Inputs: Four data strings.
    Outputs: Exact raw text references. Logic: The contract accepts free-form text; only a whole
    strict JSON document has decoded leaves. Constraints: This does not repair model verdict JSON
    or authorize publication; semantic requirements and response validation remain mandatory.
    """
    spans = [s for s in proposal_spans(request_fixture(text)) if s.source == "content"]
    assert "".join(s.text for s in spans) == text


@pytest.mark.parametrize("span", [-1, True, "0", 999999])
async def test_span_index_rejection(span):
    """Functionality: Reject forged witness references. Inputs: Four invalid indices and a mock.
    Outputs: Validation exception. Logic: Require strict integer and actual span bounds.
    Constraints: No inferred mapping or quoted evidence can substitute for a proposal reference.
    """
    request = request_fixture("Output")
    raw = json.dumps(
        {
            "basis": "The output violates the selected requirement.",
            "verdict": "noncompliant",
            "checked": [0],
            "witness": {"requirement": 0, "span": span},
        }
    )
    with pytest.raises(ValueError):
        await JsonBehaviorReviewer(AsyncMock(return_value=raw)).assess(request)


class SuspendedStream(httpx.AsyncByteStream):
    """Functionality: Simulate an unfinished HTTP body. Logic: Wait on an unset event and expose
    closure. Constraints: In-memory transport only; no actual network, model or service reliability.
    """

    def __init__(self, closed):
        """Functionality: Bind closure observation. Inputs: asyncio event. Outputs: Stream.
        Logic: Store event only. Constraints: No I/O or timers are started by construction.
        """
        self.closed = closed

    async def __aiter__(self):
        """Functionality: Suspend response consumption. Inputs: Implicit current task cancellation.
        Outputs: No body before cancellation. Logic: Wait forever on an unset async event.
        Constraints: Async generator interface only; cancellation is not swallowed.
        """
        await asyncio.Event().wait()
        yield b""

    async def aclose(self):
        """Functionality: Mark actual stream cleanup. Inputs: Stored event. Outputs: None.
        Logic: Set event. Constraints: No retry or replacement response is produced.
        """
        self.closed.set()


async def test_pool_cancel_and_reuse(monkeypatch):
    """Functionality: Verify real SDK stream cancellation and owned pool reuse/close.
    Inputs: In-memory HTTP transport and fake configuration. Outputs: Closed cancelled stream,
    one healthy next request, one client reused, and no requests after explicit close.
    Logic: Cancel an incomplete body then use the same client and close it. Constraints: Exercises
    actual SDK/HTTPX machinery but no real server, TLS handshake or model recognition is verified.
    """
    closed, calls, clients = asyncio.Event(), [], []

    async def handle(request):
        """Functionality: Return observable in-memory responses. Inputs: SDK HTTP request.
        Outputs: Suspended stream or complete response. Logic: Inspect only fake user text.
        Constraints: No network, keys/headers logging, retries or external side effects.
        """
        user = json.loads(request.content)["messages"][1]["content"]
        calls.append(user)
        await request.extensions["trace"](
            "connection.connect_tcp.started", {"credential": "PRIVATE_TRACE_INFO"}
        )
        await request.extensions["trace"]("PRIVATE_EVENT_NAME", {"body": "PRIVATE_TRACE_BODY"})
        if user == "blocked":
            return httpx.Response(200, stream=SuspendedStream(closed))
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "chat.completion",
                "created": 0,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "complete"},
                    }
                ],
            },
        )

    def http_client(**kwargs):
        """Functionality: Preserve SDK defaults with a fake transport. Inputs: Verified TLS context.
        Outputs: Real async HTTP client. Logic: Bind MockTransport and record the instance.
        Constraints: Cannot reach the network; no options or certificate validation are relaxed.
        """
        client = DefaultAsyncHttpxClient(**kwargs, transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    monkeypatch.setattr(project_provider, "DefaultAsyncHttpxClient", http_client)
    transport = project_provider.ProjectModelTransport(
        {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "fake", "DASHSCOPE_MODEL": "fixture"}, 30
    )
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                await transport.generate_text("fixture", "blocked")
        assert closed.is_set() and not clients[0].is_closed
        assert await transport.generate_text("fixture", "healthy") == "complete"
    finally:
        await transport.aclose()
    assert clients[0].is_closed and len(clients) == 1
    assert calls == ["blocked", "healthy"]
    assert transport.metadata()["calls"][0]["status"] == "cancelled"
    assert transport.metadata()["calls"][1]["client_reused"] is True
    metadata = transport.metadata()
    assert "PRIVATE_" not in json.dumps(metadata)
    assert metadata["calls"][0]["http_requests"] == 1
    assert metadata["calls"][1]["http_response_headers_ms"] >= 0
    metadata["calls"][0]["http_trace"][0]["event"] = "changed external copy"
    assert transport.metadata()["calls"][0]["http_trace"][0]["event"] == (
        "connection.connect_tcp.started"
    )
    with pytest.raises(RuntimeError, match="already closed"):
        await transport.generate_text("fixture", "unreachable")
    assert calls == ["blocked", "healthy"]


async def test_trace_concurrent_isolation():
    """Functionality: Verify task-local timing with concurrent calls. Inputs: Two synthetic hook
    operations and sensitive trace-info traps. Outputs: One event/send per own record, both
    responses timed, no private data retained. Logic: Synchronize both operations within separate
    tasks and exercise real hook/context code. Constraints: No SDK/network/model call; this
    verifies observability isolation, not provider throughput or response behavior.
    """
    transport = project_provider.ProjectModelTransport(
        {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "fake", "DASHSCOPE_MODEL": "fixture"}, 30
    )
    records, entered = [{}, {}], [asyncio.Event(), asyncio.Event()]

    async def operation(marker):
        """Functionality: Synchronize explicit task-local HTTP hooks. Inputs: Index and enclosing
        test records/events. Outputs: Index. Logic: Bind a dummy request, wait for both tasks,
        exercise trace and response hooks. Constraints: No actual HTTP send or secret logging.
        """
        request = httpx.Request("POST", "https://fixture.invalid")
        await transport._http_request(request)
        entered[marker].set()
        await entered[1 - marker].wait()
        await request.extensions["trace"](
            "connection.start_tls.complete", {"return_value": "PRIVATE_TLS_INFO"}
        )
        await transport._http_response(object())
        return marker

    try:
        assert await asyncio.gather(
            transport._await_response(operation, records[0], marker=0),
            transport._await_response(operation, records[1], marker=1),
        ) == [0, 1]
    finally:
        await transport.aclose()
    for record in records:
        assert record["http_requests"] == 1 and len(record["http_trace"]) == 1
        assert record["http_trace"][0]["event"] == "connection.start_tls.complete"
        assert record["http_response_headers_ms"] >= 0
    assert "PRIVATE_" not in json.dumps(records)
