"""Full production interview with real providers and preset answers from the failed run.

Run: python -m docs.examples.replay_interview Wang_Shunyao_CV.pdf
Uses .env credentials and makes real API requests, including parsing and evaluation.
"""

import argparse
from pathlib import Path

from agents.tracing import emit_trace
from app.application import build_application
from app.cli import run_interview
from app.parsing.files import read_resume
from app.tracing import FileTrace

ANSWERS = [
    "I design the system",
    "I use transformer",
    "I upgrade the system",
    "ok",
    "the speed",
    "speed",
    "I optimize the handling capacity",
    "yes",
]
RECENT_ANSWERS = [
    "I design the system",
    "attention",
    "I optimize them",
    "ok",
    "the speed",
    "the user part sql",
    "yes",
    "api",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resume", type=Path)
    parser.add_argument("--answers", choices=["baseline", "recent"], default="baseline")
    args = parser.parse_args()
    answers = iter(RECENT_ANSWERS if args.answers == "recent" else ANSWERS)

    def answer(prompt):
        value = next(answers)
        print(prompt + value, flush=True)
        return value

    with FileTrace(Path("output")) as trace:
        emit_trace("replay.started")
        result = run_interview(
            build_application(),
            read_resume(args.resume),
            max_questions=8,
            max_questions_per_project=4,
            max_questions_per_topic=2,
            job_title="AI Engineer",
            read_answer=answer,
            write=lambda line: print(line, flush=True),
        )
        trace.save_result(result)
    fallback_count = sum(log["fallback_used"] for log in result["decision_logs"])
    print(f"Fallback questions: {fallback_count}/{len(result['question_history'])}", flush=True)
    print(f"Interview record: {trace.path}", flush=True)


if __name__ == "__main__":
    main()
