"""职责：从简历单元提取待确认推荐字段，完整覆盖 CandidateInput 的业务字段。
实现：识别显式字段标签和技能单元列表，严格解析类型，保留证据；冲突或不支持的格式为未知。
关联：resume_editor 提供单元文本，resume_versions.editor 交付建议；
保存后才由 candidate_payload 用于推荐。

目录：
- parse_value：将带标签的原文值解析为推荐契约类型，不转换 GPA 制或推算日期。
- collect_slots：扫描单元并汇总建议、原文证据、问题和缺失字段，不写库或调用模型。

关键变量：
- SLOT_LABELS：推荐字段的明确中英文标签与字段名，不包含技能词表或专业映射。
- LABEL_PATTERN：定位显式标签和值的边界，允许同一行包含多个字段。
- TAG_FIELDS：以列表表示的字段，保留技能的斜线和大小写。
- NUMBER_UNITS：各数值字段允许的原始单位，不进行换算。
- LEVELS：显式中文在读年级到现有学业阶段代码的映射，不按日期或学历推断年级。
- MODES：显式工作方式到现有类别的对应。
- BOOLEANS：显式暑期意愿的布尔表示。
- logger：仅记录提取数量和非法字段名，不记录证据、姓名或联系方式。

约束：
缺失为 null，明确 0/false 保留；不从联系方式、学校或项目推断偏好/年级/时长。
技能列表只接受显式列表格式；数值、类别冲突不会静默选择一个。所有证据仅走本人私有接口。
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
    """输入推荐字段名及标签后的文本，输出严格类型或 None（无确定解析），未知字段抛 KeyError。
    列表只分隔明确标点，数字需完整匹配单位；GPA 保留分子及原证据，不归一化或推测成绩制。
    无副作用；契约限额与枚举由调用方最终使用 CandidateInput 校验。
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
        # 技能描述段落与技能名称列表是不同输入，禁止把“熟悉 Python 等”整个句子当作模型特征。
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
    """输入稳定单元文本字典，返回全部 11 个业务字段的建议、证据、问题和缺失项，不保存确认值。
    扫描所有单元的显式标签；技能单元允许类别前缀后的逗号列表或独立列表。
    列表合并原样名称，标量多值/无法支持的明确格式设未知；最终通过实际推荐契约验证。
    不记录原文日志，不猜测未提供字段；证据为单元、1 起始行号和原文，供本人核对。
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
                # 未命名技能仅接受明确的列表行，禁止从正文推断整个段落是技能或给每个单词打标签。
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
    # 限额或非有限值是确定的解析问题，不截断或用其他实现替代；保留证据要求人工输入。
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
