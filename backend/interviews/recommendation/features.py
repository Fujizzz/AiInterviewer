"""从已验证资料构造固定11维v4输入。只使用实际提供的字段，不依赖研究目录或pandas。

目录：
- margin：双方数值已知时计算候选人减岗位要求。
- build_features：按原实验顺序构造float32数组及可核对的缺失信息。

关键变量：
- FEATURE_NAMES：v4-B固定输入列名和顺序。
- LEVELS：与训练相同的学业阶段编码。

设计说明：
schemas验证原始类型；本层将None转NaN，runtime处理推理。
技能/专业/行业保持原样精确匹配，不添加大小写归一化或同义词扩展。
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
    """功能：计算余量；输入双方数值，输出差或NaN；任一未知时不填零，无外部副作用。"""
    return np.nan if candidate is None or requirement is None else candidate - requirement


def build_features(candidate: CandidateInput, job: JobInput) -> np.ndarray:
    """功能：生成一行11维输入；输入严格校验后的候选人与岗位，输出float32数组。

    逻辑：四项匹配、六项余量和暑期投入；任一必要来源未知时保持NaN。
    约束：已知空技能/专业列表对应零匹配；未知不是零；无归一化、ID特征或面试信息。
    极端有限输入溢出float32时抛ValueError，不返回无穷特征。
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
    # HTTP允许有限数；这里还需检查float32表示范围，避免转换警告和无穷进入树模型。
    try:
        values = [float(value) for value in values]
    except OverflowError as exc:
        raise ValueError("Numeric margin is outside the supported float32 range") from exc
    limit = float(np.finfo(np.float32).max)
    if any(not np.isnan(value) and abs(value) > limit for value in values):
        raise ValueError("Numeric margin is outside the supported float32 range")
    return np.asarray(values, dtype=np.float32)
