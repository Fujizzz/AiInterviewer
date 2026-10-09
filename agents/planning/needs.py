"""Persist objective gaps and expose server-owned targets to the executor."""

from hashlib import sha256

from shared.contracts.planning import InformationNeed


def open_needs(progress, topic_key):
    """Read legacy contexts without mutating a speculative question-agent snapshot."""
    if progress is None:
        return []
    if progress.information_needs:
        return [n for n in progress.information_needs if n.status == "open"
                and n.objective_version == progress.objective_version]
    return [InformationNeed(
        need_id=(progress.next_need_id if index == 0 and progress.next_need_id else
                 "need:" + sha256(f"{topic_key}:{progress.objective_version}:{target}".encode()).hexdigest()[:20]),
        objective_id=progress.objective_id or topic_key,
        objective_version=progress.objective_version,
        target=target, answer_unit=target,
        source_answer_ids=progress.evidence_answer_ids,
    ) for index, target in enumerate(progress.missing_information)]


def sync_needs(progress, topic_key, *, source_answer_id=None):
    existing = open_needs(progress, topic_key)
    # Migration happens once; later updates retire needs explicitly.
    if not progress.information_needs:
        progress.information_needs = existing
    by_target = {n.target: n for n in progress.information_needs
                 if n.objective_version == progress.objective_version}
    missing = set(progress.missing_information)
    for need in existing:
        if need.target not in missing:
            need.status = "satisfied" if progress.coverage_status == "sufficient" else "superseded"
    for target in progress.missing_information:
        need = by_target.get(target)
        if need is None:
            need = InformationNeed(
                need_id="need:" + sha256(f"{topic_key}:{progress.objective_version}:{target}".encode()).hexdigest()[:20],
                objective_id=progress.objective_id or topic_key,
                objective_version=progress.objective_version,
                target=target, answer_unit=target,
            )
            progress.information_needs.append(need)
        elif need.status != "blocked":
            need.status = "open"
        if source_answer_id and source_answer_id not in need.source_answer_ids:
            need.source_answer_ids.append(source_answer_id)
    current = {n.target: n for n in open_needs(progress, topic_key)}
    progress.next_need_id = next((current[t].need_id for t in progress.missing_information if t in current), None)


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
