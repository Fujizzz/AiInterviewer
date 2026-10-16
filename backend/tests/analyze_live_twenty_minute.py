"""Responsibilities: Summarize measured live interview performance and immutable dialogue evidence.
Implementation: Aggregate actual SDK usage, bounded wall intervals, topic actions, plan revisions
and production review traces. Do not infer successful recovery from errors that were not observed.
Related Modules: run_live_twenty_minute writes the raw private artifacts; preparation benchmark
provides usage_totals. Manual dialogue assessment is recorded separately from mechanical metrics.

Declaration Index:
- distribution: Compute count, mean, median and maximum for measured seconds.
- category: Classify API calls by operation and schema, retaining background scoring separately.
- interval_seconds: Sum the union of clipped request intervals without double-counting overlap.
- question_source: Classify a displayed question using its committed provenance and fallback flag.
- summarize: Derive requested metrics from saved requests, dialogue, state and planning history.
- render: Write a readable report with latency, follow-ups, planning, sources, usage and stability.
- main: Load an existing run and write derived metrics without sending any API requests.

Variable Index:
None
"""

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from run_full_preparation_benchmark import usage_totals


def distribution(values):
    """Return seconds statistics; empty categories have no invented zero-duration observation."""
    return {
        "count": len(values),
        "mean_seconds": round(statistics.mean(values), 3) if values else None,
        "median_seconds": round(statistics.median(values), 3) if values else None,
        "max_seconds": round(max(values), 3) if values else None,
    }


def category(request, schemas):
    """Separate foreground work from report and background assessment provider requests."""
    operation = request.get("operation") or request.get("model_operation")
    schema = schemas.get(request.get("logical_call_id"), "")
    if operation == "resume_extraction" or schema == "ResumeExtraction":
        return "resume_parsing"
    if operation == "evaluation" or schema == "CompactAnswerDecision":
        return "answer_analysis"
    if (
        operation
        in {
            "question_quality",
            "question_repeat_check",
            "question_issue_check",
            "prepared_question_use",
        }
        or "Quality" in schema
        or "Adjudication" in schema
    ):
        return "quality_and_cache_admission"
    if operation == "question" or schema in {"QuestionAgentDecision", "StandaloneQuestionDraft"}:
        return "question_generation"
    if operation and "plan" in operation or "PlanProposal" in schema:
        return "planning"
    if schema in {"ReportNarrative", "InterviewReportNarrative", "FinalReportNarrative"}:
        return "report_narrative"
    return "background_scoring_or_other"


def interval_seconds(requests, start, end):
    """Clip actual SDK wall intervals to a foreground request and sum their union."""
    intervals = sorted(
        (max(start, r["started_elapsed"]), min(end, r["finished_elapsed"]))
        for r in requests
        if "started_elapsed" in r and r["finished_elapsed"] > start and r["started_elapsed"] < end
    )
    total, right = 0, start
    for left, stop in intervals:
        total += max(0, stop - max(left, right))
        right = max(right, stop)
    return round(total, 6)


def question_source(turn):
    """Prefer committed provenance over the superficial wording of a displayed question."""
    if turn.get("generation_reason") == "PREPARED_QUESTION":
        return "prepared"
    details = turn["action"].get("decision_trace", {}).get("details", {})
    return "template_fallback" if details.get("fallback_used") else "live_generation"


def summarize(report):
    """Derive measured rates and denominators, preserving final-report latency separately."""
    turns = report["turns"]
    requests = report["api_requests"]
    schemas = {c["id"]: c["schema"] for c in report["logical_calls"]}
    live = [r for r in requests if r["phase"] == "interview"]
    latency = []
    for turn in turns:
        if turn.get("advance_seconds") is None:
            continue
        start, end = turn["submitted_elapsed"], turn["returned_elapsed"]
        own = [r for r in live if r.get("turn") == turn["number"]]
        entry = {
            "turn": turn["number"],
            "next_type": turn["next_type"],
            "seconds": turn["advance_seconds"],
        }
        for name in (
            "answer_analysis",
            "question_generation",
            "quality_and_cache_admission",
            "planning",
        ):
            entry[name + "_api_seconds"] = interval_seconds(
                [r for r in own if category(r, schemas) == name], start, end
            )
        stages = [
            s for s in report["progress"] if s.get("turn") == turn["number"] and "seconds" in s
        ]
        entry["backend_stages"] = {s.get("stage"): s["seconds"] for s in stages}
        entry["foreground_api_requests"] = sum(
            category(r, schemas)
            in {"answer_analysis", "question_generation", "quality_and_cache_admission", "planning"}
            and r.get("started_elapsed", 0) < end
            for r in own
        )
        latency.append(entry)
    questions = [t for t in turns if t["question"].get("intent") != "SELF_INTRODUCTION"]
    knowledge = [
        t
        for t in questions
        if (t["action"].get("question") or {}).get("need_id", "").startswith("knowledge:")
    ]
    knowledge_ids = {t["number"] for t in knowledge}
    followups = [
        t
        for t in questions
        if t["question"].get("dialogue_action") in {"probe", "clarify"}
        and t["number"] not in knowledge_ids
    ]
    openings = [
        t for t in questions if t["question"].get("dialogue_action") in {"new_topic", "new_project"}
    ]
    counts = Counter()
    topic_statistics = {}
    followup_numbers = {turn["number"] for turn in followups}
    project_names = {p["project_id"]: p["name"] for p in report.get("projects", [])}
    for turn in questions:
        question = turn["question"]
        topic = topic_statistics.setdefault(
            question["topic_key"],
            {
                "topic_key": question["topic_key"],
                "topic": question["topic"],
                "project": project_names.get(question["project_id"], question["project_id"]),
                "questions": 0,
                "dynamic_followups": 0,
                "knowledge_questions": 0,
                "question_numbers": [],
            },
        )
        topic["questions"] += 1
        topic["dynamic_followups"] += turn["number"] in followup_numbers
        topic["knowledge_questions"] += turn["number"] in knowledge_ids
        topic["question_numbers"].append(turn["number"])
    for turn in openings:
        counts[question_source(turn)] += 1
    groups = defaultdict(list)
    for request in requests:
        groups[(request["phase"], category(request, schemas))].append(request)
    api_groups = [
        {
            "phase": phase,
            "category": name,
            "requests": len(items),
            "successful_responses": sum(r["status"] == "success" for r in items),
            "tokens": usage_totals(items),
        }
        for (phase, name), items in sorted(groups.items())
    ]
    traces = [t for t in report["traces"] if t.get("phase") == "interview"]
    runs = {}
    for trace in traces:
        qid = trace.get("question_id")
        if trace["event"] == "react.started":
            runs.setdefault(
                qid,
                {
                    "question_id": qid,
                    "turn": trace.get("turn"),
                    "drafts": 0,
                    "validations": [],
                    "stop_reason": None,
                },
            )
        if qid not in runs:
            continue
        row = runs[qid]
        if (
            trace["event"] == "react.decision"
            and trace.get("decision", {}).get("action") == "final"
        ):
            row["drafts"] += 1
        elif trace["event"] == "react.validation":
            row["validations"].append(trace.get("errors", []))
        elif trace["event"] == "react.finished":
            row["stop_reason"] = trace.get("stop_reason")
    for row in runs.values():
        reviews = [
            c
            for c in report["logical_calls"]
            if c.get("phase") == "interview"
            and c.get("model_question_id") == row["question_id"]
            and c["schema"] in {"CompactQualityReview", "QuestionQualityReview"}
        ]
        row["first_candidate_passed"] = (
            row["stop_reason"] == "FINAL"
            and row["drafts"] == 1
            and bool(row["validations"])
            and not row["validations"][0]
            and bool(reviews)
            and reviews[0]["status"] == "success"
        )
    revisions = report.get("final_context", {}).get("plan_history", []) or next(
        (turn["plan_history_after"] for turn in reversed(turns) if "plan_history_after" in turn), []
    )
    plan = [
        {
            "version": p["version"],
            "elapsed_seconds": p["elapsed_seconds"],
            "trigger": p["trigger"],
            "reason": p["reason"],
            "fallback_used": p["fallback_used"],
            "source": p.get("source"),
            "semantic_changes": p.get("semantic_changes", []),
            "topics": [
                {
                    k: t.get(k)
                    for k in ("topic_key", "objective", "budget_seconds", "expected_questions")
                }
                for t in p["topics"]
            ],
            "proposal": p.get("proposal"),
        }
        for p in revisions
    ]
    normalized = [" ".join(re.findall(r"\w+", t["question"]["text"].casefold())) for t in questions]
    review_counts = Counter(
        r.get("turn") for r in live if category(r, schemas) == "quality_and_cache_admission"
    )
    answer_stages = [
        s["seconds"]
        for s in report["progress"]
        if s.get("stage") == "answer_evaluation" and "seconds" in s
    ]
    evaluation_tested = report.get("evaluation_tested", True)
    result = {
        "status": report["status"],
        "model": report["model"],
        "evaluation_tested": evaluation_tested,
        "report_generated": report.get("report_generated", evaluation_tested),
        "live_seconds": report.get("live_seconds"),
        "configured_seconds": report.get("configured_minutes", 20) * 60,
        "exchange_seconds_until_finish_submission": (
            turns[-1]["submitted_elapsed"] - report["live_started_elapsed"]
            if turns and "submitted_elapsed" in turns[-1] and "live_started_elapsed" in report
            else None
        ),
        "final_report_wait_seconds": turns[-1].get("advance_seconds")
        if evaluation_tested and turns and turns[-1].get("next_type") == "finished"
        else None,
        "finish_response_wait_seconds": turns[-1].get("advance_seconds")
        if turns and turns[-1].get("next_type") == "finished"
        else None,
        "final_score_status": report.get("final_response", {})
        .get("result", {})
        .get("final_report", {})
        .get("score_status")
        if evaluation_tested
        else "not_tested",
        "overall_score": report.get("final_response", {})
        .get("result", {})
        .get("final_report", {})
        .get("overall_score"),
        "unscored_answer_count": report.get("final_response", {})
        .get("result", {})
        .get("final_report", {})
        .get("unscored_answer_count"),
        "first_question_start_seconds": report.get("first_question_start_seconds"),
        "introduction_to_first_technical_seconds": turns[0].get("advance_seconds")
        if turns
        else None,
        "submit_to_next_question": distribution(
            [r["seconds"] for r in latency if r["next_type"] == "question"]
        ),
        "technical_answer_to_next_question": distribution(
            [r["seconds"] for r in latency if r["next_type"] == "question" and r["turn"] != 1]
        ),
        "answer_analysis_stage": distribution(answer_stages),
        "latency_components": {
            name: distribution(
                [
                    row.get("backend_stages", {}).get("answer_evaluation", 0)
                    if name == "answer_analysis_stage"
                    else row[name + "_api_seconds"]
                    for row in latency
                    if row["next_type"] == "question" and row["turn"] != 1
                ]
            )
            for name in (
                "answer_analysis_stage",
                "question_generation",
                "quality_and_cache_admission",
                "planning",
            )
        },
        "per_turn_latency": latency,
        "questions_including_intro": len(turns),
        "technical_questions": len(questions),
        "practical_followups_total": len(followups),
        "dynamic_followups": sum(question_source(t) == "live_generation" for t in followups),
        "followup_sources": dict(Counter(question_source(t) for t in followups)),
        "protocol_followups_total": sum(
            t["question"].get("dialogue_action") in {"probe", "clarify"} for t in questions
        ),
        "followups_by_topic": dict(Counter(t["question"]["topic_key"] for t in followups)),
        "followup_question_numbers": [t["number"] for t in followups],
        "topic_statistics": list(topic_statistics.values()),
        "new_topic_sources": dict(counts),
        "raw_generation_reasons": dict(Counter(t.get("generation_reason") for t in questions)),
        "knowledge_questions": len(knowledge),
        "knowledge_question_numbers": [t["number"] for t in knowledge],
        "api_requests": len(requests),
        "tokens": usage_totals(requests),
        "api_groups": api_groups,
        "interview_api_requests": len(live),
        "interview_tokens": usage_totals(live),
        "pre_interview_api_requests": len(requests) - len(live),
        "pre_interview_tokens": usage_totals([r for r in requests if r["phase"] != "interview"]),
        "codex_answer_api_requests": 0,
        "preparation_first_pass_count": sum(
            q["status"] == "ready" and q["api_requests"] == 2 for q in report["question_attempts"]
        ),
        "preparation_first_pass_denominator": len(report["question_attempts"]),
        "dynamic_generation_runs": list(runs.values()),
        "first_candidate_pass_count": sum(r["first_candidate_passed"] for r in runs.values()),
        "first_candidate_pass_denominator": len(runs),
        "rewrites": sum(max(0, r["drafts"] - 1) for r in runs.values()),
        "fallback_displayed_questions": [
            t["number"]
            for t in questions
            if t["action"].get("decision_trace", {}).get("details", {}).get("fallback_used")
        ],
        "exact_repeated_question_count": len(normalized) - len(set(normalized)),
        "max_quality_requests_per_turn": max(review_counts.values(), default=0),
        "quality_requests_by_turn": dict(review_counts),
        "logical_failures": [c for c in report["logical_calls"] if c["status"] != "success"],
        "exceptions": report.get("exceptions", []),
        "plan_revisions": plan,
        "planning_background_events": dict(
            Counter(t["event"] for t in traces if t["event"].startswith("planning.background"))
        ),
        "finish_state": report.get("final_context", {}).get("state"),
        "final_action": (report.get("final_response") or {}).get("result", {}),
    }
    return result


def render(report, summary, output):
    """Write the six requested metric categories and links to complete private evidence."""
    lines = [
        f"# Real {report.get('configured_minutes', 20)}-minute interview benchmark",
        "",
        report["scope"],
        "",
        f"Model: {report['model']}; evaluation mode: {report.get('evaluation_mode')}.",
        f"Status: {summary['status']}; configured: {summary['configured_seconds']} seconds; "
        f"actual: {summary['live_seconds']} seconds.",
        "Evaluation: "
        + (
            "included."
            if summary["evaluation_tested"]
            else "not tested; live answer analysis retained, scoring and reports excluded."
        ),
        "",
        "## Latency",
        "",
        "```json",
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "first_question_start_seconds",
                    "introduction_to_first_technical_seconds",
                    "submit_to_next_question",
                    "technical_answer_to_next_question",
                    "answer_analysis_stage",
                )
            },
            indent=2,
        ),
        "```",
        "",
        "| Answer turn | Next type | Total s | Analysis API s | Generation API s | "
        "Quality API s | Planning API s |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["per_turn_latency"]:
        lines.append(
            f"| {row['turn']} | {row['next_type']} | {row['seconds']:.3f} | "
            f"{row['answer_analysis_api_seconds']:.3f} | "
            f"{row['question_generation_api_seconds']:.3f} | "
            f"{row['quality_and_cache_admission_api_seconds']:.3f} | "
            f"{row['planning_api_seconds']:.3f} |"
        )
    lines.extend(
        [
            "",
            "API intervals are clipped to the foreground request. Background scoring is "
            "excluded from those components. Timed-out SDK workers may briefly overlap the next "
            "stage, so API component sums are not an additive critical-path decomposition. "
            "Backend answer_evaluation stage timing is recorded independently. "
            "Final report waiting is separate "
            "from next-question latency.",
            "",
            "## Follow-ups and sources",
            "",
            "```json",
            json.dumps(
                {
                    k: summary[k]
                    for k in (
                        "questions_including_intro",
                        "technical_questions",
                        "dynamic_followups",
                        "followups_by_topic",
                        "followup_question_numbers",
                        "new_topic_sources",
                        "raw_generation_reasons",
                        "knowledge_questions",
                        "knowledge_question_numbers",
                    )
                },
                indent=2,
            ),
            "```",
            "",
            "## Plan and Execute",
            "",
            "```json",
            json.dumps(summary["plan_revisions"], ensure_ascii=False, indent=2),
            "```",
            "",
            "Background events: " + json.dumps(summary["planning_background_events"]),
            "",
            "## API requests and provider-reported tokens",
            "",
            "| Period | Category | Requests | Input tokens | Output tokens | "
            "Total tokens | Unknown usage |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for group in summary["api_groups"]:
        u = group["tokens"]
        lines.append(
            f"| {group['phase']} | {group['category']} | {group['requests']} | "
            f"{u['input_tokens']} | {u['output_tokens']} | {u['total_tokens']} | "
            f"{u['unknown_requests']} |"
        )
    lines.extend(
        [
            "",
            f"All requests: {summary['api_requests']}; tokens: {summary['tokens']}.",
            "Codex authored candidate answers directly; no simulated-answer provider calls.",
            "",
            "## Quality and stability",
            "",
            "```json",
            json.dumps(
                {
                    k: summary[k]
                    for k in (
                        "first_candidate_pass_count",
                        "first_candidate_pass_denominator",
                        "rewrites",
                        "fallback_displayed_questions",
                        "exact_repeated_question_count",
                        "max_quality_requests_per_turn",
                        "exceptions",
                    )
                },
                indent=2,
            ),
            "```",
            "",
            "First-pass denominator is dynamic ReAct generation runs, including failed runs; "
            "cached questions are reported separately. Semantic repetition requires "
            "manual dialogue review.",
            "",
            "## Evidence and boundaries",
            "",
            "- transcript.md: all questions and Codex-authored candidate answers.",
            "- result.json: full requests, returned usage, turns, traces and final state.",
            "- metrics.json: derived machine-readable metrics.",
            "- traces.jsonl: original planning, quality, recovery and state observations.",
            "- benchmark.sqlite3: private persisted resume, cache, interview and evaluation data.",
            "No failure injection or hidden policy changes were performed. Observed errors may "
            "demonstrate recovery; absence of errors does not establish fault tolerance.",
        ]
    )
    if (output / "manual_review.md").exists():
        lines.extend(
            [
                "",
                "## Manual dialogue and planning review",
                "",
                (output / "manual_review.md").read_text(encoding="utf-8"),
            ]
        )
    (output / "metrics_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    """Analyze an existing private test directory; perform no provider or database mutations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = json.loads((args.output / "result.json").read_text(encoding="utf-8"))
    summary = summarize(report)
    (args.output / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    render(report, summary, args.output)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "status",
                    "live_seconds",
                    "questions_including_intro",
                    "dynamic_followups",
                    "api_requests",
                    "tokens",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
