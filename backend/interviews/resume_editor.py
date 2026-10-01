"""职责：定义在线简历单元、无模型标题分组和用户确认推荐槽位的保存契约。
实现：识别独立标题；未识别文本保留在 other；resume_slots 单独提取待确认推荐值，保存仍需显式确认。
关联：resume_versions 的 editor/editions/recommendation-profile；复用推荐 CandidateInput 验证槽位。

配置说明：ResumeEditionInput.label 可选 120 字符，units 必填且总正文含标题有界，
slots 默认空字典，由 CandidateInput 严格校验；未知字段拒绝。

目录：
- ResumeEditionInput：严格编辑稿请求，不接受身份字段或未知单元。
- ResumeEditionInput.validate：整体长度和推荐字段校验，不进行语义推断。
- split_units：按独立标题分组原文，所有非标题行保留。
- render_units：将用户填写单元组合为独立面试正文。
- candidate_payload：用版本 UUID 构造推荐输入，缺失字段保持 null。

关键变量：
- UNIT_LABELS：稳定单元 ID 与显示名称，组成编辑稿正文顺序。
- HEADING_UNITS：仅匹配独立标题的中英文标题到单元映射。
- SLOT_UNITS：推荐字段的关联单元说明，不作为字段值推断规则。

约束：
用户保存确认的 slots 是唯一推荐数值/类别来源；不调用模型、不修改训练预处理或单位。
"""

import re

from pydantic import ValidationError as SchemaError
from rest_framework import serializers

from .recommendation.schemas import CandidateInput

UNIT_LABELS = {
    "basic": "个人简介与联系方式",
    "education": "教育经历",
    "experience": "实习与工作经历",
    "projects": "项目经历",
    "skills": "专业技能",
    "awards": "荣誉与证书",
    "publications": "论文与研究成果",
    "preferences": "求职意向",
    "other": "其他信息",
}
HEADING_UNITS = {
    "个人信息": "basic",
    "个人简介": "basic",
    "基本信息": "basic",
    "summary": "basic",
    "profile": "basic",
    "education": "education",
    "教育背景": "education",
    "教育经历": "education",
    "工作经历": "experience",
    "实习经历": "experience",
    "实习与工作经历": "experience",
    "work experience": "experience",
    "experience": "experience",
    "internships": "experience",
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
    "certifications": "awards",
    "论文与研究成果": "publications",
    "论文发表": "publications",
    "开源贡献 & 论文发表": "publications",
    "开源贡献与论文发表": "publications",
    "publications": "publications",
    "求职意向": "preferences",
    "求职目标": "preferences",
    "career objective": "preferences",
    "其他信息": "other",
    "other": "other",
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
    """功能：编辑稿请求；逻辑：有界单元与严格槽位；约束：新快照保存，不原地覆盖原文。"""

    label = serializers.CharField(max_length=120, required=False, default="", allow_blank=True)
    units = serializers.DictField(
        child=serializers.CharField(max_length=200000, allow_blank=True, trim_whitespace=False)
    )
    slots = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        """输入字段，输出完整单元和已确认槽位；未知 ID、超限或无正文拒绝，所有校验无写入。"""
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
    """输入已提取原文，输出稳定单元字典；只按整行标题切换，未匹配内容保留在 other。
    标题本身由单元标签表示；行内容和空白保持原样，无自动补全、模型或数据库副作用。
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
    """输入已校验单元，输出带单元标题的正文；省略空单元但不改写非空单元内的字符。"""
    return "\n\n".join(
        f"{label}\n{units[key]}" for key, label in UNIT_LABELS.items() if units[key].strip()
    )


def candidate_payload(version):
    """输入本人版本，输出现有推荐契约；版本 UUID 为 candidate_id，未确认字段为 null。
    不读取姓名/邮箱，不从自然语言猜测槽位，不改变大小写、月/小时/GPA 单位或模型参数。
    """
    return CandidateInput.model_validate(
        {
            "candidate_id": str(version.pk),
            **version.recommendation_slots,
        }
    ).model_dump()
