"""Paid two-turn replay of the recorded mixed-answer boundary. No full interview."""

import asyncio
import json
import os
import re
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, time
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def run():
    from agents.domain.models import InterviewContext
    from agents.model_calls import current_model_call, safe_error_details
    from agents.orchestrator import InterviewAgentService
    from agents.tracing import trace_sink
    from app.adapters.assessment import RealtimeDecisionAdapter
    from app.adapters.llm import ProviderLLMAdapter
    from app.providers.llm import OpenAILLM
    from app.settings import interview_settings
    from shared.contracts import CandidateAnswer, EvaluationRequest, PlannedQuestion
    from tests.agent.mocks import InMemoryRepository

    os.environ["AI_SECURITY_ENABLED"] = "false"
    source = ROOT / "output/lightweight_review_20261015T125445Z"
    output = ROOT / "output" / ("scoped_boundary_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir(parents=True)
    context = InterviewContext.model_validate_json(
        (source / "initial_context.json").read_text(encoding="utf-8")
    )
    context.state.clock_started_at = time() - context.state.elapsed_seconds
    question = PlannedQuestion.model_validate_json(
        (source / "question_1.json").read_text(encoding="utf-8")
    )
    repo = InMemoryRepository()
    context = await repo.initialize_interview(context)
    iid = context.interview_id
    repo.questions[question.question_id] = question
    repo.question_interviews[question.question_id] = iid
    repo.interview_question_ids[iid] = [question.question_id]
    provider = OpenAILLM()
    settings = interview_settings()
    service = InterviewAgentService(
        repository=repo,
        settings=settings,
        llm=ProviderLLMAdapter(provider),
        background_replanning=True,
    )
    evaluation = RealtimeDecisionAdapter(provider, repo)
    report = {
        "status": "running",
        "model": provider.model,
        "requests": [],
        "turns": [],
        "scope": "Real answer analysis, target-state update, question generation and review; "
        "reused initial context and two fixed answers from the recorded case. "
        "No resume parsing, initial planning, RAG, scoring, AI Security or full interview.",
    }
    lock = threading.Lock()
    actual = provider.client.chat.completions.create

    def observed(**kwargs):
        with lock:
            if len(report["requests"]) >= 12:
                raise RuntimeError("Small smoke request limit reached")
            call = current_model_call.get()
            entry = {"operation": call.operation if call else "other"}
            report["requests"].append(entry)
        started = perf_counter()
        try:
            result = actual(**kwargs)
            entry.update(
                status="success", usage=result.usage.model_dump() if result.usage else None
            )
            entry["response"] = result.choices[0].message.content if result.choices else None
            return result
        except Exception as error:
            entry.update(status="failed", error=safe_error_details(error))
            raise
        finally:
            entry["seconds"] = round(perf_counter() - started, 3)

    def trace(event, data):
        with lock:
            with (output / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps({"event": event, **data}, ensure_ascii=False, default=str) + "\n"
                )

    token = trace_sink.set(trace)
    try:
        with patch.object(provider.client.chat.completions, "create", side_effect=observed):
            for index in (1, 2):
                answer_text = (source / f"answer_{index}.txt").read_text(encoding="utf-8")
                answer = CandidateAnswer(
                    interview_id=iid,
                    question_id=question.question_id,
                    answer_id=str(uuid4()),
                    text=answer_text,
                )
                started = perf_counter()
                feedback = await evaluation.evaluate(
                    EvaluationRequest(
                        request_id=str(uuid4()), interview_id=iid, question=question, answer=answer
                    )
                )
                analysis_seconds = perf_counter() - started
                if feedback.analysis_status != "valid":
                    report["invalid_feedback"] = feedback.model_dump(mode="json")
                    raise AssertionError("Real scoped analysis was unavailable")
                action = await service.apply_evaluation_feedback(iid, feedback, answer=answer)
                context = await repo.get_interview_context(iid)
                progress = context.topic_progress[question.topic_key]
                row = {
                    "index": index,
                    "question": question.text,
                    "answer": answer_text,
                    "analysis_seconds": round(analysis_seconds, 3),
                    "answer_to_next_seconds": round(perf_counter() - started, 3),
                    "feedback": feedback.model_dump(mode="json"),
                    "progress": progress.model_dump(mode="json"),
                    "next_question": action.question.model_dump(mode="json")
                    if action.question
                    else None,
                    "generation_reason": action.decision_trace.details.get("generation_reason"),
                }
                report["turns"].append(row)
                print(
                    json.dumps(
                        {
                            "index": index,
                            "next_question": row["next_question"],
                            "seconds": row["answer_to_next_seconds"],
                            "needs": [
                                {"target": n.target, "status": n.status}
                                for n in progress.information_needs
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert feedback.analysis_status == "valid" and feedback.analysis.needs_assessed
                assert action.question is not None, (
                    "A local unavailable detail must not finish the interview"
                )
                if action.question.topic_key == question.topic_key:
                    assert not re.search(
                        r"tensor operation|channel.wise concat|cross.attention|"
                        r"(?:inside|enter|before).{0,25}DiT blocks|mathematical fusion",
                        action.question.text + " " + action.question.information_goal,
                        re.IGNORECASE,
                    ), "The recorded unavailable private fusion must not be asked again"
                assert any(
                    n.status == "blocked" and n.blocked_evidence for n in progress.information_needs
                )
                assert not any(
                    n.status == "open" and n.blocked_evidence for n in progress.information_needs
                )
                (output / f"context_{index}.json").write_text(
                    context.model_dump_json(indent=2), encoding="utf-8"
                )
                question = action.question
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=safe_error_details(error))
        raise
    finally:
        await service.close_background()
        provider.client.close()
        trace_sink.reset(token)
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"output": str(output), "status": report["status"]}), flush=True)


if __name__ == "__main__":
    asyncio.run(run())
