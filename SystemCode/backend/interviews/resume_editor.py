"""Responsibilities: Define online resume sections, deterministic heading grouping, and confirmed
recommendation slots.
Implementation: Recognize standalone headings and preserve unmatched text under other; suggestions
require explicit confirmation.
Related Modules: resume_versions exposes editor/editions/profile endpoints; CandidateInput validates
saved slots.

Declaration Index:
- ResumeEditionInput: Validate an edit request without identity fields or unknown sections.
- ResumeEditionInput.validate: Check total length and recommendation fields without semantic
  inference.
- split_units: Group source text by standalone headings while retaining every non-heading line.
- render_units: Combine user-edited sections into standalone interview text.
- candidate_payload: Build recommendation input from a version UUID and leave missing fields null.

Variable Index:
- UNIT_LABELS: Stable section IDs and English display labels in resume-body order.
- HEADING_UNITS: Chinese and English standalone headings mapped to section IDs for input
  compatibility.
- SLOT_UNITS: Associates recommendation fields with sections; it does not infer field values.

Constraints:
User-confirmed saved slots are the sole source of recommendation values/categories. This module
calls no model and does not change training preprocessing or units.
"""

import re

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
    "教育背景": "education",
    "教育经历": "education",
    "工作经历": "experience",
    "实习经历": "experience",
    "实习与工作经历": "experience",
    "work experience": "experience",
    "experience": "experience",
    "internships": "experience",
    "internships and work experience": "experience",
    "项目经历": "projects",
    "项目经验": "projects",
    "projects": "projects",
    "专业技能": "skills",
    "技能": "skills",
    "技能清单": "skills",
    "关键技能": "skills",
    "skills": "skills",
    "technical skills": "skills",
    "荣誉与证书": "awards",
    "荣誉奖项": "awards",
    "获奖情况": "awards",
    "awards": "awards",
    "awards and certifications": "awards",
    "certifications": "awards",
    "论文与研究成果": "publications",
    "论文发表": "publications",
    "开源贡献 & 论文发表": "publications",
    "开源贡献与论文发表": "publications",
    "publications": "publications",
    "publications and research": "publications",
    "求职意向": "preferences",
    "求职目标": "preferences",
    "career objective": "preferences",
    "career preferences": "preferences",
    "其他信息": "other",
    "other": "other",
    "other information": "other",
}
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
    """Validate a bounded section-based edit request and strict slots; save as a new snapshot
    without replacing source text.
    """

    label = serializers.CharField(max_length=120, required=False, default="", allow_blank=True)
    units = serializers.DictField(
        child=serializers.CharField(max_length=200000, allow_blank=True, trim_whitespace=False)
    )
    slots = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        """Validate supplied sections and slots without writes; reject unknown IDs, oversized
        content, or an empty body.
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
            CandidateInput.model_validate({"candidate_id": "validation-only", **slots})
        except SchemaError as exc:
            fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
            raise serializers.ValidationError({"slots": fields}) from exc
        attrs.update(units=units)
        return attrs


def split_units(text):
    """Split extracted source text into stable sections using standalone headings, retaining
    unmatched lines in other.

    Section labels represent headings; preserve line content and whitespace without completion,
    model calls, or database effects.
    """
    parts = {key: [] for key in UNIT_LABELS}
    current = "other"
    for line in text.splitlines(keepends=True):
        heading = re.sub(r"^[\s#•\d.、）)]+", "", line.strip()).rstrip(":：").strip().casefold()
        if heading in HEADING_UNITS:
            current = HEADING_UNITS[heading]
        else:
            parts[current].append(line)
    return {key: "".join(lines) for key, lines in parts.items()}


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
