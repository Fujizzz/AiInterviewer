"""Aggregate supported multi-dimensional observations within the atomic turn."""

from hashlib import sha256

from agents.domain.errors import InvalidAgentState
from agents.domain.models import EvidenceRecord
from shared.contracts import Competency, CompetencyState


def _quotes(evidence):
    return evidence.source_quotes or [evidence.quote]


def _apply_relations(context, question, answer, feedback):
    if answer is None or feedback.analysis_status != "valid":
        return
    for relation in feedback.analysis.answer_relations:
        earlier = next(
            (
                entry
                for entry in context.question_history
                if entry.answer is not None
                and entry.answer.answer_id == relation.earlier_answer_id
                and entry.question.project_id == question.project_id
            ),
            None,
        )
        if (
            earlier is None
            or relation.earlier_answer_id == answer.answer_id
            or relation.earlier_quote not in earlier.answer.text
            or relation.current_quote not in answer.text
        ):
            raise InvalidAgentState("Answer relation must reference grounded same-project answers")
        key = "\0".join(
            (
                relation.earlier_answer_id,
                answer.answer_id,
                relation.kind,
                relation.earlier_quote,
                relation.current_quote,
            )
        )
        relation_id = relation.relation_id or "relation-" + sha256(key.encode()).hexdigest()[:16]
        if any(item["relation_id"] == relation_id for item in context.answer_relations):
            continue
        context.answer_relations.append(
            {
                **relation.model_dump(mode="json"),
                "relation_id": relation_id,
                "current_answer_id": answer.answer_id,
                "project_id": question.project_id,
                "validation_status": "grounded",
            }
        )
        if relation.kind == "clarifies":
            continue
        for record in context.evidence_records:
            if (
                record.status == "active"
                and record.project_id == question.project_id
                and record.answer_id == relation.earlier_answer_id
                and any(
                    relation.earlier_quote in quote or quote in relation.earlier_quote
                    for quote in _quotes(record.evidence)
                    if quote.strip()
                )
            ):
                record.status = "superseded" if relation.kind == "supersedes" else "disputed"
                record.relation_id = relation_id


def apply_evidence(context, question, answer, feedback):
    _apply_relations(context, question, answer, feedback)
    dimensions = feedback.dimensions if feedback.assessment_status == "valid" else []
    if feedback.analysis_status == "valid" and (
        feedback.analysis.status
        in {
            "non_answer",
            "explicit_unknown",
            "refusal",
        }
        or feedback.analysis.answer_scope in {"label_only", "none"}
    ):
        dimensions = []
    seen = set()
    for evidence in dimensions:
        if evidence.competency in seen:
            raise InvalidAgentState("Duplicate assessment dimension")
        seen.add(evidence.competency)
        if answer is None or any(
            not quote.strip() or quote not in answer.text for quote in _quotes(evidence)
        ):
            raise InvalidAgentState("Assessment evidence must quote the current answer")
        thread_id = question.thread_id or question.question_id
        fact = " ".join(evidence.fact.casefold().split())
        matching = next(
            (
                record
                for record in context.evidence_records
                if record.status == "active"
                and record.project_id == question.project_id
                and record.evidence.competency == evidence.competency
                and (
                    record.thread_id == thread_id
                    or " ".join(record.evidence.fact.casefold().split()) == fact
                    or record.evidence.quote.strip() == evidence.quote.strip()
                )
            ),
            None,
        )
        record = EvidenceRecord(
            project_id=question.project_id,
            thread_id=thread_id,
            question_id=question.question_id,
            answer_id=answer.answer_id,
            difficulty=question.difficulty,
            evidence=evidence.model_copy(deep=True),
        )
        if matching is None:
            context.evidence_records.append(record)
        elif evidence.strength >= matching.evidence.strength or feedback.analysis.contradictions:
            context.evidence_records[context.evidence_records.index(matching)] = record
    retire_related_evidence(context)
    rebuild_scores(context, seen)


def retire_related_evidence(context):
    """Apply the durable relation ledger to existing and late-arriving scores."""
    for relation in context.answer_relations:
        if relation.get("validation_status") != "grounded" or relation.get("kind") not in {
            "supersedes",
            "disputes",
        }:
            continue
        sides = [(relation["earlier_answer_id"], relation["earlier_quote"])]
        if relation["kind"] == "disputes":
            sides.append((relation["current_answer_id"], relation["current_quote"]))
        for record in context.evidence_records:
            if record.status != "active" or record.project_id != relation["project_id"]:
                continue
            if any(
                record.answer_id == answer_id
                and any(quote in q or q in quote for q in _quotes(record.evidence) if q.strip())
                for answer_id, quote in sides
            ):
                record.status = "superseded" if relation["kind"] == "supersedes" else "disputed"
                record.relation_id = relation["relation_id"]


def rebuild_scores(context, seen=()):
    """Rebuild dimensions, including ones whose sole support was retracted."""
    for competency in Competency:
        entries = [
            r
            for r in context.evidence_records
            if r.status == "active" and r.evidence.competency == competency
        ]
        scored = [r for r in entries if r.evidence.rubric_level is not None]
        verified = [
            r
            for r in entries
            if r.evidence.observation == "supported" and r.evidence.strength >= 0.5
        ]
        previous = context.state.competencies.get(
            competency, CompetencyState(competency=competency)
        )
        context.state.competencies[competency] = CompetencyState(
            competency=competency,
            score=sum(r.evidence.rubric_level for r in scored) / len(scored) if scored else None,
            coverage=min(1.0, sum(r.evidence.strength for r in entries) * 0.2),
            evidence_count=len(entries),
            independent_evidence_count=len(verified),
            max_verified_difficulty=max((r.difficulty for r in verified), default=0),
            last_asked_at_question_index=(
                context.state.question_index
                if competency in seen
                else previous.last_asked_at_question_index
            ),
        )
