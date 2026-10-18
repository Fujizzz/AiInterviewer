"""Criterion-local safety gates and one contribution per independent episode.

No levels are assigned here and no scores/weights are computed. Phase four can
consume this plan after judging and apply its versioned numeric policy.
"""

import hashlib
import json
from typing import Literal

from evaluation.contracts import CriterionAssessment, EvaluationModel, EvidenceItem, Text
from evaluation.resolution import EvidenceResolution
from evaluation.resolver import replay_resolution
from evaluation.rubric import RubricPack
from shared.contracts import Competency

SELECTION_POLICY_VERSION = "episode-selection-1.0.0"


class GroupContributionPlan(EvaluationModel):
    independence_group_id: Text
    evidence_id: Text
    assessment_id: Text
    supplemental_evidence_ids: tuple[Text, ...]
    reason_codes: tuple[Text, ...] = ("one_contribution_per_episode",)


class CriterionEligibility(EvaluationModel):
    competency: Competency
    criterion_id: Text
    groups: tuple[GroupContributionPlan, ...]
    has_unresolved_contradiction: bool
    reason_codes: tuple[Text, ...]

    @property
    def independent_evidence_count(self) -> int:
        return len(self.groups)


class EligibilityPlan(EvaluationModel):
    selection_policy_version: Literal["episode-selection-1.0.0"] = SELECTION_POLICY_VERSION
    assessments: tuple[CriterionAssessment, ...]
    criteria: tuple[CriterionEligibility, ...]


def _with_gate(assessment: CriterionAssessment, **changes) -> CriterionAssessment:
    data = {**assessment.model_dump(mode="json"), **changes}
    if CriterionAssessment.model_validate(data) == assessment:
        return assessment
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data["assessment_id"] = "gated-v1-" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return CriterionAssessment.model_validate(data)


def _quality(item: EvidenceItem) -> tuple[int, int, int]:
    """Transparent ordinal tie-break only; numeric effective weights are phase four."""
    return (
        {"concrete": 2, "partial": 1, "vague": 0}[item.specificity],
        {"personal": 3, "shared": 2, "team_only": 1, "unclear": 0}[item.ownership_scope],
        {"reported_experience": 2, "hypothetical": 1, "opinion": 0, "unclear": 0}[item.factuality],
    )


def plan_criterion_contributions(
    assessments: tuple[CriterionAssessment, ...],
    resolution: EvidenceResolution,
    *,
    rubric: RubricPack,
) -> EligibilityPlan:
    # Derived states supplied by callers are never grounding/eligibility authority.
    resolution = replay_resolution(resolution.history)
    items = {item.evidence_id: item for item in resolution.evidence_items}
    states = {state.evidence_id: state for state in resolution.states}
    seen, gated = set(), []
    for original in assessments:
        assessment = CriterionAssessment.model_validate(original.model_dump())
        if assessment.assessment_id in seen:
            raise ValueError("assessment IDs must be unique")
        seen.add(assessment.assessment_id)
        rubric.validate_assessment(assessment)
        references = set(assessment.evidence_ids) | set(assessment.counter_evidence_ids)
        if not references <= items.keys():
            raise ValueError("assessment references unknown evidence")
        disputed = sorted(key for key in references if states[key].status == "disputed")
        if disputed and assessment.decision in {"included", "disputed"}:
            # Keep both actual source sides, even if a judge put opposing claims in
            # the same supporting list. Sibling facts in this episode stay usable.
            opposing = set(states[disputed[0]].counter_evidence_ids)
            supporting = (set(assessment.evidence_ids) | {disputed[0]}) - opposing
            assessment = _with_gate(
                assessment,
                decision="disputed",
                assigned_level=None,
                matched_anchor_ids=(),
                evidence_ids=tuple(sorted(supporting)),
                counter_evidence_ids=tuple(
                    sorted((set(assessment.counter_evidence_ids) | opposing) - supporting)
                ),
                reason_codes=("unresolved_contradiction",),
                concise_rationale=(
                    "Related candidate source claims conflict; reassessment is required."
                ),
            )
        elif assessment.decision == "included":
            blocked = {states[key].status for key in references} & {
                "retracted",
                "insufficient",
            }
            if blocked:
                assessment = _with_gate(
                    assessment,
                    decision="insufficient",
                    assigned_level=None,
                    matched_anchor_ids=(),
                    reason_codes=tuple(
                        sorted(
                            "claim_retracted"
                            if status == "retracted"
                            else "independence_unresolved"
                            for status in blocked
                        )
                    ),
                    concise_rationale="The assessment relies on withdrawn or unresolved evidence.",
                )
            elif all(states[key].status == "duplicate" for key in assessment.evidence_ids):
                assessment = _with_gate(
                    assessment,
                    decision="excluded",
                    assigned_level=None,
                    matched_anchor_ids=(),
                    reason_codes=("duplicate_claim",),
                    concise_rationale="Repeated claims add no independent contribution.",
                )
        elif assessment.decision == "disputed":
            assessment = _with_gate(
                assessment,
                decision="insufficient",
                reason_codes=("reassessment_required",),
                concise_rationale="The conflict is resolved; a new judge assessment is required.",
            )
        gated.append(assessment)

    criteria = []
    for competency, criterion_id in sorted({(a.competency, a.criterion_id) for a in gated}):
        local = [a for a in gated if (a.competency, a.criterion_id) == (competency, criterion_id)]
        candidates: dict[str, list[tuple[EvidenceItem, CriterionAssessment]]] = {}
        for assessment in local:
            if assessment.decision == "included":
                for key in assessment.evidence_ids:
                    if states[key].status == "eligible":
                        item = items[key]
                        candidates.setdefault(item.independence_group_id, []).append(
                            (item, assessment)
                        )
        groups = []
        for group_id, choices in sorted(candidates.items()):
            item, assessment = sorted(
                choices,
                key=lambda pair: (
                    *(-part for part in _quality(pair[0])),
                    pair[0].evidence_id,
                    pair[1].assessment_id,
                ),
            )[0]
            if len({a.assigned_level for e, a in choices if e.evidence_id == item.evidence_id}) > 1:
                raise ValueError("one evidence item has conflicting levels for the same criterion")
            groups.append(
                GroupContributionPlan(
                    independence_group_id=group_id,
                    evidence_id=item.evidence_id,
                    assessment_id=assessment.assessment_id,
                    supplemental_evidence_ids=tuple(
                        sorted(
                            {
                                key
                                for a in local
                                for key in a.evidence_ids
                                if items[key].independence_group_id == group_id
                                and key != item.evidence_id
                                and states[key].status in {"eligible", "duplicate"}
                            }
                        )
                    ),
                )
            )
        disputed = any(a.decision == "disputed" for a in local)
        reasons = ("unresolved_contradiction",) if disputed else ()
        if not groups:
            reasons += ("no_independent_contribution",)
        criteria.append(
            CriterionEligibility(
                competency=competency,
                criterion_id=criterion_id,
                groups=tuple(groups),
                has_unresolved_contradiction=disputed,
                reason_codes=reasons,
            )
        )
    return EligibilityPlan(
        assessments=tuple(sorted(gated, key=lambda a: a.assessment_id)), criteria=tuple(criteria)
    )
