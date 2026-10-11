"""Project per-criterion evidence onto objective completion without another model call."""

import re
from hashlib import sha256

from shared.contracts.planning import CompletionRequirement


def requirements_for_objective(item, progress):
    """Read older contexts without mutating the repository's analysis snapshot."""
    stored = getattr(progress, "completion_requirements", [])
    if stored:
        return stored
    version = getattr(progress, "objective_version", 1)
    return [
        CompletionRequirement(
            criterion_id="criterion:"
            + sha256(f"{item.topic_key}:{version}:{text}".encode()).hexdigest()[:20],
            requirement=text,
        )
        for text in dict.fromkeys(
            part.strip() for part in re.split(r"[;；]+", item.completion_criteria) if part.strip()
        )
    ]


def project_completion(update, requirements):
    """A broad sufficient verdict cannot close a criterion without its own evidence."""
    projected = [item.model_copy(deep=True) for item in requirements]
    observations = {item.criterion_id: item for item in update.criterion_coverage}
    for item in projected:
        observed = observations.get(item.criterion_id)
        if observed is None:
            continue
        item.coverage_status = observed.coverage_status
        item.missing_information = list(dict.fromkeys(observed.missing_information))
        if observed.supporting_quotes:
            item.evidence = [
                proof for proof in item.evidence if proof["answer_id"] != observed.answer_id
            ] + [
                {
                    "answer_id": observed.answer_id,
                    "supporting_quotes": observed.supporting_quotes,
                    "supporting_segment_ids": observed.supporting_segment_ids,
                }
            ]
    gaps = [
        gap
        for item in projected
        if item.coverage_status != "sufficient"
        for gap in (item.missing_information or [item.requirement])
    ]
    all_satisfied = bool(projected) and all(
        item.coverage_status == "sufficient" for item in projected
    )
    if all_satisfied:
        # The criterion contract is authoritative in both directions: a generic
        # overall gap must not reopen a goal whose required evidence is complete.
        missing = []
    elif not observations and update.coverage_status == "partial" and update.missing_information:
        # Older analyzers may still supply a useful narrow gap. Keep it actionable,
        # but never use that legacy observation to satisfy any completion criterion.
        missing = update.missing_information
    elif gaps and observations:
        # Replace a stale umbrella gap with the remaining atomic needs. In particular,
        # measured results must not reopen a validation method already evidenced.
        missing = gaps
    else:
        missing = [*update.missing_information, *gaps]
    missing = list(dict.fromkeys(missing))
    sufficient = all_satisfied or (
        not projected and update.coverage_status == "sufficient" and not missing
    )
    return update.model_copy(
        update={
            "coverage_status": "sufficient" if sufficient else "partial",
            "missing_information": missing,
        }
    ), projected
