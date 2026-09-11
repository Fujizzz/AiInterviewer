"""Terminal entry point for the integrated evidence-based AI interviewer."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agents.domain.errors import AgentError
from app.application import MVPInterviewApplication, build_application
from app.llm import LLMError
from app.resume import read_resume


def run_interview(
    application: MVPInterviewApplication,
    resume_text: str,
    *,
    max_questions: int = 5,
    max_follow_up_per_topic: int = 2,
    job_title: str = "General AI / Software Engineer",
    read_answer=input,
    write=print,
):
    """Run the async application from a synchronous CLI or offline smoke test."""

    result = asyncio.run(
        application.run(
            resume_text,
            max_questions=max_questions,
            max_follow_up_per_topic=max_follow_up_per_topic,
            job_title=job_title,
            read_answer=read_answer,
            write=write,
        )
    )
    report = result["final_report"]
    score = report["overall_score"]
    score_text = f"{score:.2f} / 5" if score is not None else "insufficient evidence"
    write(f"\nInterview finished.\nEvidence-weighted score: {score_text}")
    write("Strengths:")
    for item in report["strengths"]:
        write(f"- {item}")
    write("Areas to improve or validate:")
    for item in report["weaknesses"]:
        write(f"- {item}")
    write("\nFinal report (JSON):")
    write(json.dumps(report, indent=2, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evidence-based technical interview using the deterministic local Agent core"
    )
    parser.add_argument("resume", type=Path, help="UTF-8 text or text-based PDF resume")
    parser.add_argument("--max-questions", type=int, default=5)
    parser.add_argument("--max-follow-up-per-topic", type=int, default=2)
    parser.add_argument("--job-title", default="General AI / Software Engineer")
    args = parser.parse_args()
    if args.max_questions < 1 or args.max_follow_up_per_topic < 0:
        parser.error("max-questions must be positive; max-follow-up-per-topic must be nonnegative")
    try:
        run_interview(
            build_application(),
            read_resume(args.resume),
            max_questions=args.max_questions,
            max_follow_up_per_topic=args.max_follow_up_per_topic,
            job_title=args.job_title,
        )
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nInterview cancelled. No final evaluation was produced.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, AgentError, LLMError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
