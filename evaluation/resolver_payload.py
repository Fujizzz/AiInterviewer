"""Compact request-local references; original sources and ledger IDs stay immutable."""

from evaluation.resolution import RelationDecision, ResolutionDraft


def resolver_payload(context, current, previous):
    """Keep every original answer and quote; serialize each evidence relation once."""
    prior = previous.evidence_items
    ids = [item.evidence_id for item in (*prior, *current)]
    aliases = {key: f"e{index}" for index, key in enumerate(ids)}
    groups = {}
    turns = {source.turn.answer.answer_id: source.turn for source in previous.history.sources}

    def evidence(item, *, resolved=False):
        row = item.model_dump(
            mode="json",
            include={
                "answer_id",
                "quote_spans",
                "normalized_claim",
                "evidence_kind",
                "ownership_scope",
                "factuality",
                "specificity",
            },
        )
        row["evidence_id"] = aliases[item.evidence_id]
        if resolved:
            row["relation"] = item.relation.value
            row["related_evidence_ids"] = [aliases[key] for key in item.related_evidence_ids]
            group = item.independence_group_id
            row["episode"] = groups.setdefault(group, f"g{len(groups)}")
        return row

    payload = {
        "question": context.question.model_dump(mode="json"),
        "answer": context.answer.model_dump(mode="json"),
        "current_evidence": [evidence(item) for item in current],
        "history": [evidence(item, resolved=True) for item in prior],
        "source_answers": [turn.model_dump(mode="json") for turn in turns.values()],
    }
    return payload, {alias: key for key, alias in aliases.items()}


def expand_resolution(output, references):
    """Reject unknown aliases before any relation reaches the durable ledger."""
    decisions = []
    for item in output.decisions:
        try:
            data = item.model_dump()
            data["evidence_id"] = references[item.evidence_id]
            for field in ("related_evidence_ids", "same_episode_as"):
                data[field] = tuple(references[key] for key in getattr(item, field))
        except KeyError as error:
            raise ValueError("unknown request-local evidence reference") from error
        decisions.append(RelationDecision.model_validate(data))
    return ResolutionDraft(decisions=tuple(decisions))
