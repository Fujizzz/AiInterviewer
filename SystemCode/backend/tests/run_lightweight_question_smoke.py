"""Opt-in paid, short foreground check. No scoring, RAG or AI Security calls."""

import argparse
import asyncio
import json
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def run(turns=3):
    from agents.model_calls import current_model_call, safe_error_details
    from agents.orchestrator import InterviewAgentService
    from agents.question.quality import QuestionQualityGate
    from agents.tracing import trace_sink
    from app.adapters.assessment import RealtimeDecisionAdapter
    from app.adapters.llm import ProviderLLMAdapter
    from app.providers.llm import OpenAILLM
    from app.settings import interview_settings
    from shared.contracts import (
        CandidateAnswer,
        CandidateProfile,
        Competency,
        EvaluationRequest,
        InitializeInterviewRequest,
        InterviewStage,
        JobProfile,
    )
    from tests.agent.mocks import InMemoryRepository

    os.environ["AI_SECURITY_ENABLED"] = "false"
    output = (
        ROOT / "output" / ("lightweight_review_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    output.mkdir(parents=True)
    (ROOT / "tmp" / "active_lightweight_smoke.txt").write_text(str(output), encoding="utf-8")
    source = ROOT / "output/full_preparation_20261015T115133Z/result.json"
    stored = json.loads(source.read_text(encoding="utf-8"))["stored_profile"]
    profile = CandidateProfile.model_validate(stored["candidate_profile"])
    profile.projects = [p for p in profile.projects if "Mango" in p.name]
    (output / "background.md").write_text(
        "固定模拟背景：参与视频修复管线衔接，窗口内先聚合目标框，再按运动余量扩张 ROI，"
        "坐标映射回原帧后做边界与时间融合。没有可公开的上线效果数值。"
        "FFmpeg 解码、推理、编码通过有界缓冲连接，队列满时施加背压；"
        "保留时间戳、最后窗口和音视频对齐检查。TeaCache 与动态 CFG 由已有组件提供，"
        "了解接入与验证方法，不掌握私有精确公式、阈值或参数曲线。"
        "候选人只读取公开问题与固定背景，手工回答，不调用候选人模型。",
        encoding="utf-8",
    )
    provider = OpenAILLM()
    settings = interview_settings()
    report = {
        "status": "running",
        "model": provider.model,
        "provider": provider.provider,
        "scope": f"{turns} real foreground answer-to-next-question transitions; "
        "reused real CV profile; "
        "no PDF parsing, RAG, scoring, AI Security or full interview.",
        "api_requests": [],
        "turns": [],
        "quality_cases": [],
    }
    lock = threading.Lock()
    actual = provider.client.chat.completions.create

    def audited(**kwargs):
        with lock:
            if len(report["api_requests"]) >= 24:
                raise RuntimeError("Small paid-test request limit reached")
            call = current_model_call.get()
            entry = {
                "operation": call.operation if call else "other",
                "status": "running",
                "input_chars": sum(len(m.get("content", "")) for m in kwargs["messages"]),
            }
            report["api_requests"].append(entry)
        started = perf_counter()
        try:
            result = actual(**kwargs)
            entry.update(
                status="success", usage=result.usage.model_dump() if result.usage else None
            )
            return result
        except Exception as exc:
            entry.update(status="failed", error=safe_error_details(exc))
            raise
        finally:
            entry["seconds"] = round(perf_counter() - started, 3)

    def trace(event, data):
        with lock:
            with (output / "events.jsonl").open("a", encoding="utf-8") as target:
                target.write(
                    json.dumps({"event": event, **data}, ensure_ascii=False, default=str) + "\n"
                )

    repo = InMemoryRepository()
    service = InterviewAgentService(
        repository=repo,
        settings=settings,
        llm=ProviderLLMAdapter(provider),
        background_replanning=True,
    )
    evaluation = RealtimeDecisionAdapter(provider, repo)
    iid = "lightweight-" + str(uuid4())
    trace_token = trace_sink.set(trace)
    try:
        with patch.object(provider.client.chat.completions, "create", side_effect=audited):
            first = (
                await service.initialize_interview(
                    InitializeInterviewRequest(
                        interview_id=iid,
                        candidate_profile=profile,
                        duration_seconds=900,
                        job_profile=JobProfile(
                            job_id="smoke",
                            title="AI Engineer",
                            competency_importance={Competency.TECHNICAL_DEPTH: 1},
                        ),
                        enabled_stages=[InterviewStage.PROJECT_DEEP_DIVE],
                        planning_enabled=True,
                    )
                )
            ).first_action
            action = first
            initial = await repo.get_interview_context(iid)
            (output / "initial_context.json").write_text(
                initial.model_dump_json(indent=2), encoding="utf-8"
            )
            for index in range(1, turns + 1):
                if action.question is None:
                    raise RuntimeError("Interview ended before the short check completed")
                print(
                    json.dumps(
                        {
                            "answer_file": str(output / f"answer_{index}.txt"),
                            "question": action.question.text,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                (output / f"question_{index}.json").write_text(
                    action.question.model_dump_json(indent=2), encoding="utf-8"
                )
                path = output / f"answer_{index}.txt"
                while not path.exists():
                    await asyncio.sleep(0.3)
                text = path.read_text(encoding="utf-8").strip()
                answer = CandidateAnswer(
                    interview_id=iid,
                    question_id=action.question.question_id,
                    answer_id=str(uuid4()),
                    text=text,
                )
                before_calls, started = len(report["api_requests"]), perf_counter()
                feedback = await evaluation.evaluate(
                    EvaluationRequest(
                        request_id=str(uuid4()),
                        interview_id=iid,
                        question=action.question,
                        answer=answer,
                    )
                )
                analysis_seconds = perf_counter() - started
                action = await service.apply_evaluation_feedback(iid, feedback, answer=answer)
                report["turns"].append(
                    {
                        "index": index,
                        "answer_analysis_seconds": round(analysis_seconds, 3),
                        "answer_to_next_question_seconds": round(perf_counter() - started, 3),
                        "api_requests": len(report["api_requests"]) - before_calls,
                        "analysis_status": feedback.analysis_status,
                        "coverage_status": feedback.objective_coverage_status,
                        "next_question": action.question.text if action.question else None,
                        "generation_reason": action.decision_trace.details.get("generation_reason"),
                    }
                )
                print(
                    json.dumps({"completed_turn": report["turns"][-1]}, ensure_ascii=False),
                    flush=True,
                )

            # Actual reviewer checks, separate from the live interview; do not publish test drafts.
            context = await repo.get_interview_context(iid)
            (output / "final_context.json").write_text(
                context.model_dump_json(indent=2), encoding="utf-8"
            )
            cases = [
                ("known_repeat", first.question, context, True),
                (
                    "invented_result",
                    first.question.model_copy(
                        update={
                            "text": "你们上线后已经把 GPU 峰值显存降低了 80%，"
                            "这个线上实测结果是如何得到的？"
                        }
                    ),
                    initial,
                    True,
                ),
                (
                    "reasonable_method_and_reason",
                    first.question.model_copy(
                        update={
                            "text": "在 FFmpeg 流式处理管线中，你是如何连接解码与推理环节的，"
                            "为什么这样设计？",
                            "information_goal": "Explain the FFmpeg streaming pipeline design",
                            "topic": "FFmpeg streaming",
                        }
                    ),
                    initial,
                    False,
                ),
            ]
            for name, question, view, expected_block in cases:
                started = perf_counter()
                review = await QuestionQualityGate(ProviderLLMAdapter(provider), settings).review(
                    question, view
                )
                report["quality_cases"].append(
                    {
                        "name": name,
                        "seconds": round(perf_counter() - started, 3),
                        "issues": [i.code for i in review.issues],
                        "passed": bool(review.issues) == expected_block,
                    }
                )
            # One unchanged strict review of the same initial draft for actual usage comparison.
            strict = settings.model_copy(deep=True)
            strict.question_agent.quality_mode = "strict"
            before = len(report["api_requests"])
            await QuestionQualityGate(ProviderLLMAdapter(provider), strict).review(
                first.question, initial
            )
            report["strict_comparison_requests"] = report["api_requests"][before:]
            report["status"] = (
                "passed" if all(c["passed"] for c in report["quality_cases"]) else "failed"
            )
            report["final_state"] = context.state.model_dump(mode="json")
    except Exception as exc:
        report.update(status="failed", error=safe_error_details(exc))
        raise
    finally:
        await service.close_background()
        provider.client.close()
        trace_sink.reset(trace_token)
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps({"output": str(output), "status": report["status"]}, ensure_ascii=False),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, choices=(1, 2, 3), default=3)
    asyncio.run(run(parser.parse_args().turns))
