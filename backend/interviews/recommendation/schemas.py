"""推荐接口的严格输入契约。将JSON省略/null保留为未知，不猜测简历未提供的属性。

目录：
- Profile：所有输入对象的严格校验基类。
- CandidateInput：候选人可选资料，数值单位沿用训练协议。
- JobInput：岗位可选要求；明确空技能要求仍按训练约束拒绝。
- JobsRequest：一个候选人与一个有界岗位列表。
- JobsRequest.unique_jobs：拒绝同一请求重复岗位ID。
- CandidatesRequest：一个岗位与一个有界候选人列表。
- CandidatesRequest.unique_candidates：拒绝同一请求重复候选人ID。

关键变量：
- MAX_ITEMS：一次排序最多100个对象，是HTTP资源上限而非模型阈值。
- Text：包含非空白内容的原样字符串，保留大小写和空格。
- Number：有限且非负的严格数值，不接受布尔或数字字符串。
- Count：有限且非负的严格整数。
- Names：最多256个原样字符串的列表；空列表表示明确为空。
- AcademicLevel：原实验的11个学业阶段代码，不映射产品seniority。
- WorkMode：原始数据的工作方式类别，No Preference仍按类别精确匹配。

关键状态说明：
Profile.model_config拒绝额外字段、类型强转和非有限数；所有可选资料默认None。
majors合并原实验的本科/第二/硕士已知专业，空列表和未知分开。
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
    """功能：统一严格校验；输入JSON对象，输出类型化资料；额外字段和非法类型抛ValidationError。

    逻辑：拒绝NaN/Infinity，HTTP未知必须为null；无数据库、网络或日志副作用。
    """

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class CandidateInput(Profile):
    """功能：描述一名候选人；输入ID及可选资料，输出验证对象。

    逻辑：省略资料保留None；明确0/false/空列表保留；约束：月/小时/GPA单位由调用者确认，
    不从技能或项目推断学历、经验和工作偏好，不自动填补或缩放GPA。
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
    """功能：描述一个岗位；输入ID及可选要求，输出验证对象。

    逻辑：未知要求不解释为无要求；约束：required_skills可未知但已知时不可为空，
    所有数值采用训练单位，不将seniority等其他业务标签映射到学业阶段。
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
    """功能：岗位排序请求；输入一人和1至100个岗位；输出有序候选池契约，不做数据库查询。"""

    candidate: CandidateInput
    jobs: Annotated[list[JobInput], Field(min_length=1, max_length=MAX_ITEMS)]

    @model_validator(mode="after")
    def unique_jobs(self):
        """功能：验证岗位ID唯一；输入已验证实例，输出self；重复抛ValueError，无去重副作用。"""
        if len({job.job_id for job in self.jobs}) != len(self.jobs):
            raise ValueError("Duplicate job IDs are not allowed")
        return self


class CandidatesRequest(Profile):
    """功能：候选人排序请求；输入一岗和1至100人；输出有序候选池契约，不读取面试记录。"""

    job: JobInput
    candidates: Annotated[list[CandidateInput], Field(min_length=1, max_length=MAX_ITEMS)]

    @model_validator(mode="after")
    def unique_candidates(self):
        """功能：验证候选人ID唯一；输入已验证实例，输出self；重复抛ValueError，不静默合并。"""
        if len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("Duplicate candidate IDs are not allowed")
        return self
