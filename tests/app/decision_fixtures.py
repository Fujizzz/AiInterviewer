"""Translate existing scripted dialogue fixtures into the compact model wire format."""

from app.adapters.decision import CompactAnswerDecision


def compact_output(output):
    a = output["analysis"]
    return CompactAnswerDecision(
        answer_relevance=output.get("answer_relevance", 0.9),
        analysis=dict(
            status=a["status"],
            scope=a.get("answer_scope", "concrete"),
            new_information=a.get("new_information", False),
            complete=a.get("thread_complete", False),
            need=next(iter(a.get("missing_information", [])), None),
        ),
        coverage=[
            dict(
                objective_id=item["objective_id"],
                status=item["coverage_status"],
                missing=item.get("missing_information", []),
                segments=item.get("supporting_segment_ids", []),
                criteria=[
                    dict(
                        criterion_id=c["criterion_id"],
                        status=c["coverage_status"],
                        missing=c.get("missing_information", []),
                        segments=c.get("supporting_segment_ids", []),
                    )
                    for c in item.get("criterion_coverage", [])
                ],
            )
            for item in output.get("objective_coverage", []) or []
        ],
    )


def legacy_data(data):
    return {**data, "answer": " ".join(s["text"] for s in data["answer_segments"])}
