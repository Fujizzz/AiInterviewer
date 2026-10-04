"""Aggregate supported multi-dimensional observations within the atomic turn."""

from agents.domain.errors import InvalidAgentState
from agents.domain.models import EvidenceRecord
from shared.contracts import CompetencyState


def apply_evidence(context, question, answer, feedback):
    if feedback.analysis.status in {
        "non_answer",
        "explicit_unknown",
        "refusal",
    } or feedback.analysis.answer_scope in {"label_only", "none"}:
        return
    seen = set()
    for evidence in feedback.dimensions:
        if evidence.competency in seen:
            raise InvalidAgentState("Duplicate assessment dimension")
        seen.add(evidence.competency)
        if answer is None or not evidence.quote.strip() or evidence.quote not in answer.text:
            raise InvalidAgentState("Assessment evidence must quote the current answer")
        thread_id = question.thread_id or question.question_id
        fact = " ".join(evidence.fact.casefold().split())
        matching = next(
            (
                record
                for record in context.evidence_records
                if record.project_id == question.project_id
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
        entries = [
            r for r in context.evidence_records if r.evidence.competency == evidence.competency
        ]
        scored = [r for r in entries if r.evidence.rubric_level is not None]
        verified = [
            r
            for r in entries
            if r.evidence.observation == "supported" and r.evidence.strength >= 0.5
        ]
        context.state.competencies[evidence.competency] = CompetencyState(
            competency=evidence.competency,
            score=sum(r.evidence.rubric_level for r in scored) / len(scored) if scored else None,
            coverage=min(1.0, sum(r.evidence.strength for r in entries) * 0.2),
            evidence_count=len(entries),
            independent_evidence_count=len(verified),
            max_verified_difficulty=max((r.difficulty for r in verified), default=0),
            last_asked_at_question_index=context.state.question_index,
        )
