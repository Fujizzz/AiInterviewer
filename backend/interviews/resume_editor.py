"""Responsibilities: Define online resume sections, deterministic heading grouping, and confirmed
recommendation slots.
Implementation: Normalize heading spelling for matching without rewriting section bodies; recognize
common English/Chinese headings and move an unheaded contact preamble into basic information.
Unknown preambles remain under other; recommendation suggestions require explicit confirmation.
Saved edition slots contain the full CandidateInput business field set, with unknowns set to null.
Related Modules: resume_versions exposes editor/editions/profile endpoints; CandidateInput validates
saved slots.

Declaration Index:
- ResumeEditionInput: Validate an edit request without identity fields or unknown sections.
- ResumeEditionInput.validate: Check total length and materialize all recommendation fields with
  null for omitted values, without semantic inference.
- normalize_heading: Normalize heading decoration, spacing and English conjunction spelling.
- split_units: Group source text by standalone headings while retaining every non-heading line.
- section_warnings: Locate repeated long passages for explicit review without deleting text.
- render_units: Combine user-edited sections into standalone interview text.
- candidate_payload: Build recommendation input from a version UUID and leave missing fields null.

Variable Index:
- UNIT_LABELS: Stable section IDs and English display labels in resume-body order.
- HEADING_UNITS: Chinese and English standalone headings mapped to section IDs for input
  compatibility.
- CONTACT_PATTERN: Explicit email/phone labels that identify an unheaded contact preamble.
- logger: Record section counts and preamble classification without personal data.
- SLOT_UNITS: Associates recommendation fields with sections; it does not infer field values.

Constraints:
User-confirmed saved slots are the sole source of recommendation values/categories. This module
calls no model and does not change training preprocessing or units.
"""

import logging
import re
import unicodedata

from pydantic import ValidationError as SchemaError
from rest_framework import serializers

from .recommendation.schemas import CandidateInput

UNIT_LABELS = {
    "basic": "Profile and Contact Information",
    "education": "Education",
    "experience": "Internships and Work Experience",
    "projects": "Projects",
    "skills": "Skills",
    "awards": "Awards and Certifications",
    "publications": "Publications and Research",
    "preferences": "Career Preferences",
    "other": "Other Information",
}
HEADING_UNITS = {
    "个人信息": "basic",
    "个人简介": "basic",
    "基本信息": "basic",
    "summary": "basic",
    "profile": "basic",
    "profile and contact information": "basic",
    "education": "education",
    "educational background": "education",
    "academic background": "education",
    "教育背景": "education",
    "教育经历": "education",
    "工作经历": "experience",
    "实习经历": "experience",
    "实习与工作经历": "experience",
    "work experience": "experience",
    "internship experience": "experience",
    "professional experience": "experience",
    "employment history": "experience",
    "internships and employment": "experience",
    "experience": "experience",
    "internships": "experience",
    "internships and work experience": "experience",
    "项目经历": "projects",
    "项目经验": "projects",
    "projects": "projects",
    "project experience": "projects",
    "selected projects": "projects",
    "专业技能": "skills",
    "技能": "skills",
    "技能清单": "skills",
    "关键技能": "skills",
    "skills": "skills",
    "technical skills": "skills",
    "core skills": "skills",
    "skills and technologies": "skills",
    "荣誉与证书": "awards",
    "荣誉奖项": "awards",
    "获奖情况": "awards",
    "awards": "awards",
    "honors and awards": "awards",
    "honours and awards": "awards",
    "awards and certifications": "awards",
    "certifications": "awards",
    "论文与研究成果": "publications",
    "论文发表": "publications",
    "开源贡献 and 论文发表": "publications",
    "开源贡献与论文发表": "publications",
    "publications": "publications",
    "publications and research": "publications",
    "open source contributions and publication": "publications",
    "open source contributions and publications": "publications",
    "求职意向": "preferences",
    "求职目标": "preferences",
    "career objective": "preferences",
    "career preferences": "preferences",
    "其他信息": "other",
    "other": "other",
    "other information": "other",
}
CONTACT_PATTERN = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|(?:e-?mail|phone|mobile|电话|手机|邮箱)\s*[:：]",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)
SLOT_UNITS = {
    "skills": "skills",
    "interests": "preferences",
    "majors": "education",
    "gpa": "education",
    "academic_level": "education",
    "months_experience": "experience",
    "num_publications": "publications",
    "in_person_commitment": "preferences",
    "hours_per_week": "preferences",
    "length_of_commitment": "preferences",
    "commit_to_summer": "preferences",
}


class ResumeEditionInput(serializers.Serializer):
    """Validate a bounded section-based edit request and complete strict slots; save as a new
    snapshot without replacing source text. Omitted business fields become null.
    """

    label = serializers.CharField(max_length=120, required=False, default="", allow_blank=True)
    units = serializers.DictField(
        child=serializers.CharField(max_length=200000, allow_blank=True, trim_whitespace=False)
    )
    slots = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        """Functionality: Validate section/slot input and complete the recommendation field set.
        Inputs: Deserialized attrs and original request fields from this serializer instance.
        Outputs: Validated attributes with all sections and all CandidateInput business fields.
        Logic: Reject invalid content/types, then use schema defaults for omitted slots; retain
        explicit zero, false, empty lists and null. No semantic inference or database writes.
        Constraints: Caller identity is never accepted; invalid supplied values still raise
        ValidationError instead of being converted to null. Existing historical rows are untouched.
        """
        if set(self.initial_data) - {"label", "units", "slots"}:
            raise serializers.ValidationError("Unknown edition fields.")
        if set(attrs["units"]) - set(UNIT_LABELS):
            raise serializers.ValidationError("Unknown resume units.")
        units = {key: attrs["units"].get(key, "") for key in UNIT_LABELS}
        text = render_units(units)
        if not text.strip() or len(text) > 200000:
            raise serializers.ValidationError(
                "Resume text must be nonempty and at most 200000 characters."
            )
        slots = attrs["slots"]
        if not isinstance(slots, dict) or set(slots) - set(SLOT_UNITS):
            raise serializers.ValidationError("Unknown recommendation slots.")
        try:
            profile = CandidateInput.model_validate({"candidate_id": "validation-only", **slots})
        except SchemaError as exc:
            fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
            raise serializers.ValidationError({"slots": fields}) from exc
        attrs.update(units=units, slots=profile.model_dump(exclude={"candidate_id"}))
        return attrs


def normalize_heading(line):
    """Functionality: Produce a lookup key for a possible standalone heading.
    Inputs: One original source line, including optional decoration and newline.
    Outputs: A lowercase, whitespace-collapsed key; source text is never changed.
    Logic: Normalize Unicode width, remove heading prefixes/trailing colons, and equate ampersands
    with 'and' and hyphens with spaces so common English titles share one alias.
    Constraints: Only exact dictionary matches become headings; prose containing a title stays text.
    """
    heading = unicodedata.normalize("NFKC", line).strip()
    heading = re.sub(r"^[\s#•\d.、）)]+", "", heading).rstrip(":：").strip().casefold()
    heading = re.sub(r"[-‐‑–—]", " ", heading).replace("&", " and ")
    return " ".join(heading.split())


def split_units(text):
    """Functionality: Group extracted text into editor sections without changing source content.
    Inputs: Complete extracted resume text with optional standalone section headings.
    Outputs: All stable section IDs mapped to their original non-heading lines.
    Logic: Match normalized headings; buffer the preamble separately and place it in basic only
    when it contains explicit contact evidence, otherwise in other. Preserve body whitespace and
    repeated lines for user review; do not guess dates or silently remove content.
    Constraints: Only section placement changes. No database/model calls or recommendation-value
    confirmation; logs include section and preamble counts only.
    """
    parts = {key: [] for key in UNIT_LABELS}
    preamble = []
    current = None
    for line in text.splitlines(keepends=True):
        heading = normalize_heading(line)
        if heading in HEADING_UNITS:
            current = HEADING_UNITS[heading]
        else:
            (preamble if current is None else parts[current]).append(line)
    target = "basic" if CONTACT_PATTERN.search("".join(preamble)) else "other"
    parts[target] = preamble + parts[target]
    logger.info(
        "Resume sections grouped nonempty=%d preamble_lines=%d preamble_unit=%s",
        sum(bool("".join(lines).strip()) for lines in parts.values()),
        len(preamble),
        target,
    )
    return {key: "".join(lines) for key, lines in parts.items()}


def section_warnings(units):
    """Functionality: Flag repeated long passages in editable sections for human review.
    Inputs: Section IDs mapped to source or saved edition text.
    Outputs: At most five warnings containing section, one-based line and a short source excerpt.
    Logic: Index exact windows of 16 whitespace-separated tokens within each section. Extend a
    non-overlapping repeated window to its full match and emit one warning per repeated passage.
    Constraints: At least 80 characters must repeat. Formatting whitespace is ignored only for
    comparison; no content is removed, rewritten or treated as definitely erroneous. Complexity
    is linear in token count with a fixed-size window; warning text is private API data, never logs.
    """
    warnings = []
    for unit, text in units.items():
        tokens = list(re.finditer(r"\S+", text))
        words = [token.group() for token in tokens]
        seen = {}
        index = 0
        while index + 16 <= len(words):
            window = tuple(words[index : index + 16])
            earlier = seen.setdefault(window, index)
            if earlier + 16 <= index and len(" ".join(window)) >= 80:
                length = 16
                while (
                    index + length < len(words)
                    and earlier + length < index
                    and words[earlier + length] == words[index + length]
                ):
                    length += 1
                warnings.append(
                    {
                        "code": "repeated_passage",
                        "unit": unit,
                        "line": text.count("\n", 0, tokens[index].start()) + 1,
                        "text": " ".join(window),
                    }
                )
                if len(warnings) == 5:
                    return warnings
                index += length
            else:
                index += 1
    return warnings


def render_units(units):
    """Render validated sections with their labels, omitting empty sections and preserving
    characters inside non-empty sections.
    """
    return "\n\n".join(
        f"{label}\n{units[key]}" for key, label in UNIT_LABELS.items() if units[key].strip()
    )


def candidate_payload(version):
    """Build the existing recommendation contract from the user's version, using its UUID as
    candidate_id.

    Do not read names or email, infer slots from prose, or change case, month/hour/GPA units, or
    model parameters.
    """
    return CandidateInput.model_validate(
        {
            "candidate_id": str(version.pk),
            **version.recommendation_slots,
        }
    ).model_dump()
