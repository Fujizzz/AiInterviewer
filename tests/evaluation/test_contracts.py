import json

import pytest
from pydantic import ValidationError

from evaluation.contracts import (
    CompetencyScore,
    CriterionAssessment,
    EvaluationResult,
    EvidenceItem,
    EvidenceRelation,
    QuoteSpan,
    ScoreSnapshot,
)
from shared.contracts import CandidateAnswer


def test_fixture_roundtrip_and_exact_current_answer_spans(example):
    answer = CandidateAnswer.model_validate(example["answer"])
    result = EvaluationResult.model_validate(example["result"])
    assert EvaluationResult.model_validate_json(result.model_dump_json()) == result
    assert result.schema_version == "1.0"
    assert len(result.evidence_items[0].quote_spans) == 2
    for item in result.evidence_items:
        item.validate_answer(answer)
    assert result.score_snapshot.overall_score is None
    assert result.score_snapshot.status == "insufficient_evidence"
    assert not result.score_snapshot.overall_score_publishable


@pytest.mark.parametrize(
    "patch",
    [
        {"char_start": -1},
        {"char_end": 0},
        {"char_end": 2},
        {"quote": ""},
        {"quote": "   "},
        {"char_start": True},
        {"char_end": "3"},
        {"extra": "ignored?"},
    ],
)
def test_quote_span_rejects_invalid_shape(patch):
    with pytest.raises(ValidationError):
        QuoteSpan.model_validate({"quote": "abc", "char_start": 0, "char_end": 3, **patch})


def test_unicode_offsets_and_whitespace_are_not_normalized():
    text = "序🙂 我排查\n 日志。"
    quote = " 我排查\n "
    start = text.index(quote)
    span = QuoteSpan(quote=quote, char_start=start, char_end=start + len(quote))
    span.validate_answer_text(text)
    assert span.quote == quote
    with pytest.raises(ValueError, match="current answer exactly"):
        span.validate_answer_text(text.replace("排", "检"))


@pytest.mark.parametrize("source", ["question_text", "resume_claim", "historical_answer"])
def test_non_answer_sources_cannot_ground_current_answer(example, source):
    item = example["result"]["evidence_items"][0]
    quote = example[source]
    item["quote_spans"] = [{"quote": quote, "char_start": 0, "char_end": len(quote)}]
    evidence = EvidenceItem.model_validate(item)
    with pytest.raises(ValueError, match="current answer exactly"):
        evidence.validate_answer(CandidateAnswer.model_validate(example["answer"]))


@pytest.mark.parametrize("field", ["answer_id", "question_id"])
def test_answer_identity_must_match(example, field):
    evidence = EvidenceItem.model_validate(example["result"]["evidence_items"][0])
    example["answer"][field] = "another-id"
    with pytest.raises(ValueError, match="current answer and question"):
        evidence.validate_answer(CandidateAnswer.model_validate(example["answer"]))


@pytest.mark.parametrize(
    "patch",
    [
        {"normalized_claim": " "},
        {"quote_spans": []},
        {"schema_version": "2.0"},
        {"relation": "invented"},
        {"relation": "duplicate"},
        {"related_evidence_ids": ["earlier"]},
        {"relation": "supports", "related_evidence_ids": ["earlier", "earlier"]},
        {"relation": "supports", "related_evidence_ids": ["evidence-diagnostic"]},
    ],
)
def test_evidence_rejects_ambiguous_structure(example, patch):
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate({**example["result"]["evidence_items"][0], **patch})


@pytest.mark.parametrize("mode", ["reversed", "overlap"])
def test_quote_spans_must_be_ordered_and_disjoint(example, mode):
    item = example["result"]["evidence_items"][0]
    if mode == "reversed":
        item["quote_spans"].reverse()
    else:
        item["quote_spans"].append(item["quote_spans"][-1])
    with pytest.raises(ValidationError, match="ordered and non-overlapping"):
        EvidenceItem.model_validate(item)


@pytest.mark.parametrize("relation", list(EvidenceRelation))
def test_all_relations_roundtrip(example, relation):
    item = example["result"]["evidence_items"][0]
    item["relation"] = relation.value
    item["related_evidence_ids"] = [] if relation == EvidenceRelation.NEW else ["earlier"]
    evidence = EvidenceItem.model_validate(item)
    assert EvidenceItem.model_validate_json(evidence.model_dump_json()) == evidence


@pytest.mark.parametrize(
    "patch",
    [
        {"assigned_level": 0},
        {"assigned_level": 6},
        {"assigned_level": True},
        {"assigned_level": 3.5},
        {"assigned_level": "3"},
        {"assigned_level": None},
        {"evidence_ids": []},
        {"matched_anchor_ids": []},
        {"reason_codes": []},
        {"decision": "excluded"},
        {"decision": "disputed"},
        {"evidence_ids": ["e", "e"]},
        {"counter_evidence_ids": ["evidence-diagnostic"]},
        {"competency": "unknown"},
        {"concise_rationale": "  "},
        {"score": 5},
    ],
)
def test_assessment_rejects_invalid_decisions(example, patch):
    with pytest.raises(ValidationError):
        CriterionAssessment.model_validate({**example["result"]["assessments"][0], **patch})


@pytest.mark.parametrize("decision", ["insufficient", "excluded", "disputed"])
def test_unscored_assessments_never_manufacture_a_zero(example, decision):
    item = example["result"]["assessments"][0]
    item.update(decision=decision, assigned_level=None, matched_anchor_ids=[])
    if decision == "disputed":
        item["counter_evidence_ids"] = ["evidence-counter"]
    assessment = CriterionAssessment.model_validate(item)
    assert assessment.assigned_level is None


@pytest.mark.parametrize(
    "patch",
    [
        {"overall_score": 0},
        {"overall_score": 3},
        {"overall_score": float("nan")},
        {"overall_coverage": float("inf")},
        {"overall_coverage": -0.1},
        {"overall_score_publishable": True},
        {"status": "published"},
        {"reason_codes": []},
        {"supersedes_snapshot_id": "snapshot-previous"},
        {"reevaluation_reason": "rubric_update"},
        {"supersedes_snapshot_id": "snapshot-example", "reevaluation_reason": "self"},
        {"competencies": []},
        {"schema_version": "future"},
    ],
)
def test_snapshot_rejects_invalid_publication_and_lineage(example, patch):
    with pytest.raises(ValidationError):
        ScoreSnapshot.model_validate({**example["result"]["score_snapshot"], **patch})


def test_snapshot_preserves_missing_competencies_and_previous_snapshot(example):
    data = example["result"]["score_snapshot"]
    first = ScoreSnapshot.model_validate(data)
    data.update(
        snapshot_id="snapshot-revised",
        supersedes_snapshot_id=first.snapshot_id,
        reevaluation_reason="rubric_update",
    )
    revised = ScoreSnapshot.model_validate(data)
    assert revised.supersedes_snapshot_id == first.snapshot_id
    assert first.supersedes_snapshot_id is None
    with pytest.raises(ValidationError, match="frozen"):
        first.snapshot_id = "overwrite"
    data["competencies"].pop()
    with pytest.raises(ValidationError, match="all six"):
        ScoreSnapshot.model_validate(data)


@pytest.mark.parametrize("field", ["score", "coverage", "reliability"])
@pytest.mark.parametrize("value", [-1, 6, float("inf"), float("nan"), True, "0.5"])
def test_competency_numeric_ranges_are_strict(example, field, value):
    item = example["result"]["score_snapshot"]["competencies"][0]
    with pytest.raises(ValidationError):
        CompetencyScore.model_validate({**item, field: value})


def test_published_scores_require_actual_contributions(example):
    snapshot = example["result"]["score_snapshot"]
    debugging = next(c for c in snapshot["competencies"] if c["competency"] == "debugging")
    debugging.update(score=3.0, status="published", reason_codes=[])
    snapshot.update(
        overall_score=3.0, status="published", overall_score_publishable=True, reason_codes=[]
    )
    assert ScoreSnapshot.model_validate(snapshot).overall_score == 3.0
    criterion = next(c for c in debugging["criteria"] if c["status"] == "published")
    criterion["contributions"][0]["effective_weight"] = 0
    with pytest.raises(ValidationError, match="effective evidence"):
        ScoreSnapshot.model_validate(snapshot)


def test_failure_cannot_return_evidence_scores_or_topic_completion(example):
    data = example["result"]
    data.update(status="failed", reason_codes=["model_timeout"])
    with pytest.raises(ValidationError, match="failed results"):
        EvaluationResult.model_validate(data)
    data.update(
        evidence_items=[], assessments=[], score_snapshot=None, analysis={"thread_complete": False}
    )
    assert EvaluationResult.model_validate(data).status == "failed"
    data["analysis"]["thread_complete"] = True
    with pytest.raises(ValidationError, match="failed results"):
        EvaluationResult.model_validate(data)


@pytest.mark.parametrize(
    "mutation", ["duplicate_evidence", "duplicate_assessment", "answer", "interview"]
)
def test_result_rejects_inconsistent_identity(example, mutation):
    data = example["result"]
    if mutation == "duplicate_evidence":
        data["evidence_items"].append(data["evidence_items"][0])
    elif mutation == "duplicate_assessment":
        data["assessments"].append(data["assessments"][0])
    elif mutation == "answer":
        data["answer_id"] = "other"
    else:
        data["score_snapshot"]["interview_id"] = "other"
    with pytest.raises(ValidationError):
        EvaluationResult.model_validate(data)


@pytest.mark.parametrize(
    "model",
    [
        QuoteSpan,
        EvidenceItem,
        CriterionAssessment,
        CompetencyScore,
        ScoreSnapshot,
        EvaluationResult,
    ],
)
def test_contracts_expose_json_schema(model):
    schema = json.loads(json.dumps(model.model_json_schema()))
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
