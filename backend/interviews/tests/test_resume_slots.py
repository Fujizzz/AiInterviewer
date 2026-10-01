"""职责：验证推荐字段规则提取的完整性、严格类型、证据及未知/冲突边界。
实现：纯合成单元与实际 CandidateInput；不请求模型、不读取个人文件、不操作数据库。
关联：resume_slots、resume_editor.SLOT_UNITS 及推荐输入契约。
目录：
- ResumeSlotTests：规则契约与保守提取回归。
- ResumeSlotTests.test_all_fields_and_original_units：11 字段完整且不改变 GPA/月/小时单位。
- ResumeSlotTests.test_missing_is_unknown：缺失不是零，不按学校、日期或项目猜测偏好。
- ResumeSlotTests.test_conflicts_require_confirmation：矛盾数值/不支持单位不得选择一个结果。
- ResumeSlotTests.test_skill_lists_and_inline_intent：明确技能列表和行内求职意向保留真实名称。
- ResumeSlotTests.test_limits_and_unsupported_descriptions：契约超限和描述段落保留证据但值未知。
关键变量：
（无模块级变量。）
约束：
这些用例证明规则及契约，不证明任意排版 PDF 的识别质量或推荐效果。
"""

from django.test import SimpleTestCase

from interviews.recommendation.schemas import CandidateInput
from interviews.resume_editor import SLOT_UNITS, split_units
from interviews.resume_slots import collect_slots


class ResumeSlotTests(SimpleTestCase):
    """功能：验证提取；逻辑：合成明确证据与歧义输入；约束：无数据库或供应商执行。"""

    def test_all_fields_and_original_units(self):
        """输入覆盖每个字段的显式标签，输出与真实推荐 schema 完全相同的字段集和严格类型。"""
        result = collect_slots(
            {
                "other": "\n".join(
                    [
                        "技能关键词：Python, Django",
                        "求职意向：后端开发 / 数据工程",
                        "专业：计算机科学",
                        "GPA: 3.8 / 4.0 (Top 5%)",
                        "累计经验：0个月",
                        "当前学业阶段：硕士一年级",
                        "每周可投入时间：20小时/周",
                        "可连续投入时间：6个月",
                        "论文数量：0篇",
                        "工作方式：混合",
                        "可参与暑期工作：否",
                    ]
                )
            }
        )
        values = result["values"]
        self.assertEqual(set(values), set(CandidateInput.model_fields) - {"candidate_id"})
        self.assertEqual(set(values), set(SLOT_UNITS))
        self.assertEqual(
            values,
            {
                "skills": ["Python", "Django"],
                "interests": ["后端开发", "数据工程"],
                "majors": ["计算机科学"],
                "gpa": 3.8,
                "months_experience": 0.0,
                "academic_level": "MS1",
                "hours_per_week": 20.0,
                "length_of_commitment": 6.0,
                "num_publications": 0,
                "in_person_commitment": "Hybrid",
                "commit_to_summer": False,
            },
        )
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["issues"], {})
        self.assertEqual(collect_slots({"skills": "技能：[]"})["values"]["skills"], [])
        self.assertEqual(result["evidence"]["gpa"][0]["line"], 4)
        self.assertIn("4.0", result["evidence"]["gpa"][0]["text"])
        CandidateInput.model_validate({"candidate_id": "fixture", **values})

    def test_missing_is_unknown(self):
        """输入学历、日期和项目；无明确推荐标签的字段为 null，不虚构年级、经验、意愿或论文数。"""
        result = collect_slots(
            {
                "education": "NUS | 计算机 | 硕士 2026.08–2027.10",
                "projects": "AI 项目",
                "experience": "2026.04–2026.08 实习",
            }
        )
        self.assertTrue(all(value is None for value in result["values"].values()))
        self.assertEqual(set(result["missing"]), set(SLOT_UNITS))

    def test_conflicts_require_confirmation(self):
        """不同 GPA 或错误单位输入必须未知并附问题码；重复一致标量不算冲突，不换算周到月。"""
        result = collect_slots(
            {
                "education": "GPA: 3.8\nGPA: 3.5",
                "preferences": "可连续投入时间：6周\n每周可投入小时：20\n每周可投入小时：20",
            }
        )
        self.assertIsNone(result["values"]["gpa"])
        self.assertEqual(result["issues"]["gpa"], "conflicting_values")
        self.assertIsNone(result["values"]["length_of_commitment"])
        self.assertEqual(result["issues"]["length_of_commitment"], "unsupported_format")
        self.assertEqual(result["values"]["hours_per_week"], 20)

    def test_skill_lists_and_inline_intent(self):
        """技能标题分组后分类列表及单标识符可提取；含联系方式的行内意向只取标签后的方向。"""
        units = split_units(
            "求职意向：Agent 开发 / AI Infra | 联系邮箱：demo@example.test\n关键技能\n"
            "Agent: Python, Tool / Function Calling, RAG\nAI Infra: vLLM, Python\n- SQL\n"
        )
        result = collect_slots(units)
        self.assertEqual(
            result["values"]["skills"], ["Python", "Tool / Function Calling", "RAG", "vLLM", "SQL"]
        )
        self.assertEqual(result["values"]["interests"], ["Agent 开发", "AI Infra"])
        self.assertTrue(all(item["unit"] == "skills" for item in result["evidence"]["skills"]))
        prose = collect_slots({"skills": "Python, SQL\n熟悉 Python，负责接口开发。"})
        self.assertEqual(prose["values"]["skills"], ["Python", "SQL"])

    def test_limits_and_unsupported_descriptions(self):
        """超长技能或自然语言熟练程度不得整体作为特征；契约超限产生问题码，证据保留。"""
        result = collect_slots(
            {"skills": "技能：熟悉 Python，负责接口开发", "education": "专业：" + "x" * 513}
        )
        self.assertIsNone(result["values"]["skills"])
        self.assertIsNone(result["values"]["majors"])
        self.assertEqual(result["issues"]["majors"], "invalid_value")
        self.assertTrue(result["evidence"]["majors"])
