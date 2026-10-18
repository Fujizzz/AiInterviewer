from decimal import ROUND_DOWN, Inexact, getcontext, setcontext

import pytest

from evaluation.aggregation import AggregationRecord
from evaluation.aggregator import aggregate_scores, replay_aggregation
from tests.evaluation.resolver_helpers import decision, resolve, source
from tests.evaluation.scoring_helpers import all_assessments, scoring_input


def record():
    item = source("a")
    return aggregate_scores(scoring_input(resolve((item, decision(item))), *all_assessments(item)))


def test_published_snapshot_replays_from_json_without_files_or_models(monkeypatch):
    original = record()
    saved = AggregationRecord.model_validate_json(original.model_dump_json())
    monkeypatch.setattr("evaluation.rubric.load_rubric_pack", lambda *_: pytest.fail("disk read"))
    assert replay_aggregation(saved) == original.snapshot
    assert saved == original


@pytest.mark.parametrize(
    "section",
    [
        "score",
        "weight",
        "math",
        "quality",
        "gate",
        "source",
        "rubric",
        "policy_version",
        "judgement",
        "snapshot_id",
    ],
)
def test_replay_rejects_tampered_sources_trace_and_snapshot(section):
    data = record().model_dump(mode="json")
    if section == "score":
        data["snapshot"]["overall_score"] = 5
    elif section == "weight":
        data["weights"][0]["effective_weight"] = 0.5
    elif section == "math":
        data["overall_mean"]["numerator"] += 1
    elif section == "quality":
        data["weights"][0]["quality"]["specificity"] = "vague"
    elif section == "gate":
        data["inputs"]["policy"]["thresholds"]["min_independent_evidence"] = 2
    elif section == "source":
        data["inputs"]["history"]["sources"][0]["turn"]["answer"]["text"] = "different answer"
    elif section == "rubric":
        data["inputs"]["rubric"]["rubrics"][0]["criteria"][0]["anchors"][0]["behavior"] = "changed"
    elif section == "policy_version":
        data["inputs"]["policy"]["policy_version"] = "future"
    elif section == "judgement":
        data["inputs"]["judgement"]["assessments"][0]["concise_rationale"] = "changed"
    else:
        data["snapshot"]["snapshot_id"] = "forged"
    with pytest.raises(ValueError):
        replay_aggregation(AggregationRecord.model_validate(data))


def test_reevaluation_creates_new_snapshot_and_preserves_old_record():
    old = record()
    serialized = old.model_dump_json()
    new = aggregate_scores(
        old.inputs.model_copy(
            update={
                "supersedes_snapshot_id": old.snapshot.snapshot_id,
                "reevaluation_reason": "Reviewed scoring configuration",
            }
        )
    )
    assert new.snapshot.snapshot_id != old.snapshot.snapshot_id
    assert new.snapshot.supersedes_snapshot_id == old.snapshot.snapshot_id
    assert replay_aggregation(new) == new.snapshot
    assert old.model_dump_json() == serialized
    with pytest.raises(ValueError, match="reevaluation requires"):
        aggregate_scores(old.inputs.model_copy(update={"supersedes_snapshot_id": "missing-reason"}))


def test_replay_is_independent_of_global_decimal_context_and_profile_order():
    original = record()
    previous_context = getcontext().copy()
    try:
        getcontext().prec = 3
        getcontext().rounding = ROUND_DOWN
        getcontext().traps[Inexact] = True
        replayed = aggregate_scores(
            original.inputs.model_copy(
                update={
                    "profile": original.inputs.profile.model_copy(
                        update={
                            "competencies": tuple(reversed(original.inputs.profile.competencies)),
                        }
                    ),
                }
            )
        )
    finally:
        setcontext(previous_context)
    assert original == replayed
