"""Load and validate an explicit rubric version without calling an LLM."""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, model_validator

from evaluation.contracts import (
    CriterionAssessment,
    EvaluationModel,
    Level,
    Text,
    require_unique,
)
from shared.contracts import Competency

DEFAULT_RUBRIC_VERSION = "1.0.0"
RUBRIC_ROOT = Path(__file__).with_name("rubrics")


class RubricAnchor(EvaluationModel):
    anchor_id: Text
    level: Level
    behavior: Text


class RubricCriterion(EvaluationModel):
    criterion_id: Text
    name: Text
    description: Text
    weight: float = Field(strict=True, gt=0)
    required: bool = Field(strict=True)
    anchors: tuple[RubricAnchor, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_anchors(self) -> Self:
        require_unique([anchor.anchor_id for anchor in self.anchors], "anchor_ids")
        if {anchor.level for anchor in self.anchors} != {1, 2, 3, 4, 5}:
            raise ValueError("each criterion must have exactly one anchor for each level 1-5")
        for anchor in self.anchors:
            if anchor.anchor_id != f"{self.criterion_id}.l{anchor.level}":
                raise ValueError("anchor_id must be namespaced by criterion and level")
        return self


class CompetencyRubric(EvaluationModel):
    schema_version: Literal["1.0"] = "1.0"
    rubric_version: Text
    competency: Competency
    name: Text
    description: Text
    criteria: tuple[RubricCriterion, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_criteria(self) -> Self:
        require_unique([item.criterion_id for item in self.criteria], "criterion_ids")
        if not any(item.required for item in self.criteria):
            raise ValueError("rubrics require at least one required criterion")
        for item in self.criteria:
            if not item.criterion_id.startswith(f"{self.competency.value}."):
                raise ValueError("criterion_id must be namespaced by competency")
        return self


class RubricPack(EvaluationModel):
    rubric_version: Text
    rubrics: tuple[CompetencyRubric, ...]

    @model_validator(mode="after")
    def validate_pack(self) -> Self:
        names = [rubric.competency for rubric in self.rubrics]
        require_unique(names, "competencies")
        if set(names) != set(Competency):
            raise ValueError("a rubric pack must contain exactly the six competencies")
        if any(rubric.rubric_version != self.rubric_version for rubric in self.rubrics):
            raise ValueError("all rubric versions must match the pack version")
        return self

    def for_competency(self, competency: Competency | str) -> CompetencyRubric:
        key = Competency(competency)
        return next(rubric for rubric in self.rubrics if rubric.competency == key)

    def validate_assessment(self, assessment: CriterionAssessment) -> None:
        """Check judge references; this does not judge evidence or compute scores."""
        if assessment.rubric_version != self.rubric_version:
            raise ValueError("assessment rubric version does not match the pack")
        rubric = self.for_competency(assessment.competency)
        criterion = next(
            (item for item in rubric.criteria if item.criterion_id == assessment.criterion_id), None
        )
        if criterion is None:
            raise ValueError("assessment references an unknown criterion for this competency")
        anchors = {anchor.anchor_id: anchor for anchor in criterion.anchors}
        for anchor_id in assessment.matched_anchor_ids:
            if anchor_id not in anchors:
                raise ValueError("assessment references an unknown anchor for this criterion")
            if anchors[anchor_id].level != assessment.assigned_level:
                raise ValueError("assessment level must match the referenced anchor")


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject ambiguous YAML mappings instead of silently accepting the last value."""


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("rubric YAML mapping keys must be strings")
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_rubric(path: str | Path) -> CompetencyRubric:
    path = Path(path)
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
        return CompetencyRubric.model_validate(data)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid rubric {path}: {exc}") from exc


def load_rubric_pack(
    version: str = DEFAULT_RUBRIC_VERSION, *, root: str | Path = RUBRIC_ROOT
) -> RubricPack:
    """Load all six files from root/version; never fall back to a different version."""
    if not version or any(char not in "0123456789." for char in version):
        raise ValueError("rubric version must be a numeric dotted version")
    if len(version.split(".")) != 3 or not all(part.isdigit() for part in version.split(".")):
        raise ValueError("rubric version must contain major.minor.patch")
    directory = Path(root) / version
    rubrics = []
    for competency in Competency:
        rubric = load_rubric(directory / f"{competency.value}.yaml")
        if rubric.competency != competency:
            raise ValueError(f"rubric filename does not match competency: {competency.value}")
        rubrics.append(rubric)
    return RubricPack(rubric_version=version, rubrics=tuple(rubrics))
