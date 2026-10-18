"""Responsibilities: Retain explicit diagnostics for observed regression model-contract failures.
Implementation: Replay declared observed IDs once with the unchanged guard, storing raw private
assessments only in local public/synthetic fixture artifacts, never in production logs.
Related Modules: behavior_semantic validates the response; suite evaluates complete datasets.
Declaration Index:
- diagnose: Capture one declared attempt per observed frozen regression response.
- diagnose.capture: Bind the original generation result to the current fixture artifact.
- main: Validate explicit case IDs and a new destination before sequential diagnosis.
Variable Index:
- HERE: Frozen optimization regression inputs.
- ROOT: Repository root.
"""

import argparse
import asyncio
from pathlib import Path

from ai_security import BehaviorEngine, SecurityPolicy
from ai_security.behavior_semantic import JsonBehaviorReviewer, create_behavior_reviewer
from security_evaluation.public.prepare import freeze
from security_evaluation.run import load_cases

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


async def diagnose(case_ids, output):
    """Functionality: Capture observed development anomalies without replacing suite results.
    Inputs: Observed regression IDs and exclusive output directory. Outputs: One raw private
    review and three-state result per case plus transport metadata. Logic: Verify known regression
    fixture selection before billing; intercept the unchanged single generation. Constraints:
    No fresh acceptance inputs, real credentials, candidate records or production logs are saved.
    This declared diagnostic repetition is not a hidden retry or a passing acceptance result.
    """
    cases = [
        case
        for name in ("native", "controls", "interview", "synthetic", "context")
        for case in load_cases(HERE / f"data/regression-{name}.jsonl")
    ]
    selected = [(row, request) for row, request in cases if row["id"] in case_ids]
    if {row["id"] for row, _ in selected} != set(case_ids):
        raise ValueError("diagnostics accept only known frozen regression IDs")
    reviewer = create_behavior_reviewer()
    transport = reviewer._transport
    records = []

    async def capture(instructions, payload, schema):
        """Functionality: Retain an explicitly requested public diagnostic response.
        Inputs: Unchanged reviewer messages/schema. Outputs: Original raw response. Logic: Await
        exactly once and retain the text in the current record. Constraints: No parsing repairs,
        retries or logging; only frozen regression fixtures can reach this diagnostic port.
        """
        raw = await transport.generate(instructions, payload, schema)
        records[-1]["raw_assessment"] = raw
        return raw

    policy = SecurityPolicy.model_validate_json(
        (ROOT / "security_evaluation/data/policy_v1.json").read_bytes()
    )
    engine = BehaviorEngine(policy, JsonBehaviorReviewer(capture))
    output.mkdir(parents=True, exist_ok=False)
    try:
        for row, request in selected:
            records.append({"case_id": row["id"], "expected": row["expected"]})
            records[-1]["decision"] = (await engine.check(request)).model_dump(mode="json")
            print(
                f"diagnostic case={row['id']} status={records[-1]['decision']['status']}",
                flush=True,
            )
    finally:
        await reviewer.aclose()
    freeze(
        output / "diagnostics.json",
        {
            "records": records,
            "model": reviewer.metadata(),
            "note": "Development only; not suite/acceptance metrics.",
        },
    )


def main():
    """Functionality: Launch declared development diagnostic attempts. Inputs: Repeated case ID
    options and exclusive output. Outputs: Local public artifacts. Logic: Validate IDs in diagnose
    before network calls. Constraints: No acceptance reruns or silent replacement of prior outcomes.
    """
    parser = argparse.ArgumentParser(description="Diagnose old public model-contract responses")
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.case_id) != len(set(args.case_id)):
        raise ValueError("duplicate diagnostic case IDs")
    asyncio.run(diagnose(args.case_id, args.output.resolve()))


if __name__ == "__main__":
    main()
