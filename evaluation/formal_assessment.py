"""Project current-answer capability feedback from the existing formal scoring receipt."""

from agents.domain.errors import InvalidAgentState
from evaluation.aggregation import ScoredEvaluation
from evaluation.assessment import AssessmentRecord, validate_assessment
from evaluation.inputs import AnswerSnapshot, QuestionSnapshot
from shared.contracts import DimensionEvidence


def assessment_from_formal(job, record):
    """No model call or new level: only included, positive-weight current-answer evidence.

    Cross-answer ratings are not attributed to one answer. Multiple criteria for
    one capability merge conservatively using the existing grounding function.
    Formal snapshots remain authoritative; this is a derived dialogue/UI view.
    """
    from app.adapters.evaluation import answer_segments, grounded_dimensions

    request = job.request
    if (
        record.input.request_id != request.request_id
        or record.input.interview_id != request.interview_id
        or record.input.answer != AnswerSnapshot.from_answer(request.answer)
        or record.input.question != QuestionSnapshot.from_question(request.question)
        or record.base_state_version != job.snapshot().state.state_version
    ):
        raise InvalidAgentState("Formal assessment must match its immutable source turn")
    scored = ScoredEvaluation.model_validate(record.scored.model_dump())
    base = dict(
        request_id=request.request_id,
        interview_id=request.interview_id,
        question_id=request.question.question_id,
        answer_id=request.answer.answer_id,
        base_state_version=record.base_state_version,
    )
    if scored.evaluation.status == "failed":
        return validate_assessment(
            job,
            AssessmentRecord(
                **base,
                assessment_status="unavailable",
                evaluation_issues=list(scored.evaluation.reason_codes),
            ),
        )
    items = {item.evidence_id: item for item in scored.evaluation.evidence_items}
    eligible = {
        state.evidence_id for state in scored.resolution.states if state.status == "eligible"
    }
    weights = {
        (w.assessment_id, w.evidence_id): w.effective_weight
        for w in scored.aggregation.weights
        if w.selected
    }
    dimensions = []
    for rating in scored.evaluation.assessments:
        keys = set(rating.evidence_ids)
        if (
            rating.decision != "included"
            or not keys
            or not keys <= items.keys()
            or not keys <= eligible
        ):
            continue
        strength = min(weights.get((rating.assessment_id, key), 0.0) for key in keys)
        if strength <= 0:
            continue
        sources = [items[key] for key in rating.evidence_ids]
        dimensions.append(
            DimensionEvidence(
                competency=rating.competency,
                observation="supported",
                source_quotes=list(
                    dict.fromkeys(span.quote for item in sources for span in item.quote_spans)
                ),
                fact="; ".join(item.normalized_claim for item in sources),
                rationale="Supported by the quoted behavior in this answer.",
                rubric_level=rating.assigned_level,
                strength=strength,
            )
        )
    dimensions = grounded_dimensions(
        dimensions,
        request.answer.text,
        answer_segments(request.answer.answer_id, request.answer.text),
    )
    return validate_assessment(
        job,
        AssessmentRecord(
            **base,
            assessment_status="valid",
            dimensions=dimensions,
            evidence_strength=max((d.strength for d in dimensions), default=0.0),
        ),
    )


class FormalAssessmentAdapter:
    """Read saved formal results; background scheduling owns dependency readiness."""

    requires_formal = True

    def __init__(self, repository, legacy_assessment):
        self.repository = repository
        self.legacy_assessment = legacy_assessment

    async def assess(self, job):
        if not job.shadow_enabled:
            # Resume old legacy jobs using their original assessment contract.
            return await self.legacy_assessment.assess(job)
        records = await self.repository.get_evaluation_records(job.request.interview_id)
        record = next((r for r in records if r.input.request_id == job.request.request_id), None)
        if record is None:
            raise InvalidAgentState("Formal assessment requires a saved scoring receipt")
        return assessment_from_formal(job, record)
