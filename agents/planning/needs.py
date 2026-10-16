"""Persist objective gaps and expose server-owned targets to the executor."""

from hashlib import sha256

from shared.contracts.planning import InformationNeed


def _target_key(text):
    return " ".join(text.casefold().split())


def open_needs(progress, topic_key):
    """Read legacy contexts without mutating a speculative question-agent snapshot."""
    if progress is None:
        return []
    if progress.information_needs or progress.needs_assessed:
        return [
            n
            for n in progress.information_needs
            if n.status == "open" and n.objective_version == progress.objective_version
        ]
    return [
        InformationNeed(
            need_id=(
                progress.next_need_id
                if index == 0 and progress.next_need_id
                else "need:"
                + sha256(f"{topic_key}:{progress.objective_version}:{target}".encode()).hexdigest()[
                    :20
                ]
            ),
            objective_id=progress.objective_id or topic_key,
            objective_version=progress.objective_version,
            target=target,
            answer_unit=target,
            source_answer_ids=progress.evidence_answer_ids,
        )
        for index, target in enumerate(progress.missing_information)
    ]


def sync_needs(progress, topic_key, *, source_answer_id=None):
    if progress.needs_assessed:
        # Coverage gaps describe absent evidence, not permission to ask. Only the
        # separately assessed targets may enter the executable inventory.
        current = open_needs(progress, topic_key)
        ids = {need.need_id for need in current}
        if progress.next_need_id not in ids:
            progress.next_need_id = current[0].need_id if current else None
        return
    existing = open_needs(progress, topic_key)
    # Migration happens once; later updates retire needs explicitly.
    if not progress.information_needs:
        progress.information_needs = existing
    by_target = {
        n.target: n
        for n in progress.information_needs
        if n.objective_version == progress.objective_version
    }
    missing = set(progress.missing_information)
    for need in existing:
        if need.target not in missing:
            need.status = "satisfied" if progress.coverage_status == "sufficient" else "superseded"
    for target in progress.missing_information:
        need = by_target.get(target)
        if need is None:
            need = InformationNeed(
                need_id="need:"
                + sha256(f"{topic_key}:{progress.objective_version}:{target}".encode()).hexdigest()[
                    :20
                ],
                objective_id=progress.objective_id or topic_key,
                objective_version=progress.objective_version,
                target=target,
                answer_unit=target,
            )
            progress.information_needs.append(need)
        elif need.status != "blocked":
            need.status = "open"
        if source_answer_id and source_answer_id not in need.source_answer_ids:
            need.source_answer_ids.append(source_answer_id)
    current = {n.target: n for n in open_needs(progress, topic_key)}
    progress.next_need_id = next(
        (current[t].need_id for t in progress.missing_information if t in current), None
    )


def apply_need_updates(progress, topic_key, analysis, answer):
    """Persist scoped boundaries; later coverage/budget updates cannot reopen them."""
    if not analysis.needs_assessed:
        return
    if any(
        update.answer_id != answer.answer_id
        or any(not quote or quote not in answer.text for quote in update.supporting_quotes)
        or (update.status == "blocked" and not update.supporting_quotes)
        for update in analysis.need_updates
    ):
        return  # Invalid scoped evidence must not erase the previous executable inventory.
    progress.needs_assessed = True
    for need in open_needs(progress, topic_key):
        need.status = "superseded"
    current = {n.need_id: n for n in progress.information_needs}
    blocked = {
        _target_key(n.target): n for n in progress.information_needs if n.status == "blocked"
    }
    for update in analysis.need_updates:
        if update.answer_id != answer.answer_id:
            continue
        if any(not quote or quote not in answer.text for quote in update.supporting_quotes):
            continue
        if update.status == "blocked" and not update.supporting_quotes:
            continue
        known = current.get(update.need_id) if update.need_id else None
        if update.need_id and (
            known is None or _target_key(known.target) != _target_key(update.target)
        ):
            continue  # Reusing an ID cannot change the information target.
        need = next(
            (
                n
                for n in progress.information_needs
                if n.objective_version == progress.objective_version
                and _target_key(n.target) == _target_key(update.target)
            ),
            None,
        )
        if need is None:
            need = InformationNeed(
                need_id="need:"
                + sha256(
                    f"{topic_key}:{progress.objective_version}:{_target_key(update.target)}".encode()
                ).hexdigest()[:20],
                objective_id=progress.objective_id or topic_key,
                objective_version=progress.objective_version,
                target=update.target,
                answer_unit=update.target,
            )
            progress.information_needs.append(need)
        prior = blocked.get(_target_key(need.target))
        need.status = "blocked" if update.status == "blocked" or prior else "open"
        if answer.answer_id not in need.source_answer_ids:
            need.source_answer_ids.append(answer.answer_id)
        if prior and prior is not need:
            need.blocked_evidence = list(prior.blocked_evidence)
        if update.status == "blocked":
            proof = {
                "answer_id": answer.answer_id,
                "supporting_quotes": list(update.supporting_quotes),
                "supporting_segment_ids": list(update.supporting_segment_ids),
            }
            if proof not in need.blocked_evidence:
                need.blocked_evidence.append(proof)
            blocked[_target_key(need.target)] = need
    progress.next_need_id = None
    sync_needs(progress, topic_key)


def askable_targets(progress, analysis=None):
    if progress and progress.needs_assessed:
        return [need.target for need in open_needs(progress, progress.objective_id)]
    if analysis and analysis.needs_assessed:
        return [need.target for need in analysis.need_updates if need.status == "open"]
    return (progress.missing_information if progress else []) or (
        analysis.missing_information if analysis else []
    )


def replace_objective(progress):
    for need in progress.information_needs:
        if need.status == "open":
            need.status = "superseded"
    progress.objective_version += 1
    progress.missing_information = []
    progress.next_need_id = None
    progress.coverage_status = "unassessed"
    progress.coverage_evidence = []
    progress.evidence_answer_ids = []
    progress.completion_requirements = []
