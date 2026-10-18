"""Small model view; all durable IDs and original evidence stay on the server."""

import re

from agents.planning.boundaries import asks_private_model_detail, private_model_boundary
from agents.question.compact import terms
from agents.tracing import emit_trace
from app.adapters.decision import DetailedAnswerDecision
from app.adapters.evaluation import InvalidEvaluationEvidence, answer_segments


class DecisionContext:
    def __init__(self, request, context, segments, objectives, history):
        question = request.question
        wanted = terms(request.answer.text + " " + question.information_goal)
        generic = terms(
            "assess describe explain implementation validation personal concrete evidence"
        )
        current = [
            o
            for o in objectives
            if o["topic_key"] == question.topic_key or o["objective_id"] == question.objective_id
        ]
        related = sorted(
            (o for o in objectives if o not in current),
            key=lambda o: (
                -len(
                    (terms(o["objective"] + " " + o["completion_criteria"]) - generic)
                    & terms(request.answer.text)
                )
            ),
        )
        related = [
            o
            for o in related
            if len(
                (terms(o["objective"] + " " + o["completion_criteria"]) - generic)
                & terms(request.answer.text)
            )
            >= 2
        ]
        self.objectives = [*current, *related[:1]] or objectives[:1]
        protected = set()
        for o in self.objectives:
            for proof in [
                *o["accepted_evidence"],
                *(p for c in o["completion_requirements"] for p in c["evidence"]),
            ]:
                protected.update(proof.get("supporting_segment_ids", []))

        # Keep the current conversation intact, selected-goal evidence, and matching
        # facts from older threads. Unrelated project evidence is not re-audited.
        recent = {e.answer.answer_id for e in history[-2:] if e.answer}
        rows = []
        matching = []
        self.prior = {}
        for e in context.question_history:
            if not e.answer or e.question.project_id != question.project_id:
                continue
            for s in answer_segments(e.answer.answer_id, e.answer.text):
                if e.answer.answer_id in recent or s["id"] in protected:
                    rows.append((e, s))
                else:
                    score = len(wanted & terms(s["text"]))
                    if score >= 2:
                        matching.append((score, e, s))
        rows.extend((e, s) for _, e, s in sorted(matching, key=lambda v: -v[0])[:6])
        self.current = {f"s{i}": s for i, s in enumerate(segments)}
        self.explicit_correction = bool(
            re.search(
                r"\b(?:correction:|i (?:need to |must )?correct (?:my|the earlier)|"
                r"i (?:misspoke|was wrong)|my (?:earlier|previous) (?:statement|answer) "
                r"was (?:wrong|incorrect))|更正(?:一下|之前|先前)|"
                r"我(?:之前|刚才|先前).{0,12}(?:说错|不准确)",
                request.answer.text,
                re.IGNORECASE,
            )
        )
        self.previous = {}
        seen = set()
        for e, s in rows:
            if s["id"] in seen:
                continue
            seen.add(s["id"])
            self.previous[f"h{len(self.previous)}"] = s
            self.prior[e.answer.answer_id] = e
        reverse = {s["id"]: k for k, s in self.previous.items()}
        self.criteria = {}
        goals = []
        for o in self.objectives:
            requirements = []
            for c in o["completion_requirements"]:
                key = f"c{len(self.criteria)}"
                self.criteria[key] = (o, c)
                row = {"id": key, "requirement": c["requirement"], "status": c["coverage_status"]}
                if c["missing_information"]:
                    row["gaps"] = c["missing_information"]
                refs = list(
                    dict.fromkeys(
                        reverse[s]
                        for p in c["evidence"]
                        for s in p.get("supporting_segment_ids", [])
                        if s in reverse
                    )
                )
                if refs:
                    row["proof"] = refs
                requirements.append(row)
            goals.append({"goal": o["objective"], "criteria": requirements})
        progress = getattr(context, "topic_progress", {}).get(question.topic_key)
        self.needs = {
            f"n{i}": n
            for i, n in enumerate(getattr(progress, "information_needs", []))
            if n.status == "blocked"
            or (
                n.status == "open"
                and getattr(progress, "needs_assessed", False)
                and n.objective_version == getattr(progress, "objective_version", 1)
            )
        }
        self.payload = {
            "question": question.text,
            "goal": question.information_goal,
            "answer_segments": [{"id": k, "text": s["text"]} for k, s in self.current.items()],
            "objectives": goals,
        }
        if self.previous:
            self.payload["previous_segments"] = [
                {"id": k, "text": s["text"]} for k, s in self.previous.items()
            ]
        if self.needs:
            self.payload["information_needs"] = [
                {"target": n.target, "status": n.status} for n in self.needs.values()
            ]

    def _segments(self, refs, *, prior=False):
        table = self.previous if prior else self.current
        restored = []
        for ref in refs:
            key = ref
            if key not in table and ":" in key:
                # Accept an annotated ID only when BOTH its ID and attached exact
                # quotation match supplied source. Never guess a missing alias.
                key, quote = key.split(":", 1)
                quote = quote.strip()
                if key not in table or not quote or quote not in table[key]["text"]:
                    raise InvalidEvaluationEvidence("INVALID_DECISION_SEGMENT_ALIAS")
            if key not in table:
                raise InvalidEvaluationEvidence("INVALID_DECISION_SEGMENT_ALIAS")
            restored.append(table[key]["id"])
        return restored

    def expand(self, raw):
        """Resolve aliases before existing source-grounding and completion projection."""
        coverage = {}
        self.issues = []
        limits = []
        for ref in raw.limits:
            try:
                limits.extend(self._segments([ref]))
            except InvalidEvaluationEvidence:
                # An optional malformed reference must not erase valid decision
                # deltas. Never reinterpret prose or an invented quote as proof.
                self.issues.append("INVALID_LIMITATION_SEGMENT_OMITTED")
                emit_trace("evaluation.limitation_omitted", reason="INVALID_LIMITATION_SEGMENT")
        for change in raw.criteria:
            if change.status == "unassessed":
                # Echoing an unchanged input status is a no-op, never an evidence
                # downgrade or a reason to throw away valid dialogue decisions.
                continue
            if change.id not in self.criteria:
                emit_trace("evaluation.criterion_omitted", reason="INVALID_CRITERION_ALIAS")
                self.issues.append("INVALID_COMPLETION_CRITERION_ID")
                continue
            objective, criterion = self.criteria[change.id]
            try:
                refs = self._segments(change.s)
            except InvalidEvaluationEvidence:
                emit_trace("evaluation.criterion_omitted", reason="INVALID_CRITERION_SEGMENT")
                self.issues.append("INVALID_COMPLETION_CRITERION_SEGMENT")
                continue
            item = coverage.setdefault(
                objective["objective_id"],
                dict(
                    objective_id=objective["objective_id"],
                    status="partial",
                    segments=[],
                    criteria=[],
                    missing=[],
                ),
            )
            item["segments"] = list(dict.fromkeys([*item["segments"], *refs]))
            item["criteria"].append(
                dict(
                    criterion_id=criterion["criterion_id"],
                    status=change.status,
                    segments=refs,
                    missing=[change.gap] if change.gap else [],
                )
            )
        needs = []
        local_boundaries = [
            key for key, s in self.current.items() if private_model_boundary(s["text"])
        ]
        previous_boundary = any(
            private_model_boundary(quote)
            for need in self.needs.values()
            if need.status == "blocked"
            for proof in need.blocked_evidence
            for quote in proof.get("supporting_quotes", [])
        )
        for n in raw.needs:
            status, sources = n.status, n.s
            if status == "open" and asks_private_model_detail(n.target):
                if local_boundaries:
                    # A semantic paraphrase cannot bypass the original explicit
                    # limitation. Ground the blocked target in that exact quote.
                    status, sources = "blocked", local_boundaries
                elif previous_boundary:
                    # Keep old blocked evidence rather than attaching it to this
                    # answer or fabricating a fresh candidate admission.
                    self.issues.append("UNAVAILABLE_NEED_OMITTED")
                    continue
            existing = next(
                (previous for previous in self.needs.values() if previous.target == n.target), None
            )
            needs.append(
                dict(
                    target=n.target,
                    status=status,
                    need_id=existing.need_id if existing else "",
                    segments=self._segments(sources),
                )
            )
        relations = []
        for r in raw.relations:
            try:
                context_segment = self._segments([r.context])[0] if r.context else None
            except InvalidEvaluationEvidence:
                self.issues.append("INVALID_FACT_CONTEXT_SEGMENT")
                # Invalid optional context cannot justify reconciling old facts.
                # Do not discard the independently grounded coverage/needs.
                continue
            kind = r.kind
            if kind == "supersedes" and not self.explicit_correction:
                kind = "clarifies" if r.compatibility == "compatible" else "disputes"
            relations.append(
                dict(
                    earlier_segment=self._segments([r.old], prior=True)[0],
                    current_segment=self._segments([r.current])[0],
                    kind=kind,
                    compatibility=r.compatibility,
                    different_context_segment=context_segment,
                )
            )
        return DetailedAnswerDecision(
            # Relevance is used by difficulty adjustment after background evidence
            # arrives; retain this small control signal, not capability grading.
            answer_relevance=raw.relevance,
            analysis=dict(
                status=raw.status,
                scope=raw.scope,
                new_information=raw.new,
                complete=raw.complete and "INVALID_LIMITATION_SEGMENT_OMITTED" not in self.issues,
                need=next((n["target"] for n in needs if n["status"] == "open"), None),
                limitation_segments=list(
                    dict.fromkeys([*limits, *self._segments(local_boundaries)])
                ),
            ),
            coverage=list(coverage.values()),
            needs=needs,
            relations=relations,
        )
