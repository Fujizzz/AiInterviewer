"""Bounded paid smoke for alias grounding, cached admission and all-project planning.

Reuse saved resume/question artifacts. No parsing, preparation, scoring, RAG startup,
candidate-answer generation or complete interview. Offline replay consumes no API.
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
    from agents.planning.planner import InterviewPlannerAgent, execution_topics
    from agents.question.prepared import admit_prepared
    from app.adapters.assessment import RealtimeDecisionAdapter
    from app.adapters.decision import CompactAnswerDecision
    from app.adapters.llm import ProviderLLMAdapter
    from app.providers.llm import OpenAILLM
    from app.settings import interview_settings
    from shared.contracts import CandidateAnswer, EvaluationRequest, PlannedQuestion

    saved = json.loads(
        (ROOT / "output/live_15min_20261016T061714Z/result.json").read_text(encoding="utf-8")
    )
    output = ROOT / "output" / ("delivery_smoke_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir(parents=True)
    report = {"cases": [], "api_requests": [], "offline_replay": [], "evaluation_tested": False}
    client = OpenAILLM()
    report["model"] = client.model
    create = client.client.chat.completions.create

    def audited(**kwargs):
        if len(report["api_requests"]) >= 6:
            raise RuntimeError("Smoke SDK request cap reached")
        record = {"case": report["cases"][-1]["name"]}
        report["api_requests"].append(record)
        start = perf_counter()
        try:
            response = create(**kwargs)
            record["usage"] = response.usage.model_dump() if response.usage else None
            record["status"] = "response"
            return response
        except Exception as error:
            record.update(status="error", error_type=type(error).__name__)
            raise
        finally:
            record["seconds"] = round(perf_counter() - start, 6)

    client.client.chat.completions.create = audited

    class Repository:
        def __init__(self, context):
            self.context = context

        async def get_interview_context(self, _):
            return self.context

    def analysis_case(number):
        context = InterviewContext.model_validate(
            next(
                t["context"]
                for t in saved["traces"]
                if t["event"] == "turn.loaded" and t.get("turn") == number
            )
        )
        turn = saved["turns"][number - 1]
        answer = next(
            e["answer"]
            for e in saved["final_context"]["question_history"]
            if e["question"]["question_id"] == turn["question"]["question_id"]
        )
        request = EvaluationRequest(
            request_id=f"replay-{number}",
            interview_id=context.interview_id,
            question=turn["question"],
            answer=answer,
        )
        return context, request

    async def exercise():
        # All saved decisions are replayed through the actual adapter, not just DTO parsing.
        for number in range(2, 14):
            context, request = analysis_case(number)
            raw = next(
                c["output"]
                for c in saved["logical_calls"]
                if c.get("turn") == number and c.get("schema") == "CompactAnswerDecision"
            )
            def replay(*args, decision=raw):
                return CompactAnswerDecision.model_validate(decision)
            result = await RealtimeDecisionAdapter(replay, Repository(context)).evaluate(request)
            report["offline_replay"].append(
                {
                    "question": number,
                    "status": result.analysis_status,
                    "issues": result.evaluation_issues,
                }
            )

        report["cases"].append({"name": "real_answer_analysis_previous_q3"})
        context, request = analysis_case(3)
        result = await RealtimeDecisionAdapter(client, Repository(context)).evaluate(request)
        report["cases"][-1].update(
            passed=result.analysis_status == "valid", feedback=result.model_dump(mode="json")
        )

        base = InterviewContext.model_validate(saved["final_context"])
        item = next(
            q
            for q in base.prepared_questions
            if q.cache_id == "55009b8a-9a9a-4e55-91ff-f6b421f29e33"
        )
        plan = PlannedQuestion.model_validate(saved["turns"][1]["question"])
        concrete = (
            "I represented each objective with a TopicAllocation containing topic_key and "
            "budget_seconds. TopicProgress.elapsed_seconds tracks consumed time, and "
            "InterviewState.remaining_seconds tracks the session balance. I triggered local "
            "reallocation when the current allocation could not fit another round, and model "
            "replanning when the agenda was exhausted or observed pace changed."
        )
        for name, answer, expected in [
            ("intro_does_not_answer_budget", base.introduction_answer.text, True),
            ("concrete_budget_answer_blocks_repeat", concrete, False),
        ]:
            report["cases"].append({"name": name})
            context = base.model_copy(deep=True)
            context.question_history = []
            context.topic_progress = {}
            context.used_prepared_question_ids = []
            context.rejected_prepared_questions = {}
            context.introduction_answer = CandidateAnswer(
                interview_id=context.interview_id,
                question_id="intro",
                answer_id="intro",
                text=answer,
            )
            question, reason = await admit_prepared(
                item, plan, context, ProviderLLMAdapter(client), interview_settings()
            )
            report["cases"][-1].update(
                passed=(question is not None) == expected,
                accepted=question is not None,
                reason=reason,
            )

        report["cases"].append({"name": "initial_plan_all_projects"})
        context = base.model_copy(deep=True)
        context.question_history, context.closed_threads = [], []
        context.active_thread = None
        context.topic_progress, context.used_topic_keys, context.plan_history = {}, [], []
        context.state.question_index = 0
        context.state.remaining_seconds, context.state.elapsed_seconds = 900, 0
        context.state.project_visit_count = {}
        context.plan.version, context.plan.topics = 0, []
        planner = InterviewPlannerAgent(ProviderLLMAdapter(client), interview_settings())
        await planner.revise(context, "INITIAL_PLAN")
        required = {p.project_id for p in context.candidate_profile.projects}
        actual = {t.project_id for t in execution_topics(context)}
        report["cases"][-1].update(
            passed=required <= actual,
            fallback=context.plan_history[-1].fallback_used,
            required=sorted(required),
            allocated=sorted(actual),
            revision=context.plan_history[-1].model_dump(mode="json"),
        )

    try:
        asyncio.run(exercise())
        report["passed"] = all(c["passed"] for c in report["cases"]) and all(
            r["status"] == "valid" for r in report["offline_replay"]
        )
    finally:
        client.client.close()
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "output": str(output),
                    "passed": report.get("passed"),
                    "api_requests": len(report["api_requests"]),
                    "cases": [
                        {k: v for k, v in c.items() if k not in {"feedback", "revision"}}
                        for c in report["cases"]
                    ],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
