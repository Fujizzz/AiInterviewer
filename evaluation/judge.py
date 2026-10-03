"""Rubric-only model matching, bound to a freshly replayed source history."""

import hashlib
import json
from typing import Literal

from pydantic import Field

from app.providers.llm import StructuredLLM
from evaluation.contracts import CriterionAssessment, EvaluationModel, Level, Text, require_unique
from evaluation.model_calls import EvaluationModelClient, EvaluationStageError, load_prompt
from evaluation.resolution import EvidenceResolution
from evaluation.resolver import replay_resolution
from evaluation.rubric import RubricPack
from shared.contracts import Competency

JUDGE_VERSION = "judge-1.0.0"


def content_digest(value) -> str:
    if isinstance(value, EvaluationModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AssessmentDraft(EvaluationModel):
    competency: Competency
    criterion_id: Text
    evidence_ids: tuple[Text, ...] = ()
    assigned_level: Level | None = None
    matched_anchor_ids: tuple[Text, ...] = ()
    decision: Literal["included", "insufficient", "excluded", "disputed"]
    reason_codes: tuple[Text, ...] = Field(min_length=1)
    concise_rationale: Text = Field(max_length=1000)
    counter_evidence_ids: tuple[Text, ...] = ()


class UnmappedEvidence(EvaluationModel):
    evidence_id: Text
    reason_code: Literal["no_relevant_criterion"]
    concise_rationale: Text = Field(max_length=1000)


class JudgeDraft(EvaluationModel):
    assessments: tuple[AssessmentDraft, ...]
    unmapped_evidence: tuple[UnmappedEvidence, ...] = ()


class RubricJudgement(EvaluationModel):
    judge_version: Literal["judge-1.0.0"] = JUDGE_VERSION
    resolution_digest: Text
    rubric_digest: Text
    assessments: tuple[CriterionAssessment, ...]
    unmapped_evidence: tuple[UnmappedEvidence, ...] = ()


def bind_judgement(
    draft: JudgeDraft, resolution: EvidenceResolution, *, rubric: RubricPack
) -> RubricJudgement:
    """Validate semantic references; no model-generated identities or totals accepted."""
    draft = JudgeDraft.model_validate(draft.model_dump())
    resolution = replay_resolution(resolution.history)
    rubric = RubricPack.model_validate(rubric.model_dump())
    history_digest, rubric_digest = content_digest(resolution.history), content_digest(rubric)
    items = {item.evidence_id: item for item in resolution.evidence_items}
    expected = {c.criterion_id for r in rubric.rubrics for c in r.criteria}
    seen_pairs, references, assessments = set(), set(), []
    for item in draft.assessments:
        data = item.model_dump(mode="json")
        for name in ("evidence_ids", "matched_anchor_ids", "counter_evidence_ids", "reason_codes"):
            require_unique(data[name], name)
            data[name] = sorted(data[name])
        identity = dict(
            judge_version=JUDGE_VERSION,
            resolution_digest=history_digest,
            rubric_digest=rubric_digest,
            assessment=data,
        )
        assessment = CriterionAssessment(
            **data,
            rubric_version=rubric.rubric_version,
            assessment_id="assessment-v1-" + content_digest(identity),
        )
        rubric.validate_assessment(assessment)
        refs = set(assessment.evidence_ids) | set(assessment.counter_evidence_ids)
        if not refs <= items.keys():
            raise ValueError("judge references unknown evidence")
        if not assessment.evidence_ids and assessment.decision != "insufficient":
            raise ValueError("an empty criterion must be insufficient")
        # One level belongs to one episode; independent episodes need separate judgements.
        groups = {items[key].independence_group_id for key in assessment.evidence_ids}
        if len(groups) > 1:
            raise ValueError("an assessment must refer to one independence group")
        pair = (assessment.criterion_id, next(iter(groups), None))
        if pair in seen_pairs:
            raise ValueError("duplicate criterion/episode assessment")
        seen_pairs.add(pair)
        references.update(refs)
        assessments.append(assessment)
    if {a.criterion_id for a in assessments} != expected:
        raise ValueError("judge must explicitly assess every rubric criterion")
    for criterion in expected:
        local = [a for a in assessments if a.criterion_id == criterion]
        if len(local) > 1 and any(not a.evidence_ids for a in local):
            raise ValueError("empty criterion assessment cannot coexist with evidence")
    unmapped = [item.evidence_id for item in draft.unmapped_evidence]
    require_unique(unmapped, "unmapped evidence")
    if set(unmapped) & references or set(unmapped) | references != items.keys():
        raise ValueError("every source must be assessed or explicitly unmapped")
    return RubricJudgement(
        resolution_digest=history_digest,
        rubric_digest=rubric_digest,
        assessments=tuple(sorted(assessments, key=lambda a: a.assessment_id)),
        unmapped_evidence=tuple(sorted(draft.unmapped_evidence, key=lambda a: a.evidence_id)),
    )


def validate_judgement(
    judgement: RubricJudgement, resolution: EvidenceResolution, *, rubric: RubricPack
) -> RubricJudgement:
    judgement = RubricJudgement.model_validate(judgement.model_dump())
    draft = JudgeDraft(
        assessments=tuple(
            AssessmentDraft.model_validate(
                a.model_dump(
                    exclude={
                        "schema_version",
                        "assessment_id",
                        "rubric_version",
                    }
                )
            )
            for a in judgement.assessments
        ),
        unmapped_evidence=judgement.unmapped_evidence,
    )
    rebuilt = bind_judgement(draft, resolution, rubric=rubric)
    if rebuilt != judgement:
        raise ValueError("judgement is stale, tampered or bound to different sources/rubric")
    return rebuilt


class RubricJudge:
    def __init__(self, llm: StructuredLLM, *, timeout_seconds: float = 30) -> None:
        self._client = EvaluationModelClient(llm, timeout_seconds=timeout_seconds)

    async def judge(self, resolution: EvidenceResolution, *, rubric: RubricPack) -> RubricJudgement:
        try:
            resolution = replay_resolution(resolution.history)
            rubric = RubricPack.model_validate(rubric.model_dump())
        except ValueError as error:
            raise EvaluationStageError("judge", "invalid_evidence") from error
        if not resolution.evidence_items:
            draft = JudgeDraft(
                assessments=tuple(
                    AssessmentDraft(
                        competency=r.competency,
                        criterion_id=c.criterion_id,
                        decision="insufficient",
                        reason_codes=("no_evidence",),
                        concise_rationale="No candidate evidence is available for this criterion.",
                    )
                    for r in rubric.rubrics
                    for c in r.criteria
                )
            )
        else:
            draft = await self._client.call(
                stage="judge",
                prompt=load_prompt("rubric_judge_v1"),
                schema=JudgeDraft,
                payload={
                    "judge_version": JUDGE_VERSION,
                    "rubric": rubric.model_dump(mode="json"),
                    "evidence": [
                        item.model_dump(mode="json") for item in resolution.evidence_items
                    ],
                    "states": [item.model_dump(mode="json") for item in resolution.states],
                    "conflicts": [item.model_dump(mode="json") for item in resolution.conflicts],
                },
            )
        try:
            return bind_judgement(draft, resolution, rubric=rubric)
        except ValueError as error:
            raise EvaluationStageError("judge", "invalid_assessment") from error
