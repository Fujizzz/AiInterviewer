from pathlib import Path
from shutil import copytree

import pytest
import yaml
from pydantic import ValidationError

from evaluation.contracts import CriterionAssessment, EvaluationResult
from evaluation.rubric import (
    RUBRIC_ROOT,
    CompetencyRubric,
    RubricPack,
    load_rubric,
    load_rubric_pack,
)
from shared.contracts import Competency


def test_pack_has_six_competencies_and_distinct_behavioral_anchors():
    pack = load_rubric_pack()
    assert {rubric.competency for rubric in pack.rubrics} == set(Competency)
    behaviors = []
    for rubric in pack.rubrics:
        assert len(rubric.criteria) >= 2
        for criterion in rubric.criteria:
            assert {anchor.level for anchor in criterion.anchors} == set(range(1, 6))
            behaviors.extend(anchor.behavior for anchor in criterion.anchors)
    assert len(behaviors) == len(set(behaviors)) == 105
    assert len(pack.for_competency("debugging").criteria) == 6
    assert RubricPack.model_validate_json(pack.model_dump_json()) == pack


def test_fixture_references_resolve_to_exact_rubric_criterion_and_anchor(example):
    pack = load_rubric_pack()
    result = EvaluationResult.model_validate(example["result"])
    evidence_ids = {item.evidence_id for item in result.evidence_items}
    assessments = {item.assessment_id: item for item in result.assessments}
    for assessment in result.assessments:
        pack.validate_assessment(assessment)
        assert set(assessment.evidence_ids) <= evidence_ids
        assert set(assessment.counter_evidence_ids) <= evidence_ids
    for competency in result.score_snapshot.competencies:
        rubric = pack.for_competency(competency.competency)
        assert {c.criterion_id for c in competency.criteria} == {
            c.criterion_id for c in rubric.criteria
        }
        for criterion in competency.criteria:
            for contribution in criterion.contributions:
                assessment = assessments[contribution.assessment_id]
                assert assessment.criterion_id == criterion.criterion_id
                assert assessment.assigned_level == contribution.assigned_level
                assert assessment.evidence_ids == contribution.evidence_ids


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_level",
        "duplicate_level",
        "duplicate_id",
        "wrong_namespace",
        "empty_behavior",
        "zero_weight",
        "nan_weight",
        "duplicate_criterion",
        "unknown_field",
        "no_required",
        "wrong_competency",
        "unknown_schema",
    ],
)
def test_rubric_schema_rejects_corrupt_data(mutation):
    data = load_rubric_pack().rubrics[0].model_dump(mode="json")
    criterion = data["criteria"][0]
    if mutation == "missing_level":
        criterion["anchors"].pop()
    elif mutation == "duplicate_level":
        criterion["anchors"][1]["level"] = 1
    elif mutation == "duplicate_id":
        criterion["anchors"][1]["anchor_id"] = criterion["anchors"][0]["anchor_id"]
    elif mutation == "wrong_namespace":
        criterion["criterion_id"] = "debugging.unrelated"
    elif mutation == "empty_behavior":
        criterion["anchors"][0]["behavior"] = " "
    elif mutation == "zero_weight":
        criterion["weight"] = 0
    elif mutation == "nan_weight":
        criterion["weight"] = float("nan")
    elif mutation == "duplicate_criterion":
        data["criteria"].append(criterion)
    elif mutation == "unknown_field":
        data["secret_weight"] = 10
    elif mutation == "no_required":
        for item in data["criteria"]:
            item["required"] = False
    elif mutation == "wrong_competency":
        data["competency"] = "made_up"
    else:
        data["schema_version"] = "99"
    with pytest.raises(ValidationError):
        CompetencyRubric.model_validate(data)


@pytest.mark.parametrize(
    "patch",
    [
        {"rubric_version": "2.0.0"},
        {"criterion_id": "debugging.unknown"},
        {"competency": "ownership"},
        {"matched_anchor_ids": ["debugging.root_cause.l3"]},
        {"assigned_level": 4},
    ],
)
def test_assessment_references_fail_closed(example, patch):
    assessment = CriterionAssessment.model_validate(
        {**example["result"]["assessments"][0], **patch}
    )
    with pytest.raises(ValueError):
        load_rubric_pack().validate_assessment(assessment)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "mixed_version"])
def test_pack_requires_exact_competencies_and_version(mutation):
    data = load_rubric_pack().model_dump(mode="json")
    if mutation == "missing":
        data["rubrics"].pop()
    elif mutation == "duplicate":
        data["rubrics"].append(data["rubrics"][0])
    else:
        data["rubrics"][0]["rubric_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        RubricPack.model_validate(data)


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "null",
        "{invalid",
        "schema_version: '1.0'\nschema_version: '2.0'",
        "!!python/object/apply:os.system ['echo unsafe']",
        "1: invalid_key",
    ],
)
def test_loader_rejects_invalid_ambiguous_or_unsafe_yaml(tmp_path, content):
    path = tmp_path / "invalid.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid rubric"):
        load_rubric(path)


@pytest.mark.parametrize("version", ["", "../1.0.0", "latest", "1.0", "1..0", "9.9.9"])
def test_loader_never_falls_back_to_another_version(version):
    with pytest.raises(ValueError):
        load_rubric_pack(version)


@pytest.mark.parametrize("mutation", ["missing", "wrong_file", "mixed_version"])
def test_loader_validates_files_and_does_not_cache_stale_pack(tmp_path, mutation):
    root = tmp_path / "rubrics"
    copytree(RUBRIC_ROOT, root)
    assert load_rubric_pack(root=root).rubric_version == "1.0.0"
    path = root / "1.0.0" / "ownership.yaml"
    if mutation == "missing":
        path.unlink()
    elif mutation == "wrong_file":
        path.write_text((path.parent / "debugging.yaml").read_text(), encoding="utf-8")
    else:
        data = yaml.safe_load(path.read_text())
        data["rubric_version"] = "2.0.0"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_rubric_pack(root=root)


def test_default_loader_is_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_rubric_pack().rubric_version == "1.0.0"
    assert Path(RUBRIC_ROOT).is_absolute()
