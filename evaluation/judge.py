"""Rubric-only model matching, bound to a freshly replayed source history."""

import hashlib
import json
from typing import Literal

from pydantic import Field

from app.providers.llm import StructuredLLM
from evaluation.contracts import CriterionAssessment, EvaluationModel, Level, Text, require_unique
from evaluation.model_calls import EvaluationModelClient, EvaluationStageError, load_prompt
from evaluation.resolution import EvidenceResolution, ResolutionHistory
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


class GroupRating(EvaluationModel):
    criterion_id: Text
    evidence_ids: tuple[Text, ...] = Field(min_length=1)
    assigned_level: Level | None = None
    decision: Literal["included", "insufficient", "excluded", "disputed"]
    rationale: Text = Field(max_length=240)
    counter_evidence_ids: tuple[Text, ...] = ()


class GroupRatings(EvaluationModel):
    group_id: Text
    ratings: tuple[GroupRating, ...] = ()


class GroupedJudgeDraft(EvaluationModel):
    groups: tuple[GroupRatings, ...]


def evidence_groups(resolution):
    groups = {}
    for item in sorted(resolution.evidence_items, key=lambda e: e.evidence_id):
        groups.setdefault(item.independence_group_id, []).append(item)
    return groups


def group_signatures(resolution):
    """A correction, group merge or changed conflict invalidates affected ratings."""
    states = {s.evidence_id: s for s in resolution.states}
    items = {e.evidence_id: e for e in resolution.evidence_items}
    signatures = {}
    for group, members in evidence_groups(resolution).items():
        keys = {e.evidence_id for e in members}
        counters = {k for key in keys for k in states[key].counter_evidence_ids}
        relevant = sorted(keys | counters)
        signatures[group] = content_digest(
            {
                "evidence": [items[k].model_dump(mode="json") for k in relevant],
                "states": [states[k].model_dump(mode="json") for k in relevant],
                "conflicts": [
                    c.model_dump(mode="json")
                    for c in resolution.conflicts
                    if c.evidence_id in keys or c.counter_evidence_id in keys
                ],
            }
        )
    return signatures


def grouped_payload(resolution, rubric, pending):
    """Short evidence IDs are scoped to a program-owned episode, never global."""
    groups = evidence_groups(resolution)
    states = {s.evidence_id: s for s in resolution.states}
    items = {e.evidence_id: e for e in resolution.evidence_items}
    aliases, payload_groups = {}, []
    for index, group in enumerate(sorted(pending), 1):
        key = f"g{index}"
        local = {f"e{i}": e.evidence_id for i, e in enumerate(groups[group], 1)}
        external = sorted(
            {c for eid in local.values() for c in states[eid].counter_evidence_ids}
            - set(local.values())
        )
        counters = {f"c{i}": eid for i, eid in enumerate(external, 1)}
        aliases[key] = (local, {**local, **counters})
        reverse = {eid: alias for alias, eid in {**local, **counters}.items()}

        def view(alias, eid, reverse=reverse):
            item, state = items[eid], states[eid]
            return {
                "id": alias,
                "quotes": [q.quote for q in item.quote_spans],
                "claim": item.normalized_claim,
                "kind": item.evidence_kind,
                "ownership": item.ownership_scope,
                "specificity": item.specificity,
                "factuality": item.factuality,
                "status": state.status,
                "counter_evidence_ids": [
                    reverse[c] for c in state.counter_evidence_ids if c in reverse
                ],
            }

        payload_groups.append(
            {
                "group_id": key,
                "evidence": [view(a, eid) for a, eid in local.items()],
                "counter_evidence": [view(a, eid) for a, eid in counters.items()],
            }
        )
    return {
        "judge_version": JUDGE_VERSION,
        "criteria": [
            {
                "criterion_id": c.criterion_id,
                "description": c.description,
                "anchors": {str(a.level): a.behavior for a in c.anchors},
            }
            for r in rubric.rubrics
            for c in r.criteria
        ],
        "groups": payload_groups,
    }, aliases


def expand_groups(output, aliases, rubric):
    """Resolve references only within their episode; never split/copy mixed scores."""
    require_unique([g.group_id for g in output.groups], "judge groups")
    if {g.group_id for g in output.groups} != aliases.keys():
        raise ValueError("judge must return exactly the requested groups")
    criteria = {c.criterion_id: r.competency for r in rubric.rubrics for c in r.criteria}
    assessments, unmapped = [], []
    for group in output.groups:
        local, counters = aliases[group.group_id]
        for rating in group.ratings:
            if rating.criterion_id not in criteria:
                raise ValueError("judge references unknown criterion")
            if not set(rating.evidence_ids) <= local.keys():
                raise ValueError("judge references evidence outside its group")
            if not set(rating.counter_evidence_ids) <= counters.keys():
                raise ValueError("judge references unknown counter evidence")
            assessments.append(
                AssessmentDraft(
                    competency=criteria[rating.criterion_id],
                    criterion_id=rating.criterion_id,
                    evidence_ids=tuple(local[k] for k in rating.evidence_ids),
                    assigned_level=rating.assigned_level,
                    matched_anchor_ids=(f"{rating.criterion_id}.l{rating.assigned_level}",)
                    if rating.assigned_level is not None
                    else (),
                    decision=rating.decision,
                    reason_codes=(f"rubric_{rating.decision}",),
                    concise_rationale=rating.rationale,
                    counter_evidence_ids=tuple(counters[k] for k in rating.counter_evidence_ids),
                )
            )
        referenced = {k for a in assessments for k in (*a.evidence_ids, *a.counter_evidence_ids)}
        # Returning this group declares that unmentioned sources have no matching criterion.
        # Compute the complement once; the model cannot contradict its own reference list.
        unmapped.extend(
            UnmappedEvidence(
                evidence_id=eid,
                reason_code="no_relevant_criterion",
                concise_rationale="The judge returned no matching criterion for this source.",
            )
            for eid in local.values()
            if eid not in referenced
        )
    return assessments, unmapped


def complete_draft(assessments, unmapped, rubric):
    covered = {a.criterion_id for a in assessments}
    referenced = {k for a in assessments for k in (*a.evidence_ids, *a.counter_evidence_ids)}
    unmapped = [u for u in unmapped if u.evidence_id not in referenced]
    missing = [
        AssessmentDraft(
            competency=r.competency,
            criterion_id=c.criterion_id,
            decision="insufficient",
            reason_codes=("no_evidence",),
            concise_rationale="No candidate evidence for this criterion.",
        )
        for r in rubric.rubrics
        for c in r.criteria
        if c.criterion_id not in covered
    ]
    return JudgeDraft(assessments=(*assessments, *missing), unmapped_evidence=tuple(unmapped))


class RubricJudge:
    def __init__(self, llm: StructuredLLM, *, timeout_seconds: float = 30) -> None:
        self._client = EvaluationModelClient(llm, timeout_seconds=timeout_seconds)

    async def judge(
        self,
        resolution: EvidenceResolution,
        *,
        rubric: RubricPack,
        previous: RubricJudgement | None = None,
        previous_history: ResolutionHistory | None = None,
    ) -> RubricJudgement:
        assessments, unmapped, reusable = [], [], set()
        try:
            resolution = replay_resolution(resolution.history)
            rubric = RubricPack.model_validate(rubric.model_dump())
            if previous is not None and previous.rubric_digest == content_digest(rubric):
                if previous_history is None:
                    raise ValueError("cached judgement requires its original history")
                prior = replay_resolution(previous_history)
                size = len(prior.history.sources)
                if (
                    prior.history.interview_id != resolution.history.interview_id
                    or resolution.history.sources[:size] != prior.history.sources
                    or resolution.history.decisions[:size] != prior.history.decisions
                ):
                    raise ValueError("cached judgement must precede this resolution")
                previous = validate_judgement(previous, prior, rubric=rubric)
                before, after = group_signatures(prior), group_signatures(resolution)
                reusable = {g for g in after if after[g] == before.get(g)}
                items = {e.evidence_id: e for e in prior.evidence_items}
                # Counter-evidence changes also invalidate the dependent assessment.
                reusable -= {
                    items[k].independence_group_id
                    for a in previous.assessments
                    for k in a.evidence_ids
                    if any(
                        items[c].independence_group_id not in reusable
                        for c in a.counter_evidence_ids
                    )
                }
                assessments = [
                    AssessmentDraft.model_validate(
                        a.model_dump(exclude={"schema_version", "assessment_id", "rubric_version"})
                    )
                    for a in previous.assessments
                    if a.evidence_ids and items[a.evidence_ids[0]].independence_group_id in reusable
                ]
                unmapped = [
                    u
                    for u in previous.unmapped_evidence
                    if items[u.evidence_id].independence_group_id in reusable
                ]
        except ValueError as error:
            raise EvaluationStageError("judge", "invalid_evidence") from error
        pending = set(evidence_groups(resolution)) - reusable
        if pending:
            payload, aliases = grouped_payload(resolution, rubric, pending)
            output = await self._client.call(
                stage="judge",
                prompt=load_prompt("rubric_judge_v2"),
                schema=GroupedJudgeDraft,
                payload=payload,
            )
            try:
                current, gaps = expand_groups(output, aliases, rubric)
                assessments.extend(current)
                unmapped.extend(gaps)
            except ValueError as error:
                raise EvaluationStageError("judge", "invalid_assessment") from error
        try:
            return bind_judgement(
                complete_draft(assessments, unmapped, rubric), resolution, rubric=rubric
            )
        except ValueError as error:
            raise EvaluationStageError("judge", "invalid_assessment") from error
