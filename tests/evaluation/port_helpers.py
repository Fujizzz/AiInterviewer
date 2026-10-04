"""Offline four-stage provider shared by runtime integration tests."""

from evaluation.analyzer import ConversationAnalysis
from evaluation.extractor import EvidenceExtraction
from evaluation.judge import JudgeDraft
from evaluation.resolution import ResolutionDraft

SCHEMAS = (ConversationAnalysis, EvidenceExtraction, ResolutionDraft, JudgeDraft)


def evaluation_output(prompt, data, schema):
    if schema is ConversationAnalysis:
        return schema(
            status="substantive",
            answer_scope="concrete",
            summary="Concrete personal action.",
            new_information=True,
            thread_complete=False,
            missing_information=["Fix validation"],
            uncertainties=[],
            contradiction_evidence=[],
        )
    if schema is EvidenceExtraction:
        text = data["answer"]["text"]
        return schema(
            evidence=[
                dict(
                    quote_spans=[dict(quote=text, char_start=0, char_end=len(text))],
                    normalized_claim=text,
                    evidence_kind="personal_action",
                    ownership_scope="personal",
                    factuality="reported_experience",
                    specificity="concrete",
                )
            ]
        )
    if schema is ResolutionDraft:
        return schema(
            decisions=[
                dict(
                    evidence_id=e["evidence_id"],
                    relation="new",
                    independence="new_episode",
                    concise_rationale="Independent reported action.",
                )
                for e in data["current_evidence"]
            ]
        )
    if schema is JudgeDraft:
        groups = {}
        for item in data["evidence"]:
            groups.setdefault(item["independence_group_id"], []).append(item["evidence_id"])
        assessments = []
        for rubric in data["rubric"]["rubrics"]:
            for criterion in rubric["criteria"]:
                key = criterion["criterion_id"]
                for evidence in groups.values() if key == "debugging.diagnostic_method" else [[]]:
                    assessments.append(
                        dict(
                            competency=rubric["competency"],
                            criterion_id=key,
                            evidence_ids=evidence,
                            assigned_level=3 if evidence else None,
                            matched_anchor_ids=[key + ".l3"] if evidence else [],
                            decision="included" if evidence else "insufficient",
                            reason_codes=["observable_behavior" if evidence else "no_evidence"],
                            concise_rationale="Concrete diagnostic behavior or a gap.",
                        )
                    )
        return schema(assessments=assessments)
    raise AssertionError(f"Unexpected schema: {schema}")
