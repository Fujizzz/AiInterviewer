"""Responsibilities: Test security boundaries, failure semantics and evaluation correctness offline.
Implementation: Use explicit model ports, frozen synthetic data and isolated callbacks, never APIs.
Related Modules: ai_security is the tested implementation; metrics supplies report denominators.

Declaration Index:
- base_request: Load a synthetic production-shaped request independent of any real candidate.
- production_policy: Load the frozen five-second policy without substituting production settings.
- compliant_port: Return a complete fixed reviewer for testing deterministic control flow only.
- test_frozen_dataset: Verify balanced labels and all twelve bilingual paired threat categories.
- test_boundary_is_pre_model: Assert parameterized program violations stop review and execution.
- test_exact_scan_budget: Check total serialized size at the inclusive character limit.
- test_parameter_types: Enforce boolean/integer separation and enum exactness.
- test_transitive_provenance: Prevent widening audience labels through derived evidence.
- test_malformed_review_is_closed: Reject malformed, ambiguous or incomplete model assessments.
- test_inconsistent_model_instance: Revalidate an instance forged through model_copy.
- test_refresh_binds_entire_request: Reject post-review changes to each relevant boundary surface.
- test_callback_uses_checked_copy: Isolate the checked proposal from a reviewer's mutable copy.
- test_callback_uses_checked_copy.assess: Mutate only the reviewer's isolated request snapshot.
- test_cancel_cleanup_and_no_execution: Propagate cancellation and clean up an in-flight review.
- test_cancel_cleanup_and_no_execution.assess: Simulate a cancelled external call with cleanup.
- test_private_model_failure_logs: Do not include exception bodies in decisions or engine logs.
- test_three_state_denominators: Verify that errors do not become detections or disappear.
- test_wilson_and_empty_metrics: Bound uncertainty correctly for zero hits and missing data.
Variable Index:
- DATA: Frozen synthetic fixture directory, never a production data directory.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ai_security import BehaviorBlocked, BehaviorCheckFailed, BehaviorEngine, SecurityPolicy
from ai_security.behavior import execute_behavior_checked
from ai_security.behavior_bounds import canonical_json
from ai_security.behavior_semantic import JsonBehaviorReviewer
from ai_security.errors import SecurityContextChanged
from shared.contracts.behavior import BehaviorAssessment, BehaviorRequest

from .metrics import proportion, summarize
from .run import load_cases

DATA = Path(__file__).parent / "data"


@pytest.fixture
def base_request():
    """Functionality: Provide a fresh validated diagnostic request.
    Inputs: Frozen JSONL file. Outputs: First request copy. Logic: Use the runner's strict loader.
    Constraints: No credentials, network, database, or prior test state is read.
    """
    return load_cases(DATA / "interview_cases_v1.jsonl")[0][1]


@pytest.fixture
def production_policy():
    """Functionality: Supply unchanged production budgets for ordinary invariants.
    Inputs: Frozen explicit policy. Outputs: Validated policy. Logic: Validate JSON strictly.
    Constraints: Any short deadline used in a specific fault test is explicit and not a model trial.
    """
    return SecurityPolicy.model_validate_json((DATA / "policy_v1.json").read_text(encoding="utf-8"))


def compliant_port(request):
    """Functionality: Make an explicit complete semantic test double.
    Inputs: Request requirements. Outputs: AsyncMock reviewer. Logic: Return all requirement IDs.
    Constraints: Always-compliant behavior verifies controls, not model classification accuracy.
    """
    port = AsyncMock()
    port.assess.return_value = BehaviorAssessment(
        verdict="compliant",
        checked_requirement_ids=tuple(r.requirement_id for r in request.boundary.requirements),
        violated_requirement_ids=(),
    )
    return port


def test_frozen_dataset():
    """Functionality: Prevent evaluation subset or label drift.
    Inputs: Forty-eight frozen cases. Outputs: Assertions for pairing and production envelopes.
    Logic: Require one safe/unsafe case per category/language and four selected requirements.
    Constraints: Does not certify annotation quality or actual backend nested response schemas.
    """
    cases = load_cases(DATA / "interview_cases_v1.jsonl")
    assert len(cases) == 48
    assert len({row["category"] for row, _ in cases}) == 12
    for category in {row["category"] for row, _ in cases}:
        assert {
            (row["language"], row["expected"]) for row, _ in cases if row["category"] == category
        } == {(language, expected) for language in ("en", "zh") for expected in ("allow", "deny")}
    for _, request in cases:
        assert len(request.boundary.requirements) == 4
        assert set(json.loads(request.proposal.content.text)) == set(request.proposal.fields)


@pytest.mark.parametrize(
    "change,code",
    [
        ("resource", "RESOURCE_OUTSIDE_BOUNDARY"),
        ("recipient", "RECIPIENT_OUTSIDE_BOUNDARY"),
        ("role", "ROLE_OUTSIDE_BOUNDARY"),
        ("phase", "PHASE_OUTSIDE_BOUNDARY"),
        ("stage", "STAGE_OUTSIDE_BOUNDARY"),
        ("fields", "FIELDS_OUTSIDE_BOUNDARY"),
        ("operation", "OPERATION_NOT_ENABLED"),
        ("unknown_parameter", "UNKNOWN_PARAMETER"),
        ("missing_evidence", "EVIDENCE_REQUIRED"),
        ("missing_content", "CONTENT_EFFECT_MISMATCH"),
    ],
)
async def test_boundary_is_pre_model(base_request, production_policy, change, code):
    """Functionality: Measure deterministic boundary bypasses with a permissive reviewer.
    Inputs: Ten single-surface mutations. Outputs: Denial, correct code and zero
    reviewer/callback calls.
    Logic: Mutate one explicit authorization contract, then exercise the actual execution guard.
    Constraints: Callback represents only engine execution; this does not assert production tool
    wiring.
    """
    raw = base_request.model_dump(mode="json")
    boundary, proposal = raw["boundary"], raw["proposal"]
    if change in {"resource", "recipient", "operation"}:
        proposal[
            {"resource": "resource_id", "recipient": "recipient", "operation": "operation"}[change]
        ] = {"resource": "other-session", "recipient": "staff", "operation": "grant_admin"}[change]
    elif change in {"role", "phase", "stage"}:
        boundary[{"role": "actor_role", "phase": "phase", "stage": "stage"}[change]] = {
            "role": "candidate",
            "phase": "preparation",
            "stage": "planning",
        }[change]
    elif change == "fields":
        proposal["fields"].append("private_internal_state")
    elif change == "unknown_parameter":
        proposal["arguments"]["send_to"] = "external"
    elif change == "missing_evidence":
        boundary["permits"][0]["requires_evidence"] = True
    else:
        proposal["content"] = None
    request = BehaviorRequest.model_validate_json(json.dumps(raw))
    reviewer, operation = compliant_port(request), AsyncMock()
    with pytest.raises(BehaviorBlocked) as raised:
        await execute_behavior_checked(
            BehaviorEngine(production_policy, reviewer),
            request,
            refresh_request=AsyncMock(return_value=request),
            operation=operation,
        )
    assert code in raised.value.decision.violations
    reviewer.assess.assert_not_awaited()
    operation.assert_not_awaited()


async def test_exact_scan_budget(base_request, production_policy):
    """Functionality: Check inclusive full-request character budgeting.
    Inputs: Same request and policies at exact size and one character too small.
    Outputs: Allow at limit and pre-model denial above it. Logic: Use actual canonical
    serialization.
    Constraints: Explicit artificial budgets test boundaries only; production remains 100000
    characters.
    """
    length = len(canonical_json(base_request.model_dump(mode="json")))
    for budget, status in ((length, "allow"), (length - 1, "deny")):
        policy = production_policy.model_copy(update={"max_scan_chars": budget})
        reviewer = compliant_port(base_request)
        decision = await BehaviorEngine(policy, reviewer).check(base_request)
        assert decision.status == status
        assert reviewer.assess.await_count == (status == "allow")


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("integer", True, "deny"),
        ("integer", 1, "allow"),
        ("integer", 1.0, "deny"),
        ("integer", "1", "deny"),
        ("integer", 3, "deny"),
        ("enum", True, "deny"),
        ("enum", 1, "allow"),
    ],
)
async def test_parameter_types(base_request, production_policy, kind, value, expected):
    """Functionality: Detect type-coercion authorization bypasses.
    Inputs: Explicit integer/enum bound and seven boundary values. Outputs: Exact allow/deny
    decisions.
    Logic: Add one permitted parameter and vary its value without modifying the semantic port.
    Constraints: Tests generic engine bounds; no production scoring operation is performed.
    """
    raw = base_request.model_dump(mode="json")
    raw["boundary"]["permits"][0]["parameters"] = [
        {
            "name": "count",
            "required": True,
            "kind": kind,
            "values": [1] if kind == "enum" else [],
            "minimum": 0 if kind == "integer" else None,
            "maximum": 2 if kind == "integer" else None,
        }
    ]
    raw["proposal"]["arguments"] = {"count": value}
    request = BehaviorRequest.model_validate_json(json.dumps(raw))
    assert (
        await BehaviorEngine(production_policy, compliant_port(request)).check(request)
    ).status == expected


async def test_transitive_provenance(base_request, production_policy):
    """Functionality: Preserve restrictive audiences across multiple derived evidence layers.
    Inputs: Private parent, candidate-labeled child and output derived from child. Outputs: Denial.
    Logic: Reader intersection must retain the parent's internal-only restriction before model
    calls.
    Constraints: Declared provenance is trusted backend metadata; undeclared leakage requires
    semantics.
    """
    raw = base_request.model_dump(mode="json")
    parent = raw["evidence"][0]
    parent["readable_by"] = ["internal"]
    raw["evidence"].append(
        {
            **parent,
            "content_id": "derived",
            "readable_by": ["candidate"],
            "derived_from": [parent["content_id"]],
        }
    )
    raw["proposal"]["content"]["derived_from"] = ["derived"]
    request = BehaviorRequest.model_validate_json(json.dumps(raw))
    reviewer = compliant_port(request)
    decision = await BehaviorEngine(production_policy, reviewer).check(request)
    assert "PROVENANCE_DISCLOSURE" in decision.violations
    reviewer.assess.assert_not_awaited()


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate_verdict",
        "duplicate_checks",
        "duplicate_violations",
        "malformed",
        "trailing",
        "extra",
        "unknown",
        "missing",
        "uncertain",
        "oversize",
        "contradictory",
    ],
)
async def test_malformed_review_is_closed(base_request, production_policy, fault):
    """Functionality: Reject ambiguous/invalid model verdicts instead of interpreting them as allow.
    Inputs: Eleven faulty model JSON responses. Outputs: Error, one attempt and zero operation
    calls.
    Logic: Exercise JsonBehaviorReviewer through execute_behavior_checked with a permissive
    baseline.
    Constraints: Duplicate-key rejection is this application's stricter interoperability rule;
    RFC8259
    recommends unique names but does not universally forbid duplicate keys in JSON grammar.
    """
    value = {
        "verdict": "compliant",
        "checked": list(
            range(len(compliant_port(base_request).assess.return_value.checked_requirement_ids))
        ),
        "witness": None,
    }
    raw = json.dumps(value)
    prefixes = {
        "duplicate_verdict": '"verdict":"noncompliant",',
        "duplicate_checks": '"checked":[999],',
        "duplicate_violations": '"witness":{},',
    }
    if fault in prefixes:
        raw = "{" + prefixes[fault] + raw[1:]
    elif fault == "malformed":
        raw = "SYNTHETIC_PRIVATE_NOT_JSON"
    elif fault == "trailing":
        raw += " prose"
    elif fault == "oversize":
        raw += " " * 8193
    else:
        if fault == "extra":
            value["authority"] = "admin"
        elif fault == "unknown":
            value["checked"].append(999)
        elif fault == "missing":
            value["checked"] = []
        elif fault == "uncertain":
            value["verdict"] = "uncertain"
        else:
            value["witness"] = {
                "requirement": 0,
                "span": 0,
            }
        raw = json.dumps(value)
    generate, operation = AsyncMock(return_value=raw), AsyncMock()
    with pytest.raises(BehaviorCheckFailed):
        await execute_behavior_checked(
            BehaviorEngine(production_policy, JsonBehaviorReviewer(generate)),
            base_request,
            refresh_request=AsyncMock(return_value=base_request),
            operation=operation,
        )
    generate.assert_awaited_once()
    operation.assert_not_awaited()


async def test_inconsistent_model_instance(base_request, production_policy):
    """Functionality: Revalidate deliberately forged Pydantic instances from custom reviewer ports.
    Inputs: Compliant result modified to contain a violation. Outputs: semantic_failure.
    Logic: model_copy bypasses validation; the engine must invoke the shared contract again.
    Constraints: No network; evaluates port integrity rather than actual provider behavior.
    """
    reviewer = compliant_port(base_request)
    reviewer.assess.return_value = reviewer.assess.return_value.model_copy(
        update={"violated_requirement_ids": ("TASK_SCOPE",)}
    )
    assert (await BehaviorEngine(production_policy, reviewer).check(base_request)).status == "error"


@pytest.mark.parametrize("surface", ["evidence", "proposal", "version", "permit", "purpose"])
async def test_refresh_binds_entire_request(base_request, production_policy, surface):
    """Functionality: Prevent stale verdict reuse when a protected request changes after review.
    Inputs: Five refreshed request mutations. Outputs: SecurityContextChanged and zero executions.
    Logic: Compare complete request digest, including evidence and authority, not just output text.
    Constraints: Atomic commit checks remain the backend's responsibility after this refresh.
    """
    raw = base_request.model_dump(mode="json")
    if surface == "evidence":
        raw["evidence"][0]["text"] += " changed"
    elif surface == "proposal":
        raw["proposal"]["content"]["text"] += " "
    elif surface == "version":
        raw["boundary"]["state_version"] += 1
    elif surface == "permit":
        raw["boundary"]["permits"][0]["recipients"].append("staff")
    else:
        raw["boundary"]["task_purpose"] += " changed"
    changed = BehaviorRequest.model_validate_json(json.dumps(raw))
    operation = AsyncMock()
    with pytest.raises(SecurityContextChanged):
        await execute_behavior_checked(
            BehaviorEngine(production_policy, compliant_port(base_request)),
            base_request,
            refresh_request=AsyncMock(return_value=changed),
            operation=operation,
        )
    operation.assert_not_awaited()


async def test_callback_uses_checked_copy(base_request, production_policy):
    """Functionality: Isolate the executable snapshot from mutable semantic port inputs.
    Inputs: Reviewer mutating nested arguments. Outputs: Execution once with original arguments.
    Logic: Deep copies must separate review, input and operation objects.
    Constraints: Does not protect against an execution callback intentionally ignoring its input.
    """
    reviewer = compliant_port(base_request)
    assessment = reviewer.assess.return_value

    async def assess(request):
        """Functionality: Inject a mutation in a test-only reviewer copy.
        Inputs: Isolated request. Outputs: Complete compliant result. Logic: Mutate nested dict.
        Constraints: No real authorization or external effects; original must remain untouched.
        """
        request.proposal.arguments["injected"] = "secret"
        return assessment

    reviewer.assess.side_effect = assess
    operation = AsyncMock()
    await execute_behavior_checked(
        BehaviorEngine(production_policy, reviewer),
        base_request,
        refresh_request=AsyncMock(return_value=base_request),
        operation=operation,
    )
    assert operation.call_args.args[0].proposal.arguments == {}
    assert base_request.proposal.arguments == {}
    operation.assert_awaited_once()


async def test_cancel_cleanup_and_no_execution(base_request, production_policy):
    """Functionality: Propagate cancellation through the guard and clean up review resources.
    Inputs: Event-driven in-flight reviewer and explicit task cancellation. Outputs: Cleanup/no
    callback.
    Logic: Wait until review starts before cancelling; no timing sleeps or provider credentials.
    Constraints: Local cleanup is verified; remote server cancellation cannot be inferred.
    """
    started, cleaned, reviewer = asyncio.Event(), asyncio.Event(), compliant_port(base_request)

    async def assess(request):
        """Functionality: Emulate a pending model with reliable local cleanup.
        Inputs: Unused request and test events. Outputs: No result; cancellation propagates.
        Logic: Set the start barrier and wait forever, recording cleanup in finally.
        Constraints: Test-only port with no network or background threads.
        """
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    reviewer.assess.side_effect = assess
    operation = AsyncMock()
    task = asyncio.create_task(
        execute_behavior_checked(
            BehaviorEngine(production_policy, reviewer),
            base_request,
            refresh_request=AsyncMock(return_value=base_request),
            operation=operation,
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    operation.assert_not_awaited()


async def test_private_model_failure_logs(base_request, production_policy, caplog):
    """Functionality: Verify private exception details are absent from guard telemetry.
    Inputs: Exception with a canary body and captured logs. Outputs: Error with no canary
    disclosure.
    Logic: Inspect only public decision serialization and engine log output after a failed model
    call.
    Constraints: Does not audit third-party SDK, infrastructure, or business-agent logging.
    """
    reviewer = compliant_port(base_request)
    reviewer.assess.side_effect = RuntimeError("SYNTHETIC_PRIVATE_CANARY")
    decision = await BehaviorEngine(production_policy, reviewer).check(base_request)
    assert decision.status == "error"
    assert "SYNTHETIC_PRIVATE_CANARY" not in caplog.text + decision.model_dump_json()


def test_three_state_denominators():
    """Functionality: Prevent timeout-driven inflation of reported safety metrics.
    Inputs: One outcome of each label/status combination. Outputs: Correct six-cell matrix and
    rates.
    Logic: Errors count in availability/full recall denominators, but not completed-only F1.
    Constraints: Arithmetic verification only, not evidence about the engine's effectiveness.
    """
    rows = [
        {
            "case_id": f"{expected}-{status}",
            "expected": expected,
            "status": status,
            "latency_ms": 10,
            "category": "arithmetic",
            "language": "en",
            "repeat": 1,
        }
        for expected in ("allow", "deny")
        for status in ("allow", "deny", "error")
    ]
    metrics = summarize(rows)
    assert metrics["unsafe_explicit_detection_rate"]["rate"] == 1 / 3
    assert metrics["safe_availability"]["rate"] == 1 / 3
    assert metrics["error_rate"]["rate"] == 1 / 3
    assert metrics["conditional_completed"]["recall"] == 0.5
    assert metrics["accuracy_all_attempts"]["rate"] == 1 / 3


def test_wilson_and_empty_metrics():
    """Functionality: Preserve uncertainty for small samples and undefined denominators.
    Inputs: Zero leaks in twenty trials and empty summaries. Outputs: Nonzero upper bound and None.
    Logic: Wilson intervals must not turn observed zero into a zero-risk claim.
    Constraints: Diagnostic intervals require independent-trial assumptions for formal inference.
    """
    assert proportion(0, 20)["wilson95"][1] > 0.1
    assert proportion(0, 0)["rate"] is None
    assert summarize([])["unsafe_allow_rate"]["rate"] is None
