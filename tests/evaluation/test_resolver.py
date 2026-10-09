import pytest
from pydantic import ValidationError

from evaluation.contracts import QuoteSpan
from evaluation.inputs import EvidenceIndexEntry
from evaluation.model_calls import EvaluationStageError
from evaluation.resolution import RelationDecision, ResolutionDraft, ResolutionHistory
from evaluation.resolver import EvidenceResolver, replay_resolution
from tests.evaluation.resolver_helpers import context, decision, resolve, source


@pytest.mark.parametrize(
    "relation", ["duplicate", "refines", "supports", "contradicts", "retracts"]
)
def test_all_relations_keep_grounded_audit_trail(relation):
    earlier = source("old")
    text = {
        "duplicate": "I located the lock bottleneck with profiling.",
        "refines": "The profiler showed 80% time waiting for the lock.",
        "supports": "A separate load test reproduced the lock bottleneck.",
        "contradicts": "I did not use a profiler; I guessed the cause.",
        "retracts": "I retract my earlier statement that I used a profiler.",
    }[relation]
    later = source("new", text, thread="different-question-thread")
    proof = later.evidence.quote_spans[0] if relation == "retracts" else None
    result = resolve(
        (earlier, decision(earlier)),
        (
            later,
            decision(later, relation, (earlier,), proof=proof),
        ),
    )
    assert result.evidence_items[1].relation == relation
    assert result.evidence_items[1].related_evidence_ids == (earlier.evidence.evidence_id,)
    same_group = result.evidence_items[0].independence_group_id == (
        result.evidence_items[1].independence_group_id
    )
    assert same_group == (relation != "supports")
    if relation == "contradicts":
        assert [s.status for s in result.states] == ["disputed", "disputed"]
        assert result.conflicts[0].status == "unresolved"
    elif relation == "retracts":
        assert [s.status for s in result.states] == ["retracted", "retracted"]
    else:
        assert result.states[1].status == ("duplicate" if relation == "duplicate" else "eligible")
    assert (
        replay_resolution(ResolutionHistory.model_validate_json(result.history.model_dump_json()))
        == result
    )


def test_normalized_repetition_is_overridden_without_modifying_identity():
    earlier = source("old", claim="  I used a profiler.\n")
    later = source("new", "I used a profiler, as I said.", claim="I used a profiler.")
    result = resolve(
        (earlier, decision(earlier)),
        (
            later,
            decision(later, same=(earlier,)),
        ),
    )
    assert result.states[1].status == "duplicate"
    assert result.states[0].canonical_claim == result.states[1].canonical_claim
    assert result.evidence_items[1].evidence_id == later.evidence.evidence_id
    assert result.evidence_items[0].normalized_claim == "  I used a profiler.\n"
    assert result.history.sources[1].evidence.independence_group_id is None


def test_same_event_facts_and_independent_episodes_are_distinct():
    first = source("first")
    detail = source("detail", "I validated the fix with a sustained load test.")
    separate = source("separate", "In a second incident I profiled a lock.")
    result = resolve(
        (first, decision(first)),
        (detail, decision(detail, same=(first,))),
        (separate, decision(separate)),
    )
    a, b, c = (item.independence_group_id for item in result.evidence_items)
    assert a == b and a != c
    assert all(s.status == "eligible" for s in result.states)


def test_identical_claims_in_distinct_projects_are_not_collapsed():
    first = source("a", project="one")
    second = source("b", project="two")
    result = resolve((first, decision(first)), (second, decision(second)))
    assert result.states[1].status == "eligible"
    assert (
        result.evidence_items[0].independence_group_id
        != result.evidence_items[1].independence_group_id
    )


def test_relation_target_may_share_episode_transitively():
    root = source("root")
    detail = source("detail", "I used contention sampling in that profiler.")
    later = source("later", "I sampled contention at 10 millisecond intervals.")
    relation = RelationDecision(
        evidence_id=later.evidence.evidence_id,
        relation="refines",
        related_evidence_ids=(detail.evidence.evidence_id,),
        independence="same_episode",
        same_episode_as=(root.evidence.evidence_id,),
        concise_rationale="A detail of the same diagnosis",
    )
    result = resolve(
        (root, decision(root)), (detail, decision(detail, same=(root,))), (later, relation)
    )
    assert len({e.independence_group_id for e in result.evidence_items}) == 1


def test_relation_target_in_different_episode_is_still_rejected():
    root = source("root")
    unrelated = source("other", "In a separate incident I sampled contention.")
    later = source("later", "I sampled contention at 10 millisecond intervals.")
    relation = RelationDecision(
        evidence_id=later.evidence.evidence_id,
        relation="refines",
        related_evidence_ids=(unrelated.evidence.evidence_id,),
        independence="same_episode",
        same_episode_as=(root.evidence.evidence_id,),
        concise_rationale="Incorrect cross-event link",
    )
    with pytest.raises(ValueError, match="same episode"):
        resolve((root, decision(root)), (unrelated, decision(unrelated)), (later, relation))


def test_duplicate_span_cannot_create_new_episode_in_same_answer():
    first = source("a", claim="I used a profiler. ")
    second = first.model_copy(
        update={
            "evidence": first.evidence.model_copy(
                update={
                    "evidence_id": "alternate-extraction",
                    "normalized_claim": "I used a profiler.",
                }
            )
        }
    )
    result = resolve((first, decision(first)), (second, decision(second)))
    assert result.states[1].status == "duplicate"


def test_uncertain_independence_does_not_count_as_new_evidence():
    item = source("uncertain")
    result = resolve((item, decision(item, episode="unresolved")))
    assert result.states[0].status == "insufficient"
    assert result.states[0].reason_codes == ("independence_unresolved",)


def test_retraction_resolves_only_targeted_conflicts_and_keeps_history():
    earlier = source("old")
    alias = source("alias", "Profiling was how I identified the contention.")
    contrary = source("contrary", "I did not use profiling for this incident.")
    other = source("other", "I validated the fix under a sustained workload.")
    withdrawn = source("withdrawn", "I retract my original claim about personally profiling.")
    before = resolve(
        (earlier, decision(earlier)),
        (alias, decision(alias, "duplicate", (earlier,))),
        (contrary, decision(contrary, "contradicts", (earlier,))),
        (other, decision(other, same=(earlier,))),
    )
    serialized = before.model_dump_json()
    after = replay_resolution(
        ResolutionHistory(
            interview_id="interview",
            sources=(*before.history.sources, withdrawn),
            decisions=(
                *before.history.decisions,
                decision(
                    withdrawn,
                    "retracts",
                    (alias,),
                    proof=withdrawn.evidence.quote_spans[0],
                ),
            ),
        )
    )
    assert [s.status for s in before.states] == ["disputed", "disputed", "disputed", "eligible"]
    assert [s.status for s in after.states] == [
        "retracted",
        "retracted",
        "eligible",
        "eligible",
        "retracted",
    ]
    assert after.conflicts[0].status == "resolved_by_retraction"
    assert before.model_dump_json() == serialized


def test_refinements_cannot_launder_disputed_or_retracted_claims():
    original = source("old")
    refined = source("refined", "I used the profiler's contention sampling mode.")
    contrary = source("contrary", "I never ran a profiler.")
    result = resolve(
        (original, decision(original)),
        (refined, decision(refined, "refines", (original,))),
        (contrary, decision(contrary, "contradicts", (original,))),
    )
    assert all(s.status == "disputed" for s in result.states)


@pytest.mark.parametrize(
    "problem",
    [
        "missing_source",
        "missing_decision",
        "duplicate_source",
        "duplicate_decision",
        "future",
        "foreign_interview",
        "fake_quote",
        "cross_project",
        "same_claim_contradiction",
        "fake_retraction",
        "proof_outside_evidence",
        "changed_answer",
        "changed_question",
        "resolved_source",
        "duplicate_conflict",
    ],
)
def test_invalid_history_and_edges_fail_closed(problem):
    first = source("first")
    second = source("second", "I never used a profiler.")
    pairs = [(first, decision(first)), (second, decision(second, "contradicts", (first,)))]
    if problem == "missing_source":
        pairs[1] = (
            second,
            pairs[1][1].model_copy(
                update={
                    "related_evidence_ids": ("missing",),
                    "same_episode_as": ("missing",),
                }
            ),
        )
    elif problem == "future":
        pairs.reverse()
    elif problem == "foreign_interview":
        first = first.model_copy(
            update={
                "turn": first.turn.model_copy(
                    update={
                        "answer": first.turn.answer.model_copy(update={"interview_id": "foreign"}),
                    }
                )
            }
        )
        pairs[0] = (first, decision(first))
    elif problem == "fake_quote":
        first = first.model_copy(
            update={
                "evidence": first.evidence.model_copy(
                    update={
                        "quote_spans": (QuoteSpan(quote="invented", char_start=0, char_end=8),),
                    }
                )
            }
        )
        pairs[0] = (first, decision(first))
    elif problem == "cross_project":
        second = source("second", "I never used a profiler.", project="other")
        pairs[1] = (second, decision(second, "contradicts", (first,)))
    elif problem == "same_claim_contradiction":
        second = source("second")
        pairs[1] = (second, decision(second, "contradicts", (first,)))
    elif problem in {"fake_retraction", "proof_outside_evidence"}:
        second = source("second", "I retract that claim. I used a profiler.")
        proof = QuoteSpan(quote="I retract that claim.", char_start=0, char_end=21)
        if problem == "fake_retraction":
            proof = QuoteSpan(quote="Invented retraction.", char_start=0, char_end=20)
        else:
            second = second.model_copy(
                update={
                    "evidence": second.evidence.model_copy(
                        update={
                            "quote_spans": (
                                QuoteSpan(quote="I used a profiler.", char_start=22, char_end=40),
                            ),
                        }
                    )
                }
            )
        pairs[1] = (second, decision(second, "retracts", (first,), proof=proof))
    elif problem in {"changed_answer", "changed_question"}:
        if problem == "changed_answer":
            second = first.model_copy(
                update={
                    "evidence": first.evidence.model_copy(update={"evidence_id": "different"}),
                    "turn": first.turn.model_copy(
                        update={
                            "answer": first.turn.answer.model_copy(
                                update={"text": first.turn.answer.text + " More."}
                            ),
                        }
                    ),
                }
            )
        else:
            second = first.model_copy(
                update={
                    "evidence": first.evidence.model_copy(update={"evidence_id": "different"}),
                    "turn": first.turn.model_copy(
                        update={
                            "question": first.turn.question.model_copy(
                                update={"text": "Different question?"}
                            ),
                        }
                    ),
                }
            )
        pairs[1] = (second, decision(second))
    elif problem == "resolved_source":
        first = first.model_copy(
            update={
                "evidence": first.evidence.model_copy(
                    update={
                        "independence_group_id": "untrusted-group",
                    }
                )
            }
        )
        pairs[0] = (first, decision(first))
    elif problem == "duplicate_conflict":
        third = source("third", "I profiled, or maybe I never did.")
        pairs.append((third, decision(third, "duplicate", (first, second))))
    history = ResolutionHistory(
        interview_id="interview",
        sources=tuple(p[0] for p in pairs),
        decisions=tuple(p[1] for p in pairs),
    )
    if problem.startswith("missing_decision"):
        history = history.model_copy(update={"decisions": history.decisions[:1]})
    elif problem == "duplicate_source":
        history = history.model_copy(update={"sources": (*history.sources, first)})
    elif problem == "duplicate_decision":
        history = history.model_copy(
            update={"decisions": (*history.decisions, history.decisions[0])}
        )
    with pytest.raises(ValueError):
        replay_resolution(history)


@pytest.mark.parametrize(
    "changes",
    [
        {"relation": "duplicate"},
        {"same_episode_as": ("unknown",)},
        {"independence": "same_episode"},
        {"related_evidence_ids": ("self",)},
        {"retraction_span": {"quote": "I retract", "char_start": 0, "char_end": 9}},
        {"score": 5},
        {"independence_group_id": "injected"},
        {"concise_rationale": " "},
    ],
)
def test_model_schema_rejects_inconsistent_and_program_owned_fields(changes):
    with pytest.raises(ValidationError):
        RelationDecision.model_validate(
            {
                "evidence_id": "self",
                "relation": "new",
                "independence": "new_episode",
                "concise_rationale": "A concrete event.",
                **changes,
            }
        )


async def test_resolver_payload_uses_grounded_full_history_without_rubric_or_scores():
    old = source("old")
    new = source("new", "I profiled the same contention in more detail.")
    previous = resolve((old, decision(old)))
    calls = []

    def model(prompt, data, schema):
        calls.append(data)
        assert schema is ResolutionDraft
        assert "untrusted data" in prompt
        assert set(data) == {
            "question",
            "answer",
            "current_evidence",
            "history",
            "resolved_history",
        }
        assert data["history"][0]["turn"]["answer"]["text"] == old.turn.answer.text
        return schema(decisions=(decision(new, "refines", (old,)),))

    resolver = EvidenceResolver(model)
    first = await resolver.resolve(context(new), (new.evidence,), history=previous.history)
    second = await resolver.resolve(context(new), (new.evidence,), history=previous.history)
    assert first == second and len(calls) == 2


async def test_compact_index_without_original_grounding_is_rejected_before_model():
    old, new = source("old"), source("new", "I diagnosed the cache miss ratio.")
    value = context(new).model_copy(
        update={
            "existing_evidence": (
                EvidenceIndexEntry(
                    **old.evidence.model_dump(include=set(EvidenceIndexEntry.model_fields)),
                ),
            )
        }
    )
    with pytest.raises(EvaluationStageError, match="resolver_invalid_evidence"):
        await EvidenceResolver(None).resolve(value, (new.evidence,))


@pytest.mark.parametrize("problem", ["missing", "extra", "duplicate", "unknown_target"])
async def test_entire_relation_batch_is_atomic(problem):
    new = source("new")
    chosen = decision(new)
    proposals = [chosen]
    if problem == "missing":
        proposals = []
    elif problem == "extra":
        proposals += [decision(source("imaginary"))]
    elif problem == "duplicate":
        proposals *= 2
    else:
        proposals = [decision(new, "duplicate", (source("imaginary"),))]
    with pytest.raises(EvaluationStageError, match="resolver_invalid_relations"):
        await EvidenceResolver(lambda prompt, data, schema: schema(decisions=proposals)).resolve(
            context(new),
            (new.evidence,),
        )


async def test_empty_batch_replays_history_without_a_model_call():
    old, new = source("old"), source("new")
    history = resolve((old, decision(old)))
    assert (
        await EvidenceResolver(None).resolve(context(new), (), history=history.history) == history
    )


async def test_context_cannot_change_the_original_answer_kept_in_resolution_history():
    old, new = source("old"), source("new", "I examined the cache miss rate.")
    history = resolve((old, decision(old))).history
    changed = old.turn.model_copy(
        update={
            "answer": old.turn.answer.model_copy(
                update={
                    "text": "A different candidate statement.",
                }
            )
        }
    )
    value = context(new).model_copy(update={"history": (changed,)})
    with pytest.raises(EvaluationStageError, match="resolver_invalid_evidence"):
        await EvidenceResolver(None).resolve(value, (new.evidence,), history=history)
