"""Responsibilities: Verify public-data provenance, label semantics and native transport contracts.
Implementation: Check all frozen fixture hashes/splits offline and simulate both provider SDKs.
Related Modules: prepare/collect define independent labels; run verifies dataset provenance.
Declaration Index:
- test_public_integrity: Verify source bytes, labels, raw input preservation and task group splits.
- test_provenance_tampering: Reject any dataset whose frozen hash no longer matches.
- test_protocol_conditions: Reject changed experimental conditions before creating a model client.
- test_native_response_binding: Verify guarded responses exactly match the recorded target outputs.
- test_oracle: Verify the independent literal-disclosure oracle including its documented limits.
- native_stub: Create a provider-neutral SDK stub without reading credentials or making requests.
- test_native_messages: Verify unchanged original messages and absence of JSON/tools/retries.
- test_native_failure: Verify truncation/refusal/empty errors propagate without repair or retry.
- test_native_cancellation: Verify cancellation records status and closes the client.
- test_native_cancellation.pending: Simulate an unfinished SDK response until cancellation.
Variable Index:
- HERE: Public fixture directory.
- ROOT: Repository root.
"""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_security import project_provider
from security_evaluation import run as evaluation_run
from security_evaluation.run import load_cases, load_provenance

from .collect import literal_leak
from .prepare import digest, partition, secret

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def test_public_integrity():
    """Functionality: Verify frozen source/fixture integrity and grouping without external services.
    Inputs: Public protocol, license, raw data and constructed cases. Outputs: Assertions.
    Logic: Check hashes, unique upstream IDs, exact raw attacks, matched pairs, strict contracts,
    independent literal labels and no original system-task group across development/holdout.
    Constraints: Does not certify annotations or benchmark model performance; no network/DB access.
    """
    protocol = json.loads((HERE / "protocol-v1.json").read_text(encoding="utf-8"))
    for source in protocol["sources"]:
        assert digest((HERE / "raw" / source["file"]).read_bytes()) == source["sha256"]
    assert "MIT License" in (HERE / "raw/LICENSE").read_text()
    originals = json.loads((HERE / "raw/prompt_injection.json").read_bytes())
    assert len(originals) == protocol["upstream_rows"] == 251
    by_id = {row["prompt_id"]: row for row in originals}
    assert len(by_id) == len(originals)
    groups = {"development": set(), "holdout": set()}
    for name in protocol["dataset_sha256"]:
        load_provenance(HERE / "protocol-v1.json", HERE / "data" / name)
        cases = load_cases(HERE / "data" / name)
        labels = {}
        for case, request in cases:
            original = by_id[case["public_source_id"]]
            assert case["partition"] == partition(original)
            assert request.evidence[0].text == original["user_input"]
            groups[case["partition"]].add(" ".join(original["test_case_prompt"].split()))
            labels.setdefault(case["public_source_id"], set()).add(case["expected"])
            if name.startswith("native"):
                assert request.boundary.task_purpose == original["test_case_prompt"]
                assert literal_leak(request.proposal.content.text, secret(original)) == (
                    case["expected"] == "deny"
                )
        assert all(value == {"allow", "deny"} for value in labels.values())
    assert not groups["development"] & groups["holdout"]
    assert (
        digest((ROOT / "security_evaluation/data/policy_v1.json").read_bytes())
        == protocol["policy_sha256"]
    )


def test_provenance_tampering(tmp_path):
    """Functionality: Reject changed benchmark bytes before a reviewer could be called.
    Inputs: Temporary altered copy of a frozen dataset. Outputs: ValueError assertion.
    Logic: Preserve filename but change bytes; compare to immutable protocol.
    Constraints: This unit test calls only provenance loading and performs no model calls.
    """
    altered = tmp_path / "interview-development.jsonl"
    altered.write_bytes((HERE / "data" / altered.name).read_bytes() + b"\n")
    with pytest.raises(ValueError, match="provenance"):
        load_provenance(HERE / "protocol-v1.json", altered)


@pytest.mark.parametrize("change", ["repeats", "concurrency", "timeout", "budget", "version"])
async def test_protocol_conditions(monkeypatch, tmp_path, change):
    """Functionality: Protect frozen experiment comparability against silent configuration changes.
    Inputs: Five altered conditions and a model-factory trap. Outputs: Rejection.
    Logic: Change one parameter; evaluate must fail before client creation or output writes.
    Constraints: Only temporary policy files are written. No network, DB or configuration access;
    verifies protocol enforcement rather than whether a particular threshold is appropriate.
    """
    values = json.loads((ROOT / "security_evaluation/data/policy_v1.json").read_text())
    for key, value in {
        "timeout": ("semantic_timeout_seconds", 6.0),
        "budget": ("max_scan_chars", 99999),
        "version": ("policy_version", "different-version"),
    }.items():
        if change == key:
            values[value[0]] = value[1]
    policy = tmp_path / "policy.json"
    if change in {"repeats", "concurrency"}:
        # Keep exact policy bytes so these cases exercise the run-condition checks themselves.
        policy.write_bytes((ROOT / "security_evaluation/data/policy_v1.json").read_bytes())
    else:
        policy.write_text(json.dumps(values), encoding="utf-8")
    factory = MagicMock(side_effect=AssertionError("model created before protocol verification"))
    monkeypatch.setattr(evaluation_run, "create_behavior_reviewer", factory)
    args = argparse.Namespace(
        dataset=HERE / "data/interview-development.jsonl",
        policy=policy,
        provenance=HERE / "protocol-v1.json",
        output=tmp_path / "results",
        repeats=2 if change == "repeats" else 1,
        concurrency=2 if change == "concurrency" else 1,
    )
    with pytest.raises(ValueError, match="conditions"):
        await evaluation_run.evaluate(args)
    factory.assert_not_called()
    assert not args.output.exists()


def test_native_response_binding():
    """Functionality: Verify collected native outputs and independent labels are frozen together.
    Inputs: Recorded target responses, original public rows, report and derived datasets/protocol.
    Outputs: Exact text, oracle label and denominator assertions for every successful target.
    Logic: Bind each case to its source ID and recorded response, recompute labels with the public
    literal oracle, and retain failed targets outside labeled cases but inside attempted counts.
    Constraints: Offline artifact verification only; does not query the target or evaluated guard,
    and cannot prove completeness of the narrow oracle or independent human adjudication.
    """
    directory = HERE / "results/native-targets"
    original = {
        r["prompt_id"]: r for r in json.loads((HERE / "raw/prompt_injection.json").read_bytes())
    }
    responses = [
        json.loads(line)
        for line in (directory / "responses.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    successful = {r["public_source_id"]: r for r in responses if r["status"] == "received"}
    checked = set()
    for split in ("development", "holdout"):
        dataset = directory / f"native-responses-{split}.jsonl"
        load_provenance(directory / "native-protocol.json", dataset)
        for case, request in load_cases(dataset):
            source_id = case["public_source_id"]
            response = successful[source_id]
            assert source_id not in checked
            checked.add(source_id)
            assert request.proposal.content.text == response["response"]
            assert request.boundary.task_purpose == original[source_id]["test_case_prompt"]
            assert literal_leak(response["response"], secret(original[source_id])) == (
                case["expected"] == "deny"
            )
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert checked == successful.keys()
    assert report["attempted"] == len(responses)
    assert report["received"] == len(successful)
    assert report["errors_unscored"] == len(responses) - len(successful)


@pytest.mark.parametrize(
    "output,leaked",
    [
        ("Alpha123", True),
        ("ALPHA123", True),
        ("Ａｌｐｈａ１２３", True),
        ("Alpha1234", False),
        ("I cannot disclose it.", False),
        ("QWxwaGExMjM=", False),
    ],
)
def test_oracle(output, leaked):
    """Functionality: Verify oracle boundaries and explicitly expose encoded-leak limitations.
    Inputs: Six public fake-secret examples. Outputs: Literal detection assertions.
    Logic: Match case/width variants and exclude larger words/absent/encoded token.
    Constraints: Encoding example verifies a limitation, not safe authorization of encoded leakage.
    """
    assert literal_leak(output, "Alpha123") is leaked


def native_stub(monkeypatch):
    """Functionality: Build an observable SDK substitute for isolated transport verification.
    Inputs: pytest monkeypatch. Outputs: Mock async client and transport constructor.
    Logic: Supply complete provider-compatible text/usage responses and async cleanup.
    Constraints: Replaces AsyncOpenAI before transport initialization; no real credentials/network.
    """
    client = MagicMock()
    client.close = AsyncMock()
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="fixture output", refusal=None),
            )
        ],
        usage=None,
        status="completed",
        output=[],
        output_text="fixture output",
        model="fixture-model",
    )
    client.chat.completions.create = AsyncMock(return_value=response)
    client.responses.create = AsyncMock(return_value=response)
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(project_provider, "AsyncOpenAI", constructor)
    monkeypatch.setattr(project_provider, "DefaultAsyncHttpxClient", MagicMock())
    return client, constructor


@pytest.mark.parametrize("provider", ["dashscope", "openai"])
async def test_native_messages(monkeypatch, provider):
    """Functionality: Verify native tasks are not altered by structured safety transport formatting.
    Inputs: Both mocked providers and original-message canaries. Outputs: SDK contract assertions.
    Logic: Require exact system/user text, unchanged temperature/timeout, zero retries and cleanup.
    Constraints: Provider behavior is simulated; online collection separately verifies real calls.
    """
    client, constructor = native_stub(monkeypatch)
    configuration = {
        "LLM_PROVIDER": provider,
        f"{provider.upper()}_API_KEY": "fake-key",
        f"{provider.upper()}_MODEL": "fixture-model",
        "OPENAI_TEMPERATURE": "0",
    }
    transport = project_provider.ProjectModelTransport(configuration, 30)
    assert await transport.generate_text("original system", "original input") == "fixture output"
    await transport.aclose()
    method = client.chat.completions.create if provider == "dashscope" else client.responses.create
    kwargs = method.call_args.kwargs
    assert kwargs.get("messages", kwargs.get("input")) == [
        {"role": "system", "content": "original system"},
        {"role": "user", "content": "original input"},
    ]
    assert not {"response_format", "text", "tools"} & kwargs.keys()
    assert kwargs["temperature"] == 0
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert constructor.call_args.kwargs["timeout"] == 30
    method.assert_awaited_once()
    client.close.assert_awaited_once()
    call = transport.metadata()["calls"][0]
    assert call["status"] == "received"
    assert call["sdk_await_ms"] >= 0
    assert call["client_setup_ms"] >= 0
    assert call["total_ms"] >= call["sdk_await_ms"]


@pytest.mark.parametrize("failure", ["truncated", "refused", "empty"])
async def test_native_failure(monkeypatch, failure):
    """Functionality: Preserve explicit native target failure semantics.
    Inputs: Three mocked invalid responses. Outputs: ValueError/no-retry/cleanup assertions.
    Logic: Change one response property and call plain-text generation once.
    Constraints: No implicit JSON path, response repair or model substitution follows a failure.
    """
    client, _ = native_stub(monkeypatch)
    response = client.chat.completions.create.return_value
    if failure == "truncated":
        response.choices[0].finish_reason = "length"
    elif failure == "refused":
        response.choices[0].message.refusal = "refused"
    else:
        response.choices[0].message.content = ""
    transport = project_provider.ProjectModelTransport(
        {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "fake", "DASHSCOPE_MODEL": "fake"}, 30
    )
    with pytest.raises(ValueError):
        await transport.generate_text("system", "input")
    await transport.aclose()
    client.chat.completions.create.assert_awaited_once()
    client.close.assert_awaited_once()
    assert transport.metadata()["calls"][0]["status"] == "failed"
    assert transport.metadata()["calls"][0]["total_ms"] >= 0


async def test_native_cancellation(monkeypatch):
    """Functionality: Verify plain-text request cancellation releases resources and remains visible.
    Inputs: Mock unfinished SDK request. Outputs: Timeout, cancelled metadata and cleanup checks.
    Logic: Cancel with an explicit short unit-test deadline, separate from any production setting.
    Constraints: No billed request or timeout adjustment; cancellation propagates out of transport.
    """

    async def pending(**kwargs):
        """Functionality: Simulate an unfinished SDK call.
        Inputs: SDK keyword arguments. Outputs: None until cancelled. Logic: Await an unset event.
        Constraints: No network or side effects; cancellation is deliberately not swallowed.
        """
        await asyncio.Event().wait()

    client, _ = native_stub(monkeypatch)
    client.chat.completions.create.side_effect = pending
    transport = project_provider.ProjectModelTransport(
        {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "fake", "DASHSCOPE_MODEL": "fake"}, 30
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(transport.generate_text("system", "input"), timeout=0.01)
    await transport.aclose()
    assert transport.metadata()["calls"][0]["status"] == "cancelled"
    assert transport.metadata()["calls"][0]["sdk_await_ms"] > 0
    client.close.assert_awaited_once()
