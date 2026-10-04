"""Responsibilities: Collect previously unevaluated native public outputs for frozen acceptance.
Implementation: Verify preselected source hashes; generate once per original task; label the raw
output using the previously declared independent literal oracle before any guard verdict.
Related Modules: prepare selects unused tasks; public.collect defines the narrow oracle;
security_evaluation.run evaluates the resulting immutable proposals separately.
Declaration Index:
- snapshot_sources: Preserve distinct collectors in repository-relative source hierarchies.
- collect: Freeze all target attempts and bind successful outputs/labels to a derived protocol.
- main: Parse an exclusive destination and execute sequential fixture collection.
Variable Index:
- HERE: Frozen optimization inputs directory.
- ROOT: Repository root.
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from time import perf_counter

from ai_security.project_provider import create_transport
from security_evaluation.public.collect import literal_leak
from security_evaluation.public.prepare import digest, freeze, native_case, secret

from .suite import verify_model_reference

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def snapshot_sources(output):
    """Functionality: Freeze all native-collection implementation files without filename collisions.
    Inputs: Exclusive output directory and four known repository source paths. Outputs: Source
    snapshots in repository-relative hierarchy. Logic: Preserve each complete relative path,
    including both public and optimization collectors with the same basename. Constraints: No
    model or dataset response is accessed; existing files raise instead of being overwritten.
    """
    for path in (
        Path(__file__),
        ROOT / "ai_security/project_provider.py",
        HERE.parent / "public/collect.py",
        HERE.parent / "public/prepare.py",
    ):
        relative = path.relative_to(ROOT)
        freeze(output / "source" / relative.with_name(path.name + ".txt"), path.read_bytes())


async def collect(output):
    """Functionality: Freeze one original-message target response per untouched task group.
    Inputs: Exclusive output directory, frozen protocol/selection, existing project configuration.
    Outputs: Every attempt, successful labeled requests, report and derived guard provenance.
    Logic: Verify before billing; retain failure denominators; use raw output with independent
    literal labels. Constraints: No guard consulted, target retries, translations, prompt changes
    or business writes. Target SDK timeout stays configured; guard budget remains five seconds.
    """
    protocol_path = HERE / "protocol-v2.json"
    protocol = json.loads(protocol_path.read_bytes())
    selection_path = HERE / "data/native-selection.json"
    if digest(selection_path.read_bytes()) != protocol["native_selection_sha256"]:
        raise ValueError("native selection integrity mismatch")
    for source in protocol["sources"]:
        if digest((HERE.parent / "public/raw" / source["file"]).read_bytes()) != source["sha256"]:
            raise ValueError("public source integrity mismatch")
    selection = json.loads(selection_path.read_bytes())
    transport = create_transport()
    verify_model_reference(transport.metadata())
    output.mkdir(parents=True, exist_ok=False)
    freeze(
        output / "manifest.json",
        {
            "protocol_sha256": digest(protocol_path.read_bytes()),
            "selection_sha256": protocol["native_selection_sha256"],
            "target": transport.metadata(),
            "attempts_planned": len(selection),
        },
    )
    snapshot_sources(output)
    responses, cases = [], []
    try:
        with (output / "responses.jsonl").open("x", encoding="utf-8") as stream:
            for row in selection:
                started = perf_counter()
                result = {"public_source_id": row["prompt_id"]}
                try:
                    text = await transport.generate_text(row["test_case_prompt"], row["user_input"])
                    leaked = literal_leak(text, secret(row))
                    result.update(status="received", response=text, literal_disclosure=leaked)
                    cases.append(
                        dict(
                            native_case(row, text, "deny" if leaked else "allow", "native"),
                            partition="fresh-acceptance",
                        )
                    )
                except Exception as exc:
                    result.update(status="error", exception_type=type(exc).__name__)
                    logging.error(
                        "Native acceptance fixture failed source_id=%s type=%s",
                        row["prompt_id"],
                        type(exc).__name__,
                    )
                result["latency_ms"] = (perf_counter() - started) * 1000
                responses.append(result)
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                stream.flush()
                print(f"native source_id={row['prompt_id']} status={result['status']}", flush=True)
    finally:
        await transport.aclose()
    dataset = output / "acceptance-native.jsonl"
    freeze(dataset, cases)
    freeze(
        output / "native-protocol.json",
        dict(
            protocol,
            dataset_sha256={dataset.name: digest(dataset.read_bytes())},
            parent_protocol_sha256=digest(protocol_path.read_bytes()),
            target_response_sha256=digest((output / "responses.jsonl").read_bytes()),
        ),
    )
    freeze(
        output / "report.json",
        {
            "target": transport.metadata(),
            "attempted": len(responses),
            "received": len(cases),
            "errors_unscored": len(responses) - len(cases),
            "literal_disclosures": sum(row.get("literal_disclosure", False) for row in responses),
            "limitation": (
                "Literal oracle only; encoded/indirect disclosures and official ASR unmeasured."
            ),
        },
    )


def main():
    """Functionality: Launch explicit native collection. Inputs: Required new output directory.
    Outputs: Frozen fixtures. Logic: Resolve destination and await collection once.
    Constraints: Existing results are never overwritten; no guard acceptance claim is produced.
    """
    parser = argparse.ArgumentParser(description="Freeze fresh original public target responses")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(collect(args.output.resolve()))


if __name__ == "__main__":
    main()
