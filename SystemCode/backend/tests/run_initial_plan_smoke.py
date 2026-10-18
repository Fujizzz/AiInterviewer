"""Real opening-plan smoke using a previous private interview's profile and question bank.

Declaration Index:
- main: Load existing artifacts without parsing or preparing questions, then run the smoke.
- main.audited: Record SDK durations and token usage without credentials or request bodies.
- main.exercise: Simulate a thirty-second introduction and publish one technical question.
- main.trace: Retain planning failures and acceptance diagnostics in the private artifact.
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))["final_context"]
    output = ROOT / "output" / (
        "initial_plan_background_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True)
    from agents.model_calls import current_model_call
    from agents.orchestrator import InterviewAgentService
    from agents.tracing import trace_sink
    from app.adapters.llm import ProviderLLMAdapter
    from app.providers.llm import OpenAILLM
    from shared.contracts import CandidateAnswer, InitializeInterviewRequest
    from shared.contracts.prepared import PreparedQuestion
    from tests.agent.mocks import InMemoryRepository

    client = OpenAILLM()
    report = {
        "scope": "Real API initial plan and first technical question; reused profile/cache, "
        "in-memory repository, thirty-second scripted introduction; no scoring or live RAG.",
        "model": client.model,
        "source": str(args.source),
        "api_requests": [],
        "planning_events": [],
    }
    create = client.client.chat.completions.create

    def audited(**kwargs):
        started = perf_counter()
        call = current_model_call.get()
        record = {"operation": call.operation if call else None}
        report["api_requests"].append(record)
        try:
            response = create(**kwargs)
            record["usage"] = response.usage.model_dump() if response.usage else None
            record["status"] = "response"
            return response
        except Exception as error:
            record.update(status="error", error_type=type(error).__name__)
            raise
        finally:
            record["seconds"] = round(perf_counter() - started, 6)

    client.client.chat.completions.create = audited

    async def exercise():
        repository = InMemoryRepository()
        service = InterviewAgentService(
            repository=repository,
            llm=ProviderLLMAdapter(client),
            background_replanning=True,
            prepared_questions=[PreparedQuestion.model_validate(q) for q in source["prepared_questions"]],
        )
        request = InitializeInterviewRequest(
            interview_id=str(uuid4()),
            candidate_profile=source["candidate_profile"],
            job_profile=source["job_profile"],
            duration_seconds=900,
            enabled_stages=["project_deep_dive"],
            planning_enabled=True,
            include_introduction=True,
        )
        try:
            started = perf_counter()
            initial = await service.initialize_interview(request)
            report["intro_return_seconds"] = round(perf_counter() - started, 6)
            await asyncio.sleep(30)
            task = service._initial_planner.tasks[request.interview_id]
            report["initial_plan_ready_before_submission"] = task.done()
            answer = CandidateAnswer(
                interview_id=request.interview_id,
                question_id=initial.first_action.question.question_id,
                answer_id=str(uuid4()),
                text=source["introduction_answer"]["text"],
            )
            submitted = perf_counter()
            action = await service.complete_introduction(
                request.interview_id, answer, request_id=str(uuid4())
            )
            report["intro_submission_to_technical_seconds"] = round(perf_counter() - submitted, 6)
            context = await repository.get_interview_context(request.interview_id)
            report["plan_history"] = [p.model_dump(mode="json") for p in context.plan_history]
            report["technical_action"] = action.model_dump(mode="json")
            report["used_prepared_question_ids"] = context.used_prepared_question_ids
            report["status"] = "completed"
        finally:
            await service.close_background()

    def trace(event, data):
        if event.startswith("planning.") and event not in {"planning.requested"}:
            report["planning_events"].append({"event": event, **data})

    token = trace_sink.set(trace)
    try:
        asyncio.run(exercise())
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        trace_sink.reset(token)
        client.client.close()
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"output": str(output), "status": report.get("status")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
