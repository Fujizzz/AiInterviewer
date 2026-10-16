"""Opt-in paid paired replay. --before-dir is an explicitly saved pre-edit snapshot."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def snapshot_module(directory, filename, module_name, actual_file):
    namespace = {"__name__": module_name, "__file__": str(ROOT / actual_file)}
    exec((directory / filename).read_text(encoding="utf-8"), namespace)
    return namespace


async def run(before_dir, after_only=False):
    from agents.domain.models import InterviewContext
    from agents.model_calls import current_model_call, run_model_call, safe_error_details
    from agents.question.compact import prepared_use_payload
    from agents.question.prepared import PreparedUseDecision, review_version
    from agents.tracing import trace_sink
    from app.adapters.assessment import RealtimeDecisionAdapter
    from app.adapters.decision import DetailedAnswerDecision
    from app.providers.llm import OpenAILLM
    from app.settings import interview_settings
    from shared.contracts import CandidateAnswer, EvaluationRequest, PlannedQuestion
    from shared.contracts.prepared import PreparedQuestion
    from tests.agent.mocks import InMemoryRepository

    output = ROOT / "output" / ("compact_analysis_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir(parents=True)
    source = ROOT / "output/lightweight_review_20261015T125445Z"
    recorded = ROOT / "output/scoped_boundary_20261015T151057Z"
    original = InterviewContext.model_validate_json(
        (source / "initial_context.json").read_text("utf-8")
    )
    first = PlannedQuestion.model_validate_json((source / "question_1.json").read_text("utf-8"))
    after_first = InterviewContext.model_validate_json(
        (recorded / "context_1.json").read_text("utf-8")
    )
    previous = json.loads((recorded / "result.json").read_text("utf-8"))
    roi = PlannedQuestion.model_validate(previous["turns"][0]["next_question"])
    corrected = after_first.model_copy(deep=True)
    old = corrected.question_history[0].model_copy(deep=True)
    old.question = roi.model_copy(update={"question_id": str(uuid4()), "thread_id": "old-roi"})
    old.answer = old.answer.model_copy(
        update={
            "answer_id": str(uuid4()),
            "question_id": old.question.question_id,
            "text": "All frames in the same temporal window share one union ROI.",
        }
    )
    corrected.question_history.append(old)
    cases = [
        ("mixed_boundary", original, first, (source / "answer_1.txt").read_text("utf-8")),
        (
            "roi_validation",
            after_first,
            roi,
            "I take the union of detected boxes in each temporal window, expand by a fixed margin, "
            "and clamp it to frame bounds. All frames in that window use this same ROI. "
            "I checked moving and edge-crossing clips against full-frame processing, inspected "
            "boundary seams and timestamp alignment. "
            "I do not have measured speedup or quality scores.",
        ),
        (
            "roi_conflict",
            corrected,
            roi,
            "Each frame in that same temporal window uses its own independent tight ROI. "
            "The coordinates can differ frame by frame; no single union ROI is shared.",
        ),
    ]
    saved = snapshot_module(
        before_dir, "assessment.py", "before_assessment", "app/adapters/assessment.py"
    )
    saved["CompactAnswerDecision"] = DetailedAnswerDecision
    baseline_type = saved["RealtimeDecisionAdapter"]
    old_prompt = (before_dir / "answer_decision_v2.md").read_text("utf-8")
    provider = OpenAILLM()
    report = {
        "model": provider.model,
        "requests": [],
        "cases": [],
        "scope": "Three paired foreground analyses and one paired cache check. Identical snapshots "
        "within each pair; no grading, security, generation or complete interview.",
    }
    if after_only:
        report["scope"] = (
            "Three current-version foreground analyses and one cache check; "
            "same fixed cases as the baseline. No grading, security, generation or full interview."
        )
    active = {}
    actual = provider.client.chat.completions.create

    def observed(**kwargs):
        if len(report["requests"]) >= 10:
            raise RuntimeError("Paired smoke request limit")
        call = current_model_call.get()
        row = {**active, "operation": call.operation if call else "other"}
        report["requests"].append(row)
        messages = kwargs["messages"]
        row["input_chars"] = sum(len(m["content"]) for m in messages)
        (output / f"input_{len(report['requests'])}.json").write_text(
            json.dumps(messages, ensure_ascii=False, indent=2), "utf-8"
        )
        started = perf_counter()
        try:
            result = actual(**kwargs)
            row.update(
                status="success",
                usage=result.usage.model_dump() if result.usage else None,
                response=result.choices[0].message.content if result.choices else None,
            )
            return result
        except Exception as error:
            row.update(status="failed", error=safe_error_details(error))
            raise
        finally:
            row["seconds"] = round(perf_counter() - started, 3)
            print(json.dumps({k: v for k, v in row.items() if k not in {"response"}}), flush=True)

    def trace(event, data):
        with (output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, **active, **data}, default=str) + "\n")

    token = trace_sink.set(trace)
    try:
        with patch.object(provider.client.chat.completions, "create", side_effect=observed):
            for i, (name, context, question, text) in enumerate(cases):
                answer = CandidateAnswer(
                    interview_id=context.interview_id,
                    question_id=question.question_id,
                    answer_id=str(uuid4()),
                    text=text,
                )
                request = EvaluationRequest(
                    request_id=str(uuid4()),
                    interview_id=context.interview_id,
                    question=question,
                    answer=answer,
                )
                # Alternate order so all new calls do not systematically run second.
                for variant in ["before", "after"] if i % 2 == 0 else ["after", "before"]:
                    if after_only and variant == "before":
                        continue
                    active.clear()
                    active.update(case=name, variant=variant)
                    repo = InMemoryRepository()
                    await repo.initialize_interview(context.model_copy(deep=True))
                    adapter = (
                        baseline_type(provider, repo)
                        if variant == "before"
                        else RealtimeDecisionAdapter(provider, repo)
                    )
                    if variant == "before":
                        adapter._prompt = lambda: old_prompt
                    started = perf_counter()
                    feedback = await adapter.evaluate(request)
                    report["cases"].append(
                        {
                            **active,
                            "seconds": round(perf_counter() - started, 3),
                            "feedback": feedback.model_dump(mode="json"),
                        }
                    )

            item = PreparedQuestion(
                cache_id=str(uuid4()),
                project_id=roi.project_id,
                topic_key=roi.topic_key,
                topic=roi.topic,
                text=roi.text,
                information_goal=roi.information_goal,
                difficulty=roi.difficulty,
                question_type=roi.question_type,
                review_version=review_version(),
                kind="opening",
            )
            before_prepared = snapshot_module(
                before_dir, "prepared.py", "before_prepared", "agents/question/prepared.py"
            )
            captured = {}

            class Capture:
                async def generate_structured(self, **kwargs):
                    captured.update(kwargs)
                    return kwargs["response_model"](usable=True, reason="OK")

            await before_prepared["admit_prepared"](
                item, roi, after_first, Capture(), interview_settings()
            )
            if not captured:
                raise AssertionError("Baseline cache check was not triggered")
            pairs = [
                (
                    "before",
                    (before_dir / "prepared_question_use_v1.md").read_text("utf-8"),
                    captured["payload"],
                    before_prepared["PreparedUseDecision"],
                ),
                (
                    "after",
                    (ROOT / "agents/prompts/prepared_question_use_v1.md").read_text("utf-8"),
                    prepared_use_payload(item, roi, after_first),
                    PreparedUseDecision,
                ),
            ]
            for variant, prompt, payload, schema in pairs:
                if after_only and variant == "before":
                    continue
                active.clear()
                active.update(case="cache_admission", variant=variant)
                started = perf_counter()
                try:
                    verdict = await run_model_call(
                        lambda p=prompt, d=payload, s=schema: asyncio.to_thread(provider, p, d, s),
                        operation="prepared_question_use",
                        timeout_seconds=10,
                    )
                    report["cases"].append(
                        {
                            **active,
                            "seconds": round(perf_counter() - started, 3),
                            "verdict": verdict.model_dump(),
                        }
                    )
                except Exception as error:
                    report["cases"].append({**active, "error": safe_error_details(error)})
    finally:
        trace_sink.reset(token)
        provider.client.close()
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )
        print(json.dumps({"output": str(output)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-dir", type=Path, required=True)
    parser.add_argument("--after-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.before_dir, args.after_only))
