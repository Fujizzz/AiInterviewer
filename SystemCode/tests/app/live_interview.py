"""Run an opt-in live API interview and save a clearly labeled test record."""

import argparse
import json
from pathlib import Path

from app.application import build_application
from app.cli import run_interview
from app.parsing.files import read_resume


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resume", type=Path)
    args = parser.parse_args()
    print("LIVE API SMOKE TEST: results are test data, not a hiring decision.")
    state = run_interview(build_application(), read_resume(args.resume))
    assert state["interview_finished"]
    assert len(state["question_history"]) == 5
    output = Path("output/live_interview_test.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "test_only": True,
                "notice": "Live model outputs; not a formal candidate assessment.",
                "source_resume": args.resume.name,
                **state,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved test record: {output}")


if __name__ == "__main__":
    main()
