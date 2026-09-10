import argparse
import json
from pathlib import Path
import sys
import uuid

from langgraph.types import Command

from app.graph import build_graph
from app.llm import LLMError
from app.resume import read_resume


def run_interview(graph, resume_text, *, max_questions=5, max_follow_up_per_topic=2,
                  read_answer=input, write=print):
    config = {"configurable": {"thread_id": str(uuid.uuid4())},
              "recursion_limit": max(50, 8 * max_questions + 10)}
    write("AI Interviewer started.")
    result = graph.invoke({"resume_text": resume_text, "max_questions": max_questions,
                           "max_follow_up_per_topic": max_follow_up_per_topic}, config)
    while result.get("__interrupt__"):
        question = result["__interrupt__"][0].value["question"]
        write(f"\nQuestion {len(result['question_history']) + 1}:\n{question}")
        answer = read_answer("Your answer:\n> ").strip()
        while not answer:
            write("Please enter an answer (Ctrl+C to cancel).")
            answer = read_answer("Your answer:\n> ").strip()
        result = graph.invoke(Command(resume=answer), config)
    if not result.get("interview_finished") or not result.get("final_report"):
        raise RuntimeError("Interview ended without a final report.")
    report = result["final_report"]
    write(f"\nInterview finished.\nFinal score: {report['overall_score']} / 5")
    write("Strengths:")
    for item in report["strengths"]:
        write(f"- {item}")
    write("Areas to improve:")
    for item in report["weaknesses"]:
        write(f"- {item}")
    write("\nFinal report (JSON):")
    write(json.dumps(report, indent=2, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description="Minimal LangGraph technical interview")
    parser.add_argument("resume", type=Path, help="UTF-8 text or text-based PDF resume")
    parser.add_argument("--max-questions", type=int, default=5)
    parser.add_argument("--max-follow-up-per-topic", type=int, default=2)
    args = parser.parse_args()
    if args.max_questions < 1 or args.max_follow_up_per_topic < 0:
        parser.error("max-questions must be positive; max-follow-up-per-topic must be nonnegative")
    try:
        resume = read_resume(args.resume)
        run_interview(build_graph(), resume, max_questions=args.max_questions,
                      max_follow_up_per_topic=args.max_follow_up_per_topic)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nInterview cancelled. No final evaluation was produced.", file=sys.stderr)
        return 130
    except (OSError, ValueError, LLMError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
