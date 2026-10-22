"""Responsibilities: Define the single structured-resume and recommendation field contracts.
Implementation: Strict Pydantic fields preserve unknown values, original units and source-line IDs.
Related Modules: app.parsing.resume validates model output; resume_profile persists it; the editor,
recommendation API and interview adapter project this same document without another extraction.
Declaration Index:
- ResumeFacts: Eleven shared, nullable recommendation fields with original experimental types.
- ResumeFactEvidence: Fixed evidence keys for the eleven shared facts.
- ResumeSectionLines: Stable nine-section assignment of original physical line numbers.
- ResumeProject: Interview experience facts anchored to complete original source lines.
- ResumeExtraction: Model output and persisted canonical document, including field evidence.
Variable Index:
- Text: Nonblank bounded string, preserving original capitalization and whitespace.
- Number: Finite nonnegative numeric value; booleans and numeric strings are rejected.
- Count: Nonnegative integer, preserving explicit zero.
- Names: Bounded original-name list; empty is distinct from unknown.
- AcademicLevel: Existing study-year categories, not inferred from degree or dates.
- WorkMode: Existing recommendation work-mode categories.
- LineNumber: Positive strict physical line number in the supplied source text.
- UNIT_LABELS: Stable section identifiers and display/export headings.
- SLOT_UNITS: Field-to-section associations used only for presentation.
Constraints:
This module performs no inference or I/O. Callers validate references against the original text.
Model evidence and structured content do not by themselves establish factual correctness.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=512, pattern=r"\S")]
Number = Annotated[float, Field(ge=0)]
Count = Annotated[int, Field(ge=0)]
Names = Annotated[list[Text], Field(max_length=256)]
AcademicLevel = Literal[
    "UG1", "UG2", "UG3", "UG4", "MS1", "MS2", "PhD1", "PhD2", "PhD3", "PhD4", "PhD5"
]
WorkMode = Literal["In Person", "Online", "No Preference", "Hybrid"]
LineNumber = Annotated[int, Field(ge=1, strict=True)]
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


class ResumeFacts(BaseModel):
    """Functionality: Supply identical names, types and defaults to extraction and recommendation.
    Logic: model_config rejects extra fields, coercion, NaN and infinity; omitted facts remain None.
    Constraints: No GPA conversion or date arithmetic; explicit zero, false and [] are preserved.
    skills/interests/majors contain original names; month/hour counts use their stated units.
    """

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    skills: Names | None = None
    interests: Names | None = None
    majors: Names | None = None
    in_person_commitment: WorkMode | None = None
    gpa: Number | None = None
    months_experience: Number | None = None
    academic_level: AcademicLevel | None = None
    hours_per_week: Number | None = None
    length_of_commitment: Number | None = None
    num_publications: Count | None = None
    commit_to_summer: bool | None = None


class ResumeFactEvidence(BaseModel):
    """Functionality: Constrain evidence to the same named facts used by all consumers.
    Logic: Each list contains positive source line IDs; absent evidence is an empty list.
    Constraints: Extra keys and coerced IDs are rejected; runtime validation checks source bounds.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    skills: list[LineNumber] = Field(default_factory=list)
    interests: list[LineNumber] = Field(default_factory=list)
    majors: list[LineNumber] = Field(default_factory=list)
    in_person_commitment: list[LineNumber] = Field(default_factory=list)
    gpa: list[LineNumber] = Field(default_factory=list)
    months_experience: list[LineNumber] = Field(default_factory=list)
    academic_level: list[LineNumber] = Field(default_factory=list)
    hours_per_week: list[LineNumber] = Field(default_factory=list)
    length_of_commitment: list[LineNumber] = Field(default_factory=list)
    num_publications: list[LineNumber] = Field(default_factory=list)
    commit_to_summer: list[LineNumber] = Field(default_factory=list)


class ResumeSectionLines(BaseModel):
    """Functionality: Assign source lines to exactly one editor section.
    Logic: Each named list holds positive physical line numbers; runtime validation checks coverage.
    Constraints: Lists retain source order. Blank lines may be omitted; nonblank lines may not.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    basic: list[LineNumber] = Field(default_factory=list)
    education: list[LineNumber] = Field(default_factory=list)
    experience: list[LineNumber] = Field(default_factory=list)
    projects: list[LineNumber] = Field(default_factory=list)
    skills: list[LineNumber] = Field(default_factory=list)
    awards: list[LineNumber] = Field(default_factory=list)
    publications: list[LineNumber] = Field(default_factory=list)
    preferences: list[LineNumber] = Field(default_factory=list)
    other: list[LineNumber] = Field(default_factory=list)


class ResumeProject(BaseModel):
    """Functionality: Preserve distinct experience facts for interview projection.
    Logic: name identifies an experience; technologies, claims and metrics stay in its grouping;
    source_lines identifies every original line belonging to this experience, including employer,
    role, dates, responsibilities and outcomes. The parser validates coverage and derives IDs.
    Constraints: At least one source line is required; no model-generated source IDs are accepted.
    Missing descriptions are None and fact lists are []. Agent contracts remain separate.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    domain: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    source_lines: list[LineNumber] = Field(min_length=1)


class ResumeExtraction(ResumeFacts):
    """Functionality: Represent one canonical model extraction for every resume consumer.
    Logic: Inherited facts are the only global skill/preference values. section_lines locates
    editor text; heading_lines marks standalone headings omitted only from editable section bodies.
    field_evidence maps fact names to source lines; projects holds grounded interview experiences.
    experience_context_lines retains non-experience content within experience sections, such as
    no-employment notes or skills placed there by the user; existing descriptions carry this text.
    Constraints: candidate_name is optional. The backend validates line coverage, heading placement
    and evidence before persistence. User confirmations can override the same inherited fields.
    """

    candidate_name: str | None = None
    section_lines: ResumeSectionLines
    heading_lines: list[LineNumber] = Field(default_factory=list)
    experience_context_lines: list[LineNumber] = Field(default_factory=list)
    field_evidence: ResumeFactEvidence = Field(default_factory=ResumeFactEvidence)
    projects: list[ResumeProject] = Field(default_factory=list)
