"""Responsibilities: Define strict input contracts for recommendation requests.
Implementation: Preserve null or omitted JSON fields as unknown; do not infer values absent from the
supplied resume data.
Related Modules: recommendation.api validates requests with these schemas; features consumes the
validated values.

Declaration Index:
- Profile: strict validation base class for all input objects.
- CandidateInput: optional candidate data, with units following training protocol.
- JobInput: optional job requirements; explicitly empty skill requirements still rejected per
  training constraints.
- JobsRequest: one candidate and bounded list of jobs.
- JobsRequest.unique_jobs: rejects duplicate job IDs within same request.
- CandidatesRequest: one job and bounded list of candidates.
- CandidatesRequest.unique_candidates: rejects duplicate candidate IDs within same request.

Variable Index:
- MAX_ITEMS: maximum 100 objects per sort, HTTP resource limit, not model threshold.
- Text: original string with non-whitespace content, preserving case and spaces.
- Number: strict finite non-negative numeric value; rejects boolean or numeric strings.
- Count: strict finite non-negative integer.
- Names: list of up to 256 original strings; empty list means explicitly empty.
- AcademicLevel: 11 original academic stage codes from experiment, not mapped to product seniority.
- WorkMode: original work mode category; No Preference still matched exactly by category.

Key State Notes:
Profile.model_config rejects extra fields, type coercion, and non-finite numbers; all optional data
defaults to None.
majors merge original undergraduate/second/master known majors; empty list and unknown are kept
separate.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_ITEMS = 100
Text = Annotated[str, Field(min_length=1, max_length=512, pattern=r"\S")]
Number = Annotated[float, Field(ge=0)]
Count = Annotated[int, Field(ge=0)]
Names = Annotated[list[Text], Field(max_length=256)]
AcademicLevel = Literal[
    "UG1", "UG2", "UG3", "UG4", "MS1", "MS2", "PhD1", "PhD2", "PhD3", "PhD4", "PhD5"
]
WorkMode = Literal["In Person", "Online", "No Preference", "Hybrid"]


class Profile(BaseModel):
    """Function: unified strict validation; inputs JSON object, outputs typed data; extra fields and
    invalid types raise ValidationError.

    Logic: rejects NaN/Infinity; HTTP unknown must be null; no database, network, or logging side
    effects.
    """

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class CandidateInput(Profile):
    """Function: describes a candidate; inputs ID and optional data, outputs validated object.

    Logic: omitted data preserved as None; explicit 0/false/empty list preserved; constraint:
    month/hour/GPA units confirmed by caller,
    no inference of education, experience, or work preference from skills or projects, no automatic
    GPA filling or scaling.
    """

    candidate_id: Text
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


class JobInput(Profile):
    """Function: describes a job; inputs ID and optional requirements, outputs validated object.

    Logic: unknown requirements not interpreted as no requirements; constraint: required_skills may
    be unknown but must not be empty if known,
    all numerical values use training units; no mapping of other business tags like seniority to
    academic level.
    """

    job_id: Text
    required_skills: Annotated[Names, Field(min_length=1)] | None = None
    industry: Text | None = None
    acceptable_majors: Names | None = None
    job_in_person_commitment: WorkMode | None = None
    min_gpa: Number | None = None
    min_months_experience: Number | None = None
    min_academic_level: AcademicLevel | None = None
    min_hours_per_week: Number | None = None
    min_length_of_commitment: Number | None = None
    min_num_publications: Count | None = None


class JobsRequest(Profile):
    """Function: job ranking request; inputs one person and 1 to 100 jobs; outputs ordered candidate
    pool contract, without database query.
    """

    candidate: CandidateInput
    jobs: Annotated[list[JobInput], Field(min_length=1, max_length=MAX_ITEMS)]

    @model_validator(mode="after")
    def unique_jobs(self):
        """Function: validates job ID uniqueness; inputs validated instance, outputs self;
        duplicates raise ValueError, no deduplication side effect.
        """
        if len({job.job_id for job in self.jobs}) != len(self.jobs):
            raise ValueError("Duplicate job IDs are not allowed")
        return self


class CandidatesRequest(Profile):
    """Function: candidate ranking request; inputs one job and 1 to 100 people; outputs ordered
    candidate pool contract, without reading interview records.
    """

    job: JobInput
    candidates: Annotated[list[CandidateInput], Field(min_length=1, max_length=MAX_ITEMS)]

    @model_validator(mode="after")
    def unique_candidates(self):
        """Function: validates candidate ID uniqueness; inputs validated instance, outputs self;
        duplicates raise ValueError, no silent merging.
        """
        if len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("Duplicate candidate IDs are not allowed")
        return self
