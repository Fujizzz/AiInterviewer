"""Public explanations from the exact score basis, with no additional model calls.

Only selected, positive contributions of published criteria are exposed. Private
rubric text, anchors, weights and judge prompts/rationales remain internal.
"""

from typing import Literal

from pydantic import Field

from app.providers.llm import OutputModel
from evaluation.publication import latest_scoring_record, scoreable_aggregation


class ReportEvidence(OutputModel):
    answer_id: str
    question_id: str
    round_number: int | None = None
    question: str
    quotes: list[str]
    levels: list[int]
    evidence_kind: str
    ownership_scope: str
    specificity: str
    factuality: str


class CompetencyExplanation(OutputModel):
    selected_evidence_count: int = 0
    scored_observation_count: int = 0
    total_observation_count: int = 0
    examples: list[ReportEvidence] = Field(default_factory=list)
    omitted_evidence_count: int = 0
    source_unavailable_count: int = 0
    limitation_codes: list[str] = Field(default_factory=list)


class ReportExplanation(OutputModel):
    version: Literal["1.0"] = "1.0"
    answer_count: int = 0
    competencies: dict[str, CompetencyExplanation] = Field(default_factory=dict)


def build_explanation(context, records, publication) -> ReportExplanation:
    """Explain existing numbers, never rescore or resurrect unselected evidence.

    Keep at most three distinct source excerpts per dimension. The total and
    omitted counts make this sampling explicit. Validate identity and exact
    Unicode offsets again at this public boundary, including imported records.
    """
    history = getattr(context, "question_history", [])
    rounds = {
        entry.answer.answer_id: index
        for index, entry in enumerate(history, 1)
        if entry.answer is not None
    }
    result = ReportExplanation(answer_count=len(context.processed_feedback_ids))
    aggregation = scoreable_aggregation(latest_scoring_record(records))
    if aggregation is None:
        return result
    if aggregation.snapshot.snapshot_id != publication["score_basis_snapshot_id"]:
        return result
    selected_pairs = {(w.assessment_id, w.evidence_id) for w in aggregation.weights if w.selected}
    sources = {s.evidence.evidence_id: s for s in aggregation.inputs.history.sources}
    for dimension in aggregation.snapshot.competencies:
        name = dimension.competency.value
        public_score = publication["competencies"][name]
        usable = [c for c in dimension.criteria if c.status == "published"]
        explanation = CompetencyExplanation(
            total_observation_count=len(dimension.criteria),
            scored_observation_count=len(usable) if public_score["score"] is not None else 0,
            limitation_codes=sorted(
                {
                    code
                    for c in dimension.criteria
                    if c.status != "published"
                    for code in c.reason_codes
                }
                | set(public_score["reason_codes"])
            ),
        )
        result.competencies[name] = explanation
        if public_score["score"] is None:
            continue
        selected = {}
        for criterion in usable:
            for contribution in criterion.contributions:
                if contribution.effective_weight <= 0:
                    continue
                for identifier in contribution.evidence_ids:
                    if (contribution.assessment_id, identifier) in selected_pairs:
                        selected.setdefault(identifier, set()).add(contribution.assigned_level)
        explanation.selected_evidence_count = len(selected)
        examples = []
        for identifier, levels in selected.items():
            source = sources.get(identifier)
            if source is None:
                explanation.source_unavailable_count += 1
                continue
            try:
                source.evidence.validate_answer(source.turn.answer.as_candidate_answer())
            except ValueError:
                explanation.source_unavailable_count += 1
                continue
            item = source.evidence
            examples.append(
                ReportEvidence(
                    answer_id=item.answer_id,
                    question_id=item.question_id,
                    round_number=rounds.get(item.answer_id),
                    question=source.turn.question.text,
                    quotes=[span.quote for span in item.quote_spans],
                    levels=sorted(levels),
                    evidence_kind=item.evidence_kind,
                    ownership_scope=item.ownership_scope,
                    specificity=item.specificity,
                    factuality=item.factuality,
                )
            )
        # Stable chronological display, independent of per-run hashed evidence IDs.
        examples.sort(
            key=lambda e: (e.round_number is None, e.round_number or 0, e.question, tuple(e.quotes))
        )
        # Prefer coverage of different answers before showing more of one answer.
        seen = set()
        first, remaining = [], []
        for example in examples:
            (remaining if example.answer_id in seen else first).append(example)
            seen.add(example.answer_id)
        explanation.examples = (first + remaining)[:3]
        explanation.omitted_evidence_count = len(examples) - len(explanation.examples)
    return result
