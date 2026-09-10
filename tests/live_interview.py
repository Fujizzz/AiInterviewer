"""Run an opt-in live API interview and save a clearly labeled test record."""
import argparse
import json
from pathlib import Path

from app.graph import build_graph
from app.resume import read_resume
from main import run_interview


def main():
    """Run a live interview, verify completion, and save simulated test results as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resume", type=Path)
    args = parser.parse_args()
    print("LIVE API SMOKE TEST: answers are simulated, not a real candidate assessment.")
    state = run_interview(build_graph(), read_resume(args.resume))
    assert state["interview_finished"]
    assert 3 <= len(state["question_history"]) <= 5
    output = Path("output/live_interview_test.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "test_only": True,
        "notice": "Live model outputs with simulated answers; not the resume owner's assessment.",
        "source_resume": args.resume.name,
        "candidate_profile": state["candidate_profile"], "topics": state["topics"],
        "question_history": state["question_history"],
        "interview_finished": state["interview_finished"], "final_report": state["final_report"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved test record: {output}")


if __name__ == "__main__":
    main()
