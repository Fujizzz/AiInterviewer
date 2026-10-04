"""Responsibilities: Evaluate the security engine with explicitly sourced, frozen requests.
Implementation: Run ordered single-shot checks; persist metadata, every outcome, and three-state
metrics.
Related Modules: metrics defines denominators; ai_security supplies the only evaluated
implementation.

Declaration Index:
- file_digest: Hash file bytes for experiment provenance.
- load_cases: Validate frozen requests and labels before any network call.
- load_provenance: Verify public protocol and dataset hash before any network call.
- evaluate: Run all declared repetitions with the project reviewer and unchanged budgets.
- check_one: Record one check, retaining explicit errors and measuring unqueued wall time.
- main: Parse explicit CLI choices and launch an exclusively created result directory.
Variable Index:
- ROOT: Repository root used for source hashes and default dataset paths.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from ai_security import BehaviorEngine, SecurityPolicy
from ai_security.behavior_semantic import BEHAVIOR_INSTRUCTIONS, create_behavior_reviewer
from shared.contracts.behavior import BehaviorRequest

from .metrics import summarize

ROOT = Path(__file__).resolve().parents[1]


def file_digest(path):
    """Functionality: Compute a SHA256 provenance hash.
    Inputs: Existing file path. Outputs: Hex digest. Logic: Hash raw bytes without normalization.
    Constraints: Files are read only; missing files raise rather than silently omitting provenance.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(path):
    """Functionality: Validate all test cases before starting billed API calls.
    Inputs: Frozen UTF-8 JSONL. Outputs: Ordered dictionaries and strict request snapshots.
    Logic: Enforce unique IDs, explicit binary labels, metadata and shared contracts.
    Constraints: Labels and rationale are never forwarded to the model; cases are not filtered by
    prior outcomes and malformed data terminates the run rather than reducing its denominator.
    """
    result, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["id"] in seen or row["expected"] not in {"allow", "deny"}:
            raise ValueError("duplicate case ID or invalid label")
        for field in ("category", "language", "annotation_reason"):
            if not isinstance(row[field], str) or not row[field]:
                raise ValueError("missing case metadata")
        seen.add(row["id"])
        result.append((row, BehaviorRequest.model_validate_json(json.dumps(row["request"]))))
    if not result:
        raise ValueError("empty evaluation dataset")
    return result


async def evaluate(args):
    """Functionality: Execute and record a bounded, reproducible online diagnostic experiment.
    Inputs: CLI namespace with frozen data/policy, optional provenance, declared run conditions.
    Outputs: Manifest, prompt snapshot, append-only attempt JSONL and aggregate report.
    Logic: Repeats are declared ahead of time; a semaphore admits ordered submissions, while
    append-only records follow completion order. Failures remain in the matrix and token totals.
    Constraints: Only the configured safety reviewer is called, with frozen public/synthetic data.
    No business
    agent, tool or candidate database is accessed. Costs of cancelled calls may be unreported.
    """
    cases = load_cases(args.dataset)
    provenance = load_provenance(args.provenance, args.dataset) if args.provenance else None
    policy = SecurityPolicy.model_validate_json(args.policy.read_text(encoding="utf-8"))
    if provenance:
        conditions = provenance["conditions"]
        if (
            file_digest(args.policy) != provenance["policy_sha256"]
            or args.repeats != conditions["repeats"]
            or args.concurrency != conditions["concurrency"]
            or policy.semantic_timeout_seconds != conditions["semantic_timeout_seconds"]
            or policy.max_scan_chars != conditions["max_scan_chars"]
        ):
            raise ValueError("run conditions differ from the frozen public protocol")
    reviewer = create_behavior_reviewer()
    engine = BehaviorEngine(policy, reviewer)
    args.output.mkdir(parents=True, exist_ok=False)
    source_paths = (
        sorted((ROOT / "ai_security").glob("*.py"))
        + sorted((ROOT / "shared/contracts").glob("*security*.py"))
        + [
            ROOT / "shared/contracts/behavior.py",
            ROOT / "backend/interviews/agent_safety.py",
            ROOT / "backend/interviews/agent_socket.py",
            Path(__file__),
            Path(__file__).with_name("metrics.py"),
        ]
    )
    manifest = {
        "started_utc": datetime.now(UTC).isoformat(),
        "timezone_user": "Asia/Shanghai",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dataset": str(args.dataset.relative_to(ROOT)),
        "dataset_sha256": file_digest(args.dataset),
        "policy": policy.model_dump(mode="json"),
        "policy_sha256": file_digest(args.policy),
        "instructions_sha256": hashlib.sha256(BEHAVIOR_INSTRUCTIONS.encode()).hexdigest(),
        "source_sha256": {str(p.relative_to(ROOT)): file_digest(p) for p in source_paths},
        "reviewer": reviewer.metadata(),
        "cases": len(cases),
        "repeats": args.repeats,
        "concurrency": args.concurrency,
        "order": "tasks submitted in fixture order; semaphore admits in order; repeats sequential",
        "labels": provenance["labels"]
        if provenance
        else "author-reviewed synthetic diagnostics; no independent adjudication or holdout claim",
        "scope": "fixed-proposal guard evaluation; not end-to-end agent attack success",
        "provenance": provenance,
        "provenance_sha256": file_digest(args.provenance) if args.provenance else None,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (args.output / "instructions.txt").write_text(BEHAVIOR_INSTRUCTIONS, encoding="utf-8")
    if args.provenance:
        (args.output / "provenance.json").write_bytes(args.provenance.read_bytes())
    source_directory = args.output / "source"
    source_directory.mkdir()
    for path in source_paths:
        destination = (source_directory / path.relative_to(ROOT)).with_name(path.name + ".txt")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
    rows, semaphore = [], asyncio.Semaphore(args.concurrency)
    started = perf_counter()
    pending = []
    try:
        with (args.output / "attempts.jsonl").open("x", encoding="utf-8") as output:
            for repeat in range(1, args.repeats + 1):
                pending = []
                for case, request in cases:
                    pending.append(
                        asyncio.create_task(check_one(engine, semaphore, case, request, repeat))
                    )
                for future in asyncio.as_completed(pending):
                    row = await future
                    rows.append(row)
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")
                    output.flush()
                print(
                    f"repeat={repeat} attempts={len(rows)} errors="
                    f"{sum(r['status'] == 'error' for r in rows)}",
                    flush=True,
                )
    finally:
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await reviewer.aclose()
    metadata = reviewer.metadata()
    usage = [call["usage"] for call in metadata["calls"] if call.get("usage")]
    report = {
        "finished_utc": datetime.now(UTC).isoformat(),
        "wall_seconds": perf_counter() - started,
        "metrics": summarize(rows),
        "model": metadata,
        "usage": {
            "calls": len(metadata["calls"]),
            "calls_with_usage": len(usage),
            "input_tokens": sum(u["input_tokens"] for u in usage),
            "output_tokens": sum(u["output_tokens"] for u in usage),
            "total_tokens": sum(u["total_tokens"] for u in usage),
            "warning": "Cancelled/failed remote calls may still incur unknown billed tokens.",
        },
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: report["metrics"][k]
                for k in (
                    "confusion_three_state",
                    "unsafe_allow_rate",
                    "safe_availability",
                    "error_rate",
                    "latency_ms",
                    "repeated_case_disagreement",
                )
            }
        ),
        flush=True,
    )


async def check_one(engine, semaphore, case, request, repeat):
    """Functionality: Record one model check without retrying or dropping failures.
    Inputs: Engine, concurrency semaphore, labeled case, immutable request and repeat number.
    Outputs: Metadata and three-state decision; unexpected exceptions become explicit runner errors.
    Logic: Queue time is excluded; engine time includes cancellation cleanup. Every attempt logs
    only a fixture case ID and finite result codes. Labels stay outside reviewer requests.
    Constraints: External cancellation propagates. Runner failures are visible, never treated as
    deny.
    """
    async with semaphore:
        started = perf_counter()
        try:
            decision = await engine.check(request)
            value = decision.model_dump(mode="json")
        except Exception as exc:
            logging.error(
                "Evaluation engine exception case=%s type=%s", case["id"], type(exc).__name__
            )
            value = {
                "status": "error",
                "coverage": "incomplete",
                "violations": [],
                "error_code": "runner_" + type(exc).__name__,
            }
        value.update(
            case_id=case["id"],
            expected=case["expected"],
            category=case["category"],
            language=case["language"],
            repeat=repeat,
            latency_ms=(perf_counter() - started) * 1000,
        )
        print(
            f"case={case['id']} repeat={repeat} status={value['status']} "
            f"error={value.get('error_code')} ms={value['latency_ms']:.0f}",
            flush=True,
        )
        return value


def load_provenance(path, dataset):
    """Functionality: Bind public experiment provenance to the exact evaluated file.
    Inputs: Protocol JSON and dataset Path. Outputs: Validated metadata dictionary.
    Logic: Require its dataset-name/hash entry and nonempty explicit labeling description.
    Constraints: Verification happens before billed calls; altered data raises without filtering.
    This verifies declared provenance integrity, not annotation correctness or third-party approval.
    """
    value = json.loads(path.read_text(encoding="utf-8"))
    if not value.get("labels") or value["dataset_sha256"].get(dataset.name) != file_digest(dataset):
        raise ValueError("dataset is not bound to the declared provenance")
    return value


def main():
    """Functionality: Expose explicit evaluation conditions and refuse result overwrites.
    Inputs: CLI dataset, policy, repeats, concurrency and required output path. Outputs: Process
    run.
    Logic: Validate bounded repetitions/concurrency before asynchronous evaluation.
    Constraints: Default conditions equal production; concurrency changes are separate load runs,
    not directly pooled with sequential accuracy runs. Does not alter environment configuration.
    """
    parser = argparse.ArgumentParser(description="Frozen security-engine proposal evaluation")
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "security_evaluation/data/interview_cases_v1.jsonl"
    )
    parser.add_argument(
        "--policy", type=Path, default=ROOT / "security_evaluation/data/policy_v1.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, help="Frozen protocol with exact dataset hashes")
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    args.dataset, args.policy, args.output = (
        p.resolve() for p in (args.dataset, args.policy, args.output)
    )
    asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()
