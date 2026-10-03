"""Responsibilities: Extract pending recommendation fields from resume sections across all
CandidateInput business fields.
Implementation: Recognize explicit labels and skill lists, parse strict types, and retain evidence;
conflicts or unsupported formats remain unknown.
Related Modules: resume_editor supplies section text, resume_versions exposes suggestions, and
candidate_payload consumes saved values.

Declaration Index:
- parse_value: Parse a labelled source value into its recommendation type without GPA conversion or
  date inference.
- collect_slots: Aggregate suggestions, source evidence, issues, and missing fields without database
  writes or model calls.

Variable Index:
- SLOT_LABELS: Explicit field labels in English and Chinese mapped to recommendation fields.
- LABEL_PATTERN: Locates explicit labels and value boundaries, including multiple fields on one
  line.
- TAG_FIELDS: Fields represented as lists; skill spelling, slash separators, and case are preserved.
- NUMBER_UNITS: Accepted source units for numeric fields; no unit conversion is performed.
- LEVELS: Maps explicitly stated Chinese academic year labels to existing level codes.
- MODES: Maps explicit Chinese work-mode labels to existing categories.
- BOOLEANS: Maps explicit Chinese summer-availability labels to Boolean values.
- logger: Records extraction counts and invalid field names, never evidence or personal contact
  data.

Constraints:
Missing values remain null, while explicit 0 and false are preserved. Do not infer preferences,
year, or duration from contact details, school, or projects.
Only explicit list formats are accepted for skills; numeric and categorical conflicts are never
silently resolved. Evidence is available only through the owner's private API.
"""

import logging
import re

from pydantic import ValidationError as SchemaError

from .recommendation.schemas import CandidateInput

SLOT_LABELS = {
    "skills": ("skills", "技能关键词", "专业技能", "关键技能", "技能"),
    "interests": ("interests", "目标方向", "求职意向", "求职目标", "兴趣方向"),
    "majors": ("majors", "major", "专业", "主修专业"),
    "gpa": ("gpa", "平均绩点"),
    "months_experience": (
        "months_experience",
        "累计经验（月）",
        "累计经验",
        "工作经验月数",
        "经验月数",
    ),
    "academic_level": ("academic_level", "学业阶段", "当前学业阶段", "在读年级"),
    "hours_per_week": ("hours_per_week", "每周可投入小时", "每周可投入时间", "每周工作时间"),
    "length_of_commitment": (
        "length_of_commitment",
        "可连续投入月数",
        "可连续投入时间",
        "可实习时长",
    ),
    "num_publications": ("num_publications", "发表数量", "论文数量", "发表论文数量"),
    "in_person_commitment": ("in_person_commitment", "工作方式", "办公方式"),
    "commit_to_summer": ("commit_to_summer", "可参与暑期工作", "暑期意愿", "暑期实习意愿"),
}
LABEL_PATTERN = re.compile(
    r"(?<!\w)(?P<label>"
    + "|".join(
        re.escape(label)
        for labels in SLOT_LABELS.values()
        for label in sorted(labels, key=len, reverse=True)
    )
    + r")\s*[:：]\s*",
    re.IGNORECASE,
)
TAG_FIELDS = {"skills", "interests", "majors"}
NUMBER_UNITS = {
    "months_experience": ("", "月", "个月", "months", "month"),
    "hours_per_week": ("", "小时", "小时/周", "hours/week", "h/week", "hours"),
    "length_of_commitment": ("", "月", "个月", "months", "month"),
    "num_publications": ("", "篇", "papers", "publications"),
}
LEVELS = {
    "本科一年级": "UG1",
    "本科二年级": "UG2",
    "本科三年级": "UG3",
    "本科四年级": "UG4",
    "硕士一年级": "MS1",
    "硕士二年级": "MS2",
    "博士一年级": "PhD1",
    "博士二年级": "PhD2",
    "博士三年级": "PhD3",
    "博士四年级": "PhD4",
    "博士五年级": "PhD5",
}
MODES = {
    "线下": "In Person",
    "现场": "In Person",
    "线上": "Online",
    "远程": "Online",
    "混合": "Hybrid",
    "不限": "No Preference",
}
BOOLEANS = {"是": True, "可以": True, "否": False, "不可以": False, "true": True, "false": False}
logger = logging.getLogger(__name__)


def parse_value(field, raw):
    """Parse a recommendation field and the text after its explicit label into a strict type or None
    if uncertain.

    Unknown fields raise KeyError. Lists split only on explicit separators; numbers must match the
    complete value and unit.
    Preserve the GPA numerator and source evidence without normalization or grading-scale inference.
    This function has no side effects;
    CandidateInput applies final schema limits and enumerations.
    """
    value = raw.strip().strip(";；").strip()
    if not value:
        return None
    if field in TAG_FIELDS:
        if value == "[]":
            return []
        separators = r"[,，;；、]" if field == "skills" else r"[,，;；、/]"
        names = list(
            dict.fromkeys(part.strip() for part in re.split(separators, value) if part.strip())
        )
        # Skill description paragraphs and skill name lists are different inputs; forbidden to treat
        # "familiar with Python etc." as full sentence for model features.
        if field == "skills" and re.search(r"[。！？：:]|熟悉|掌握|精通|了解|擅长|负责", value):
            return None
        return names or None
    if field == "academic_level":
        if value in LEVELS:
            return LEVELS[value]
        return value if value in LEVELS.values() else None
    if field == "in_person_commitment":
        if value in MODES:
            return MODES[value]
        return value if value in MODES.values() else None
    if field == "commit_to_summer":
        return BOOLEANS.get(value.casefold())
    if field == "gpa":
        match = re.fullmatch(
            r"(\d+(?:\.\d+)?)\s*(?:/\s*\d+(?:\.\d+)?)?\s*(?:\([^)]*\)|（[^）]*）)?", value
        )
        return float(match[1]) if match else None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(.*)", value)
    if not match or match[2].casefold() not in NUMBER_UNITS[field]:
        return None
    number = float(match[1])
    if field == "num_publications":
        return int(number) if number.is_integer() else None
    return number


def collect_slots(units):
    """Return suggestions, evidence, issues, and missing fields for all 11 business fields without
    saving confirmed values.

    Scan explicit labels in each section; the skills section also accepts comma lists after a
    category prefix or standalone lists.
    Preserve list names verbatim and mark ambiguous scalar values or unsupported formats unknown,
    then validate against the actual contract.
    Do not log source text or infer absent fields; evidence contains section, one-based line number,
    and original line for owner review.
    """
    if set(SLOT_LABELS) != set(CandidateInput.model_fields) - {"candidate_id"}:
        raise RuntimeError("Resume extraction fields do not cover the recommendation contract.")
    observed = {field: [] for field in SLOT_LABELS}
    evidence = {field: [] for field in SLOT_LABELS}
    issues = {}
    for unit, text in units.items():
        for line_number, line in enumerate(text.splitlines(), 1):
            matches = list(LABEL_PATTERN.finditer(line))
            entries = []
            for index, match in enumerate(matches):
                field = next(
                    key
                    for key, labels in SLOT_LABELS.items()
                    if match["label"].casefold() in {label.casefold() for label in labels}
                )
                end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                raw = re.split(r"[|｜]", line[match.end() : end], maxsplit=1)[0].strip()
                entries.append((field, raw))
            if unit == "skills" and not matches and line.strip():
                raw = re.split(r"[:：]", line.strip(), maxsplit=1)[-1].strip()
                raw = re.sub(r"^[•·*\-]+\s*", "", raw)
                # Unnamed skills only accept explicit list lines; forbidden to infer entire
                # paragraph as skills or tag every word individually.
                if re.search(r"[,，;；、]", raw) or re.fullmatch(r"[\w.+#-]+", raw):
                    if parse_value("skills", raw) is not None:
                        entries.append(("skills", raw))
            for field, raw in entries:
                parsed = parse_value(field, raw)
                evidence[field].append({"unit": unit, "line": line_number, "text": line})
                if parsed is None:
                    issues[field] = "unsupported_format"
                elif parsed not in observed[field]:
                    observed[field].append(parsed)
    values = {}
    for field, options in observed.items():
        if field in issues or not options:
            values[field] = None
        elif field in TAG_FIELDS:
            values[field] = list(dict.fromkeys(name for option in options for name in option))
        elif len(options) > 1:
            values[field] = None
            issues[field] = "conflicting_values"
        else:
            values[field] = options[0]
    # Limits or non-bounded values are definite parsing issues; do not truncate or substitute with
    # other implementations; retain evidence requiring manual input.
    for field, value in values.items():
        if value is not None:
            try:
                profile = CandidateInput.model_validate(
                    {"candidate_id": "extraction", **{field: value}}
                )
            except SchemaError:
                values[field] = None
                issues[field] = "invalid_value"
                logger.warning(
                    "Resume slot requires confirmation field=%s issue=invalid_value", field
                )
            else:
                values[field] = getattr(profile, field)
    logger.info(
        "Resume slots extracted known=%d unknown=%d issues=%d",
        sum(value is not None for value in values.values()),
        sum(value is None for value in values.values()),
        len(issues),
    )
    return {
        "values": values,
        "evidence": evidence,
        "issues": issues,
        "missing": [field for field, value in values.items() if value is None],
    }
