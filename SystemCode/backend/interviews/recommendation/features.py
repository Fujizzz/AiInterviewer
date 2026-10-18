"""Responsibilities: Construct the fixed 11-dimensional v4 feature input from validated data.
Implementation: Use only fields supplied by the request; convert missing numeric values to NaN and
preserve the trained feature order.
Related Modules: schemas validates raw values; runtime calls build_features before model inference.

Declaration Index:
- margin: calculates candidate minus job requirement when both values are known.
- build_features: constructs float32 arrays and verifiable missing information in original
  experimental order.

Variable Index:
- FEATURE_NAMES: fixed column names and order for v4-B input.
- LEVELS: academic level encoding consistent with training.

Design Notes:
schemas validate raw types; this layer converts None to NaN, with runtime handling inference.
Skills, specialties, and industries remain exact matches without case normalization or synonym
expansion.
"""

import numpy as np

from .schemas import CandidateInput, JobInput

FEATURE_NAMES = (
    "skill_coverage",
    "interest_matches_industry",
    "major_accepted",
    "work_mode_matches",
    "gpa_margin",
    "experience_months_margin",
    "academic_level_margin",
    "weekly_hours_margin",
    "commitment_months_margin",
    "publications_margin",
    "summer_commitment",
)
LEVELS = {
    "UG1": 1,
    "UG2": 2,
    "UG3": 3,
    "UG4": 4,
    "MS1": 5,
    "MS2": 6,
    "PhD1": 7,
    "PhD2": 8,
    "PhD3": 9,
    "PhD4": 10,
    "PhD5": 11,
}


def margin(candidate: float | None, requirement: float | None) -> float:
    """Function: computes margin; inputs two numeric values, outputs their difference or NaN; never
    fills zero if either is unknown, with no side effects.
    """
    return np.nan if candidate is None or requirement is None else candidate - requirement


def build_features(candidate: CandidateInput, job: JobInput) -> np.ndarray:
    """Function: generates a single 11-dimensional input row; inputs strictly validated candidate
    and job, outputs a float32 array.

    Logic: four matchings, six margins, and summer engagement; maintains NaN if any required source
    is unknown.
    Constraints: known empty skill/specialty lists correspond to zero matching; unknown is not zero;
    no normalization, ID features, or interview information.
    Raises ValueError on extreme input overflow beyond float32 range, never returns infinite
    features.
    """
    values = [np.nan] * len(FEATURE_NAMES)
    if candidate.skills is not None and job.required_skills is not None:
        required = set(job.required_skills)
        values[0] = len(set(candidate.skills) & required) / len(required)
    if candidate.interests is not None and job.industry is not None:
        values[1] = float(job.industry in candidate.interests)
    if candidate.majors is not None and job.acceptable_majors is not None:
        values[2] = float(bool(set(candidate.majors) & set(job.acceptable_majors)))
    if candidate.in_person_commitment is not None and job.job_in_person_commitment is not None:
        values[3] = float(candidate.in_person_commitment == job.job_in_person_commitment)
    values[4] = margin(candidate.gpa, job.min_gpa)
    values[5] = margin(candidate.months_experience, job.min_months_experience)
    values[6] = margin(LEVELS.get(candidate.academic_level), LEVELS.get(job.min_academic_level))
    values[7] = margin(candidate.hours_per_week, job.min_hours_per_week)
    values[8] = margin(candidate.length_of_commitment, job.min_length_of_commitment)
    values[9] = margin(candidate.num_publications, job.min_num_publications)
    values[10] = np.nan if candidate.commit_to_summer is None else float(candidate.commit_to_summer)
    # HTTP allows finite numbers; here, also check float32 representation range to avoid conversion
    # warnings and infinite values entering tree models.
    try:
        values = [float(value) for value in values]
    except OverflowError as exc:
        raise ValueError("Numeric margin is outside the supported float32 range") from exc
    limit = float(np.finfo(np.float32).max)
    if any(not np.isnan(value) and abs(value) > limit for value in values):
        raise ValueError("Numeric margin is outside the supported float32 range")
    return np.asarray(values, dtype=np.float32)
