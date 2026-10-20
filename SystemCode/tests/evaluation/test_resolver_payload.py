"""Transport compaction preserves all source material and cannot change ledger references."""

import pytest

from evaluation.resolution import ResolutionDraft
from evaluation.resolver import EvidenceResolver
from evaluation.resolver_payload import expand_resolution, resolver_payload
from tests.evaluation.resolver_helpers import context, decision, resolve, source


@pytest.mark.parametrize(
    "relation", ["duplicate", "refines", "supports", "contradicts", "retracts"]
)
async def test_alias_round_trip_preserves_relations_original_unicode_spans_and_scores(relation):
    old = source("old", "我使用了分析器。\nOriginal source remains unchanged.")
    new = source("new", "我更正刚才的描述。I retract the earlier claim.")
    prior = resolve((old, decision(old)))
    chosen = decision(
        new, relation, (old,), proof=new.evidence.quote_spans[0] if relation == "retracts" else None
    )
    expected = resolve((old, decision(old)), (new, chosen))

    def provider(prompt, payload, schema):
        assert payload["source_answers"] == [old.turn.model_dump(mode="json")]
        assert payload["answer"]["text"] == new.turn.answer.text
        assert (
            payload["history"][0]["quote_spans"]
            == old.evidence.model_dump(mode="json")["quote_spans"]
        )
        assert "resolved_history" not in payload
        assert "independence_group_id" not in payload["history"][0]
        data = chosen.model_dump()
        data["evidence_id"] = payload["current_evidence"][0]["evidence_id"]
        for field in ("related_evidence_ids", "same_episode_as"):
            data[field] = [payload["history"][0]["evidence_id"] for _ in data[field]]
        return schema(decisions=[data])

    actual = await EvidenceResolver(provider).resolve(
        context(new), (new.evidence,), history=prior.history
    )
    assert actual == expected
    assert actual.history.sources[-1].evidence == new.evidence


@pytest.mark.parametrize("field", ["evidence_id", "related_evidence_ids", "same_episode_as"])
def test_unknown_alias_is_rejected_before_ledger_publication(field):
    item = source("one")
    data = decision(item).model_dump()
    data["evidence_id"] = "e0"
    if field == "evidence_id":
        data[field] = "unknown"
    elif field == "related_evidence_ids":
        data.update(relation="supports", related_evidence_ids=["unknown"])
    else:
        data.update(independence="same_episode", same_episode_as=["unknown"])
    with pytest.raises(ValueError, match="unknown request-local"):
        expand_resolution(ResolutionDraft(decisions=[data]), {"e0": item.evidence.evidence_id})


def test_all_projects_and_original_answers_survive_compaction():
    first = source("one", project="p1")
    second = source("two", project="p2")
    current = source("three", project="p1")
    prior = resolve((first, decision(first)), (second, decision(second)))
    payload, references = resolver_payload(context(current), (current.evidence,), prior)
    assert payload["source_answers"] == [
        first.turn.model_dump(mode="json"),
        second.turn.model_dump(mode="json"),
    ]
    assert set(references.values()) == {
        item.evidence.evidence_id for item in (first, second, current)
    }
    assert [row["episode"] for row in payload["history"]] == ["g0", "g1"]


async def test_distinct_fact_in_same_episode_keeps_link_and_does_not_add_independence():
    old = source("diagnosis", "I reproduced the log pipeline memory failure with a 10GB file.")
    new = source("fix", "For that same incident I added a bounded queue.")
    prior = resolve((old, decision(old)))

    def provider(prompt, payload, schema):
        return schema(decisions=[{
            "evidence_id": payload["current_evidence"][0]["evidence_id"],
            "relation": "new",
            "related_evidence_ids": [],
            "independence": "same_episode",
            "same_episode_as": [payload["history"][0]["evidence_id"]],
            "concise_rationale": "A separate action during the explicitly same incident.",
        }])

    actual = await EvidenceResolver(provider).resolve(
        context(new), (new.evidence,), history=prior.history
    )
    assert len({e.independence_group_id for e in actual.evidence_items}) == 1
    assert actual.history.decisions[-1].same_episode_as == (old.evidence.evidence_id,)
    assert actual.history.decisions[-1].related_evidence_ids == ()
    assert all(s.status == "eligible" for s in actual.states)


def test_same_episode_with_empty_link_is_rejected_even_for_new_atomic_fact():
    # Captured failure shape from the live contrast experiment. Do not silently
    # infer an episode from prose or weaken the gate to get a numeric score.
    with pytest.raises(ValueError, match="same_episode requires existing episode references"):
        ResolutionDraft(decisions=[{
            "evidence_id": "e1", "relation": "new", "related_evidence_ids": [],
            "independence": "same_episode", "same_episode_as": [],
            "concise_rationale": "A distinct fact within the same incident.",
        }])
