"""Terminal entry point for the integrated evidence-based AI interviewer."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agents.domain.errors import AgentError
from app.application import MVPInterviewApplication, build_application
from app.parsing.files import read_resume
from app.providers.llm import LLMError
from app.tracing import FileTrace


def run_interview(
    application: MVPInterviewApplication,
    resume_text: str,
    *,
    max_questions: int = 5,
    max_follow_up_per_topic: int | None = None,
    max_questions_per_project: int | None = None,
    max_questions_per_topic: int | None = None,
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
            max_questions_per_project=max_questions_per_project,
            max_questions_per_topic=max_questions_per_topic,
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
    parser.add_argument(
        "--max-questions-per-project",
        type=int,
        help="Maximum questions per project, including follow-ups (default: config)",
    )
    topic_options = parser.add_mutually_exclusive_group()
    topic_options.add_argument(
        "--max-questions-per-topic",
        type=int,
        help="Maximum questions per topic, including its first question",
    )
    topic_options.add_argument(
        "--max-follow-up-per-topic",
        type=int,
        help="Legacy alias: topic question limit = this value + 1",
    )
    parser.add_argument("--job-title", default="General AI / Software Engineer")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "output",
        help="Directory for private execution traces (default: project output/)",
    )
    args = parser.parse_args()
    if (
        args.max_questions < 1
        or any(
            value is not None and value < 1
            for value in (args.max_questions_per_project, args.max_questions_per_topic)
        )
        or (args.max_follow_up_per_topic is not None and args.max_follow_up_per_topic < 0)
    ):
        parser.error("Question limits must be positive; legacy follow-up limit must be nonnegative")
    trace = FileTrace(args.output_dir)
    try:
        with trace:
            result = run_interview(
                build_application(),
                read_resume(args.resume),
                max_questions=args.max_questions,
                max_follow_up_per_topic=args.max_follow_up_per_topic,
                max_questions_per_project=args.max_questions_per_project,
                max_questions_per_topic=args.max_questions_per_topic,
                job_title=args.job_title,
            )
            trace.save_result(result)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nInterview cancelled. No final evaluation was produced.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, AgentError, LLMError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if trace.path.is_file():
            print(f"\nInterview record: {trace.path}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
