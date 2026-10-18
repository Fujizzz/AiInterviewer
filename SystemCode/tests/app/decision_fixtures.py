"""Translate existing scripted dialogue fixtures into the compact model wire format."""

from app.adapters.decision import CompactAnswerDecision


def compact_output(output):
    a = output["analysis"]
    return CompactAnswerDecision(
        status=a["status"],
        relevance=output.get("answer_relevance", 0),
        scope=a.get("answer_scope", "concrete"),
        new=a.get("new_information", False),
        complete=a.get("thread_complete", False),
        limits=a.get("limitation_segments", []),
        relations=[],
        criteria=[
            dict(
                id=c["criterion_id"],
                status=c["coverage_status"],
                gap=next(iter(c.get("missing_information", [])), ""),
                s=c.get("supporting_segment_ids", []),
            )
            for item in output.get("objective_coverage", []) or []
            for c in item.get("criterion_coverage", [])
        ],
        needs=[
            dict(target=target, status="open")
            for target in dict.fromkeys(
                [
                    *a.get("missing_information", []),
                    *(
                        gap
                        for c in output.get("objective_coverage", []) or []
                        for gap in c.get("missing_information", [])
                    ),
                ]
            )
        ]
        if a["status"] not in {"explicit_unknown", "refusal"}
        else [],
    )


def legacy_data(data):
    return {
        **data,
        "answer": " ".join(s["text"] for s in data["answer_segments"]),
        "objectives": [
            {
                "objective": o["goal"],
                "completion_requirements": [
                    {"criterion_id": c["id"], "requirement": c["requirement"]}
                    for c in o["criteria"]
                ],
            }
            for o in data["objectives"]
        ],
    }
