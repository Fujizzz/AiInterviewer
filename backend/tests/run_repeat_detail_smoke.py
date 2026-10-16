"""Small paid review-only replay of checklist, offer and concrete-answer boundaries.

Declaration Index:
- main: Load the previous CV/profile/history and persist a private smoke report.
- main.observed: Record actual request counts, usage and timing without credentials.
- main.trace: Retain review evidence and warnings for each isolated case.
- main.exercise: Review recorded and controlled examples with the real configured model.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    from agents.domain.models import InterviewContext
    from agents.model_calls import safe_error_details
    from agents.question.quality import QuestionQualityGate
    from agents.tracing import trace_sink
    from app.adapters.llm import ProviderLLMAdapter
    from app.providers.llm import OpenAILLM
    from app.settings import interview_settings

    source = ROOT / "output/live_15min_20261016T050003Z/result.json"
    previous = json.loads(source.read_text(encoding="utf-8"))
    base = InterviewContext.model_validate(previous["final_context"])
    q11 = previous["turns"][10]["question"]["question_id"]
    entry = next(e for e in base.question_history if e.question.question_id == q11)
    drafts = [
        e["draft"]
        for e in previous["traces"]
        if e["event"] == "question.quality" and e.get("turn") == 11
    ]
    cases = [
        ("recorded_q12_assertion", entry.answer.text, drafts[1], False),
        (
            "offer_only",
            "I can explain the external input-contract checks.",
            "Could you describe one specific input-alignment assertion you implemented?",
            False,
        ),
        (
            "actual_assertion",
            "I asserted mask.shape[0] == video.shape[0] before inference "
            "and rejected mismatched frame counts.",
            "Could you describe the frame-count alignment assertion you implemented?",
            True,
        ),
        (
            "chinese_checklist",
            "我检查了张量形状、数据类型、设备、时间对齐和掩码约定。",
            "请具体说明你实现过的一条时间对齐断言，以及它检查什么条件？",
            False,
        ),
        (
            "chinese_actual_assertion",
            "我在推理前断言掩码帧数等于视频帧数，不相等时拒绝执行。",
            "你实现的掩码与视频帧数对齐断言，具体检查什么条件？",
            True,
        ),
    ]
    output = ROOT / "output" / ("repeat_detail_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir(parents=True)
    client = OpenAILLM()
    report = {
        "model": client.model,
        "status": "running",
        "cases": [],
        "requests": [],
        "scope": "Five real semantic reviews with reused CV profile; one recorded Q12 "
        "draft and four controlled variants. No parsing, preparation, planning, RAG, "
        "scoring, question generation or full interview; isolated history/evidence.",
    }
    create = client.client.chat.completions.create

    def observed(**kwargs):
        if len(report["requests"]) >= len(cases):
            raise RuntimeError("Review-only smoke request cap reached")
        started = perf_counter()
        record = {"case": report["cases"][-1]["name"]}
        report["requests"].append(record)
        try:
            response = create(**kwargs)
            record["usage"] = response.usage.model_dump() if response.usage else None
            record["response"] = response.choices[0].message.content if response.choices else None
            record["status"] = "response"
            return response
        except Exception as error:
            record.update(status="failed", error=safe_error_details(error))
            raise
        finally:
            record["seconds"] = round(perf_counter() - started, 6)

    client.client.chat.completions.create = observed

    def trace(event, data):
        if event == "question.quality":
            report["cases"][-1]["review_trace"] = data

    async def exercise():
        settings = interview_settings()
        settings.question_agent.quality_mode = "relaxed"
        gate = QuestionQualityGate(ProviderLLMAdapter(client), settings)
        for name, answer, text, expected_repeat in cases:
            context = base.model_copy(deep=True)
            previous_entry = entry.model_copy(deep=True)
            previous_entry.answer.text = answer
            previous_entry.feedback.analysis.status = "substantive"
            previous_entry.feedback.analysis.limitations = []
            context.question_history = [previous_entry]
            context.topic_progress = {}  # Isolate this review from later failure quarantine.
            candidate = previous_entry.question.model_copy(
                deep=True,
                update={
                    "question_id": name,
                    "text": text,
                    "information_goal": "Describe one concrete input-validation assertion",
                },
            )
            row = {
                "name": name,
                "answer": answer,
                "question": text,
                "expected_repeat": expected_repeat,
            }
            report["cases"].append(row)
            try:
                review = await gate.review(candidate, context)
                row["issues"] = [i.code for i in review.issues]
                row["passed"] = ("SEMANTIC_REPEAT" in row["issues"]) == expected_repeat
                if not expected_repeat and row["issues"]:
                    row["passed"] = False
            except Exception as error:
                row.update(
                    passed=False,
                    error=safe_error_details(error),
                    review_errors=getattr(error, "errors", []),
                )
        report["status"] = "passed" if all(c["passed"] for c in report["cases"]) else "failed"

    token = trace_sink.set(trace)
    try:
        asyncio.run(exercise())
    finally:
        trace_sink.reset(token)
        client.client.close()
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "output": str(output),
                    "status": report["status"],
                    "cases": [
                        {"name": c["name"], "passed": c.get("passed")} for c in report["cases"]
                    ],
                },
                ensure_ascii=True,
            )
        )
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
