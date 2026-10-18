"""Responsibilities: Execute declared safety-engine suites and calculate unchanged project gates.
Implementation: Run complete datasets sequentially, retaining failures and comparing provider
configuration with the original reference; gate every dataset separately without pooling failures.
Observed acceptance outputs have an explicit replay stage, without a new holdout claim.
Related Modules: run preserves attempt/source manifests; metrics defines three-state denominators.
Declaration Index:
- gates: Calculate explicit per-dataset gate outcomes from persisted three-state metrics.
- verify_model_reference: Reject provider or model-condition drift before any billed call.
- source_paths: Enumerate implementation and experiment files required for acceptance integrity.
- verify_source_freeze: Reject changed source, protocol or selection before acceptance billing.
- suite: Execute a complete named stage under fixed conditions and save all gate outcomes.
- main: Parse a declared stage and exclusive result prefix, or freeze acceptance sources.
Variable Index:
- HERE: Frozen optimization experiment directory.
- ROOT: Repository root.
"""

import argparse
import asyncio
import json
from pathlib import Path

from ai_security.project_provider import create_transport
from security_evaluation.public.prepare import digest, freeze
from security_evaluation.run import evaluate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def gates(metrics, limits):
    """Functionality: Judge observed project counts. Inputs: Report metrics and fixed gates.
    Outputs: Individual Boolean gates. Logic: Use full safe/unsafe/error denominators and P95.
    Constraints: Passing empirical gates is not statistical assurance or industry certification.
    No threshold changes, removal of errors, pooling across datasets or rounding before comparison.
    """
    matrix = metrics["confusion_three_state"]
    return {
        "unsafe_allow": matrix["deny"]["allow"] <= limits["unsafe_allow_count"],
        "safe_false_deny": metrics["safe_false_denial_rate"]["rate"]
        <= limits["safe_false_deny_rate_max"],
        "error_rate": metrics["error_rate"]["rate"] <= limits["error_rate_max"],
        "p95_latency": metrics["latency_ms"]["p95"] <= limits["p95_latency_ms_max"],
    }


def verify_model_reference(metadata):
    """Functionality: Preserve the original model experiment conditions. Inputs: Current metadata.
    Outputs: None or ValueError naming only a finite configuration key. Logic: Compare provider,
    model, options, thinking, SDK timeout/retries, endpoint digest and SDK version with the original
    reference. Constraints: No credential, URL or request text is printed; no silent substitution.
    """
    baseline = json.loads(
        (HERE.parent / "public/results/revised-native-development/manifest.json").read_bytes()
    )["reviewer"]
    for key in (
        "provider",
        "model",
        "options",
        "enable_thinking",
        "sdk_timeout_seconds",
        "sdk_max_retries",
        "endpoint_sha256",
        "sdk_version",
    ):
        if metadata[key] != baseline[key]:
            raise ValueError("model configuration differs from original reference: " + key)


async def suite(stage, prefix):
    """Functionality: Run full stage without selective retries. Inputs: Stage and new prefix.
    Outputs: Complete per-dataset records and frozen gate summary. Logic: Check original model
    fingerprint before billing; execute sequentially and evaluate declared unchanged thresholds.
    Constraints: Acceptance requires a separately frozen source record; replay reuses its already
    observed fixtures as regression only. No candidate data, tools, model substitution or fallback.
    """
    protocol_path = HERE / (
        f"protocol-{stage}.json" if stage in ("stability", "load") else "protocol-v2.json"
    )
    protocol = json.loads(protocol_path.read_bytes())
    verify_model_reference(create_transport().metadata())
    if stage == "regression":
        names = ["native", "controls", "interview", "synthetic", "context"]
        datasets = [(name, HERE / f"data/regression-{name}.jsonl", protocol_path) for name in names]
    elif stage in ("stability", "load"):
        names = ["synthetic", "context"] if stage == "stability" else ["synthetic"]
        datasets = [(name, HERE / f"data/regression-{name}.jsonl", protocol_path) for name in names]
    elif stage in ("acceptance", "replay"):
        if stage == "acceptance":
            verify_source_freeze()
        datasets = [
            (name, HERE / f"data/acceptance-{name}.jsonl", protocol_path)
            for name in ("controls", "interview")
        ]
        datasets.append(
            (
                "native",
                HERE / "results/native-targets/acceptance-native.jsonl",
                HERE / "results/native-targets/native-protocol.json",
            )
        )
    else:
        raise ValueError("unknown safety evaluation stage")
    results = []
    for name, dataset, provenance in datasets:
        if stage == "acceptance":
            verify_source_freeze()
        destination = HERE / "results" / f"{prefix}-{stage}-{name}"
        await evaluate(
            argparse.Namespace(
                dataset=dataset,
                provenance=provenance,
                policy=ROOT / "security_evaluation/data/policy_v1.json",
                output=destination,
                repeats=protocol["conditions"]["repeats"],
                concurrency=protocol["conditions"]["concurrency"],
            )
        )
        report = json.loads((destination / "report.json").read_bytes())
        checks = gates(report["metrics"], protocol["gates"])
        results.append(
            {
                "dataset": name,
                "path": str(destination.relative_to(ROOT)),
                "gates": checks,
                "passed": all(checks.values()),
                "metrics": report["metrics"],
            }
        )
    freeze(
        HERE / "results" / f"{prefix}-{stage}-gates.json",
        {
            "stage": stage,
            "limits": protocol["gates"],
            "results": results,
            "all_passed": all(row["passed"] for row in results),
        },
    )
    print(json.dumps({"stage": stage, "all_passed": all(row["passed"] for row in results)}))


def source_paths():
    """Functionality: Enumerate the complete acceptance implementation record. Inputs: Repo paths.
    Outputs: Sorted unique source paths. Logic: Include guard, strict contracts, gateway and
    evaluation modules. Constraints: No generated results, candidate data or credentials included.
    """
    return sorted(
        set(
            list((ROOT / "ai_security").glob("*.py"))
            + [
                ROOT / "shared/contracts/behavior.py",
                ROOT / "shared/contracts/security.py",
                ROOT / "backend/interviews/agent_safety.py",
                ROOT / "backend/interviews/agent_socket.py",
                HERE.parent / "run.py",
                HERE.parent / "metrics.py",
                HERE.parent / "public/prepare.py",
                HERE.parent / "public/collect.py",
            ]
            + list(HERE.glob("*.py"))
        )
    )


def verify_source_freeze():
    """Functionality: Enforce fixed implementation before acceptance calls. Inputs: Immutable
    source/protocol/data record and current files. Outputs: None or ValueError. Logic: Require
    exact file coverage and hashes for all listed implementation and input files. Constraints:
    Results/new native outputs are excluded; no approved source drift, implicit update or retry.
    """
    record = json.loads((HERE / "acceptance-source-freeze.json").read_bytes())
    current = {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in source_paths()}
    if current != record["source_sha256"]:
        raise ValueError("acceptance source changed after freeze")
    if any(
        digest((ROOT / name).read_bytes()) != expected
        for name, expected in record["input_sha256"].items()
    ):
        raise ValueError("acceptance input changed after freeze")


def main():
    """Functionality: Launch the complete declared stage. Inputs: Stage and exclusive prefix.
    Outputs: Completed suite. Logic: Resolve fixed paths and await sequential tests.
    Constraints: Existing directories raise; repetitions/retries cannot be implicitly increased.
    """
    parser = argparse.ArgumentParser(description="Unchanged safety-engine gates per dataset")
    parser.add_argument(
        "--stage",
        choices=("regression", "stability", "load", "acceptance", "replay", "freeze-source"),
        required=True,
    )
    parser.add_argument("--prefix", required=True)
    args = parser.parse_args()
    if not args.prefix.replace("-", "").isalnum():
        raise ValueError("prefix must be alphanumeric or hyphen")
    if args.stage == "freeze-source":
        inputs = [*sorted(HERE.glob("protocol-*.json")), *sorted((HERE / "data").glob("*"))]
        freeze(
            HERE / "acceptance-source-freeze.json",
            {
                "source_sha256": {
                    str(path.relative_to(ROOT)): digest(path.read_bytes())
                    for path in source_paths()
                },
                "input_sha256": {
                    str(path.relative_to(ROOT)): digest(path.read_bytes())
                    for path in inputs
                    if path.is_file()
                },
                "note": "Before guard acceptance outcomes; source drift invalidates acceptance.",
            },
        )
    else:
        asyncio.run(suite(args.stage, args.prefix))


if __name__ == "__main__":
    main()
