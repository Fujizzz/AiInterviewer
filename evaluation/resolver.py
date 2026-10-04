"""Semantic relation proposals followed by deterministic provenance/graph gates.

Replay never calls a model. The model may propose episode identity and semantic
relations, but cannot choose group IDs, eligibility, levels, weights or scores.
"""

import hashlib
import json

from app.providers.llm import StructuredLLM
from evaluation.contracts import EvidenceItem, EvidenceRelation, require_unique
from evaluation.inputs import EvaluationInput, ThreadTurn
from evaluation.model_calls import EvaluationModelClient, EvaluationStageError, load_prompt
from evaluation.resolution import (
    EvidenceConflict,
    EvidenceResolution,
    EvidenceSource,
    EvidenceState,
    RelationDecision,
    ResolutionDraft,
    ResolutionHistory,
)
from evaluation.validator import normalize_claim, validate_evidence


class _Groups:
    def __init__(self, identifiers: list[str]):
        self.parent = dict.fromkeys(identifiers)
        self.order = {identifier: index for index, identifier in enumerate(identifiers)}

    def root(self, identifier: str) -> str:
        parent = self.parent[identifier]
        if parent is not None:
            self.parent[identifier] = self.root(parent)
            return self.parent[identifier]
        return identifier

    def join(self, left: str, right: str) -> None:
        roots = sorted({self.root(left), self.root(right)}, key=self.order.__getitem__)
        if len(roots) == 2:
            self.parent[roots[1]] = roots[0]


def _group_id(interview_id: str, root_id: str) -> str:
    value = json.dumps([interview_id, root_id], ensure_ascii=False, separators=(",", ":"))
    return "independence-v1-" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def replay_resolution(history: ResolutionHistory) -> EvidenceResolution:
    """Validate a complete history and derive a fresh, immutable eligibility view.

    Caller supplies chronological sources; current-answer sources are sorted by
    span/ID by EvidenceResolver. All edges point backwards, so cycles, missing
    targets, self references and references to future evidence fail closed.
    """
    history = ResolutionHistory.model_validate(history.model_dump())
    sources = history.sources
    ids = [source.evidence.evidence_id for source in sources]
    require_unique(ids, "source evidence IDs")
    require_unique([decision.evidence_id for decision in history.decisions], "decision IDs")
    decisions = {decision.evidence_id: decision for decision in history.decisions}
    if set(decisions) != set(ids):
        raise ValueError("exactly one decision is required for every source")
    items = {}
    turns = {}
    questions = {}
    for source in sources:
        item = validate_evidence(source.evidence, source.turn, interview_id=history.interview_id)
        if item.relation != EvidenceRelation.NEW or item.independence_group_id is not None:
            raise ValueError("history sources must be raw, unresolved extraction records")
        if item.answer_id in turns and source.turn != turns[item.answer_id]:
            raise ValueError("an answer ID cannot identify different source snapshots")
        if item.question_id in questions and source.turn.question != questions[item.question_id]:
            raise ValueError("a question ID cannot identify different question snapshots")
        turns[item.answer_id] = source.turn
        questions[item.question_id] = source.turn.question
        items[item.evidence_id] = item

    episodes = _Groups(ids)
    duplicates = _Groups(ids)
    canonical = {key: normalize_claim(item.normalized_claim) for key, item in items.items()}
    effective: dict[str, RelationDecision] = {}
    for source in sources:
        item = source.evidence
        key = item.evidence_id
        decision = decisions[key]
        targets = set(decision.related_evidence_ids) | set(decision.same_episode_as)
        if not targets <= effective.keys():
            raise ValueError("relation and episode targets must be earlier grounded evidence")
        for target in decision.same_episode_as:
            prior = items[target]
            if item.project_id != prior.project_id:
                raise ValueError("the same episode must have the same project scope")
            episodes.join(key, target)
        for target in decision.related_evidence_ids:
            if effective[target].relation == EvidenceRelation.RETRACTS:
                raise ValueError("a retraction act cannot serve as a factual relation target")
            if decision.relation in {EvidenceRelation.CONTRADICTS, EvidenceRelation.RETRACTS}:
                if canonical[key] == canonical[target] or (
                    tuple(normalize_claim(span.quote) for span in item.quote_spans)
                    == tuple(normalize_claim(span.quote) for span in items[target].quote_spans)
                ):
                    raise ValueError("contradiction/retraction requires two distinct source claims")
        if decision.retraction_span:
            proof = decision.retraction_span
            proof.validate_answer_text(source.turn.answer.text)
            if not any(
                span.char_start <= proof.char_start and proof.char_end <= span.char_end
                for span in item.quote_spans
            ):
                raise ValueError("retraction proof must lie within the current evidence quotes")

        # Exact normalized repeats in the same episode are duplicates even when a
        # model calls them new/refining/supporting evidence. Within one answer,
        # overlapping identical claims cannot manufacture a second episode.
        matches = [
            prior
            for prior in effective
            if canonical[prior] == canonical[key]
            and effective[prior].relation != EvidenceRelation.RETRACTS
            and (
                episodes.root(prior) == episodes.root(key)
                or (
                    items[prior].answer_id == item.answer_id
                    and any(
                        max(a.char_start, b.char_start) < min(a.char_end, b.char_end)
                        for a in items[prior].quote_spans
                        for b in item.quote_spans
                    )
                )
            )
        ]
        if matches and decision.relation not in {
            EvidenceRelation.CONTRADICTS,
            EvidenceRelation.RETRACTS,
        }:
            decision = RelationDecision(
                evidence_id=key,
                relation=EvidenceRelation.DUPLICATE,
                related_evidence_ids=tuple(matches),
                independence="same_episode",
                same_episode_as=tuple(sorted(set(matches) | set(decision.same_episode_as))),
                concise_rationale="Exact normalized claim repeats an existing episode claim.",
            )
            for target in matches:
                episodes.join(key, target)
        if decision.relation == EvidenceRelation.DUPLICATE:
            for target in decision.related_evidence_ids:
                duplicates.join(key, target)
        effective[key] = decision

    # Retraction is targeted, never a last-write-wins reset of the whole episode.
    withdrawn = {
        target
        for decision in effective.values()
        if decision.relation == EvidenceRelation.RETRACTS
        for target in decision.related_evidence_ids
    }
    while True:
        roots = {duplicates.root(key) for key in withdrawn}
        expanded = withdrawn | {key for key in ids if duplicates.root(key) in roots}
        expanded |= {
            key
            for key, decision in effective.items()
            if decision.relation == EvidenceRelation.REFINES
            and set(decision.related_evidence_ids) & expanded
        }
        if expanded == withdrawn:
            break
        withdrawn = expanded

    conflicts = tuple(
        EvidenceConflict(
            evidence_id=key,
            counter_evidence_id=target,
            status="resolved_by_retraction"
            if key in withdrawn or target in withdrawn
            else "unresolved",
        )
        for key, decision in effective.items()
        if decision.relation == EvidenceRelation.CONTRADICTS
        for target in sorted(decision.related_evidence_ids)
    )
    if any(
        duplicates.root(conflict.evidence_id) == duplicates.root(conflict.counter_evidence_id)
        for conflict in conflicts
    ):
        raise ValueError("contradictory claims cannot also be duplicate aliases")
    counters: dict[str, set[str]] = {key: set() for key in ids}
    for conflict in conflicts:
        if conflict.status == "unresolved":
            counters[conflict.evidence_id].add(conflict.counter_evidence_id)
            counters[conflict.counter_evidence_id].add(conflict.evidence_id)
    # Duplicate aliases and dependent refinements cannot launder disputed claims.
    while True:
        changed = False
        for key, decision in effective.items():
            linked = {prior for prior in ids if duplicates.root(prior) == duplicates.root(key)}
            if decision.relation == EvidenceRelation.REFINES:
                linked.update(decision.related_evidence_ids)
            inherited = set().union(*(counters[prior] for prior in linked)) - {key}
            if not inherited <= counters[key]:
                counters[key].update(inherited)
                changed = True
        if not changed:
            break

    uncertain_groups = {
        episodes.root(key)
        for key, decision in effective.items()
        if decision.independence == "unresolved"
    }
    resolved, states = [], []
    for key, decision in effective.items():
        status, reasons = "eligible", ("grounded_claim", "episode_resolved")
        if key in withdrawn or decision.relation == EvidenceRelation.RETRACTS:
            status, reasons = "retracted", ("claim_retracted",)
        elif counters[key]:
            status, reasons = "disputed", ("unresolved_contradiction",)
        elif episodes.root(key) in uncertain_groups:
            status, reasons = "insufficient", ("independence_unresolved",)
        elif decision.relation == EvidenceRelation.DUPLICATE:
            status, reasons = "duplicate", ("duplicate_claim",)
        resolved.append(
            EvidenceItem.model_validate(
                {
                    **items[key].model_dump(),
                    "relation": decision.relation,
                    "related_evidence_ids": tuple(sorted(decision.related_evidence_ids)),
                    "independence_group_id": _group_id(history.interview_id, episodes.root(key)),
                }
            )
        )
        states.append(
            EvidenceState(
                evidence_id=key,
                canonical_claim=canonical[key],
                status=status,
                reason_codes=reasons,
                counter_evidence_ids=tuple(sorted(counters[key])) if status == "disputed" else (),
            )
        )
    return EvidenceResolution(
        history=history, evidence_items=tuple(resolved), states=tuple(states), conflicts=conflicts
    )


class EvidenceResolver:
    def __init__(self, llm: StructuredLLM, *, timeout_seconds: float = 30) -> None:
        self._client = EvaluationModelClient(llm, timeout_seconds=timeout_seconds)
        self._prompt = load_prompt("evidence_resolver_v1")

    async def resolve(
        self,
        context: EvaluationInput,
        evidence: tuple[EvidenceItem, ...],
        *,
        history: ResolutionHistory | None = None,
    ) -> EvidenceResolution:
        try:
            context = EvaluationInput.model_validate(context.model_dump())
            history = history or ResolutionHistory(interview_id=context.interview_id)
            previous = replay_resolution(history)
            history = previous.history
            if history.interview_id != context.interview_id:
                raise ValueError("resolution history belongs to a different interview")
            prior = {item.evidence_id: item for item in previous.evidence_items}
            historical_turns = {
                source.turn.answer.answer_id: source.turn for source in history.sources
            }
            for prior_turn in context.history:
                if (
                    prior_turn.answer.answer_id in historical_turns
                    and prior_turn != historical_turns[prior_turn.answer.answer_id]
                ):
                    raise ValueError(
                        "context and resolver history disagree about an original answer"
                    )
            # The compact extraction index is never sufficient grounding authority.
            for entry in context.existing_evidence:
                if entry.evidence_id not in prior or any(
                    getattr(prior[entry.evidence_id], name) != value
                    for name, value in entry.model_dump().items()
                ):
                    raise ValueError("every extraction index entry needs its full original source")
            turn = ThreadTurn(question=context.question, answer=context.answer)
            current = tuple(
                sorted(
                    (
                        validate_evidence(item, turn, interview_id=context.interview_id)
                        for item in evidence
                    ),
                    key=lambda item: (item.quote_spans[0].char_start, item.evidence_id),
                )
            )
            require_unique([item.evidence_id for item in current], "current evidence IDs")
            if any(item.answer_id == context.answer.answer_id for item in prior.values()):
                raise ValueError("resolve a request against its pre-answer history when replaying")
            if any(
                item.evidence_id in prior
                or item.relation != EvidenceRelation.NEW
                or item.independence_group_id is not None
                for item in current
            ):
                raise ValueError("current evidence must be new raw extraction records")
        except ValueError as error:
            raise EvaluationStageError("resolver", "invalid_evidence") from error
        if not current:
            return previous
        output = await self._client.call(
            stage="resolver",
            prompt=self._prompt,
            payload={
                "question": context.question.model_dump(mode="json"),
                "answer": context.answer.model_dump(mode="json"),
                "current_evidence": [item.model_dump(mode="json") for item in current],
                "history": [source.model_dump(mode="json") for source in history.sources],
                "resolved_history": [
                    item.model_dump(mode="json") for item in previous.evidence_items
                ],
            },
            schema=ResolutionDraft,
        )
        try:
            if {decision.evidence_id for decision in output.decisions} != {
                item.evidence_id for item in current
            }:
                raise ValueError("model must resolve exactly the current evidence batch")
            proposed = {decision.evidence_id: decision for decision in output.decisions}
            require_unique([decision.evidence_id for decision in output.decisions], "decision IDs")
            updated = ResolutionHistory(
                interview_id=context.interview_id,
                sources=(
                    *history.sources,
                    *(EvidenceSource(evidence=item, turn=turn) for item in current),
                ),
                decisions=(*history.decisions, *(proposed[item.evidence_id] for item in current)),
            )
            return replay_resolution(updated)
        except ValueError as error:
            raise EvaluationStageError("resolver", "invalid_relations") from error
