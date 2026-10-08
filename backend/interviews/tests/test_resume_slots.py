"""Responsibilities: Verify completeness, strict typing, evidence, and unknown/conflict behavior in
recommendation-slot extraction.
Implementation: Exercise synthetic text with the real CandidateInput schema; no model, personal
file, or database is used.
Related Modules: interviews.resume_slots, interviews.resume_editor, and
interviews.recommendation.schemas.
Declaration Index:
- ResumeSlotTests: Verify conservative rule extraction and slot-contract boundaries.
- ResumeSlotTests.test_all_fields_and_original_units: Check all eleven fields and preserve GPA,
  month, and hour units.
- ResumeSlotTests.test_missing_is_unknown: Ensure missing data remains unknown rather than inferred
  from school, date, or project.
- ResumeSlotTests.test_conflicts_require_confirmation: Reject conflicting values and unsupported
  units without choosing a value.
- ResumeSlotTests.test_skill_lists_and_inline_intent: Extract explicit skill groups and tagged
  inline job intent.
- ResumeSlotTests.test_limits_and_unsupported_descriptions: Retain evidence while rejecting
  over-limit or unsupported descriptions.
- ResumeSlotTests.test_english_degree_and_target_roles: Read explicit majors and target roles.
- ResumeSlotTests.test_degree_without_discipline_is_unknown: Avoid guessing majors or academic year.
- ResumeSlotTests.test_all_fields_with_english_labels: Extract every field from human-readable
  English labels while preserving the recommendation contract and zero/false values.
Variable Index:
None

Constraints:
These cases verify rules and schemas; they do not establish recognition quality on arbitrary PDF
layouts or recommendation effectiveness.
"""

from django.test import SimpleTestCase

from interviews.recommendation.schemas import CandidateInput
from interviews.resume_editor import SLOT_UNITS, split_units
from interviews.resume_slots import collect_slots


class ResumeSlotTests(SimpleTestCase):
    """Functionality: Verify recommendation-slot extraction and validation boundaries.
    Inputs: Synthetic resume-section text with explicit, missing, repeated, conflicting, and
    unsupported evidence.
    Outputs: Assertions over extracted values, issue codes, evidence locations, and CandidateInput
    compatibility.
    Logic: Exercise the pure extraction function under Django's database-free SimpleTestCase.
    Constraints: No external model, vendor, personal file, or database is accessed.
    """

    def test_all_fields_and_original_units(self):
        """Input covers every field with explicit labels; output contains identical field set and
        strict types as real recommendation schema.
        """
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
        """Input includes education, date, and project; fields without explicit recommendation
        labels are null, no fabricated grades, experience, intent, or paper count.
        """
        result = collect_slots(
            {
                "education": "NUS | 计算机 | 硕士 2026.08–2027.10",
                "projects": "AI 项目",
                "experience": "2026.04–2026.08 实习",
            }
        )
        self.assertTrue(all(value is None for value in result["values"].values()))
        self.assertEqual(set(result["values"]), set(CandidateInput.model_fields) - {"candidate_id"})
        self.assertEqual(set(result["missing"]), set(SLOT_UNITS))

    def test_all_fields_with_english_labels(self):
        """Functionality: Verify English resumes can explicitly supply all recommendation fields.
        Inputs: Synthetic human-readable labels including lower-case categories and zero/false.
        Outputs: A complete strictly typed field mapping with no missing or unsupported fields.
        Logic: Extract the text through the real rules and validate against CandidateInput.
        Constraints: No vendor call, unit conversion, inferred dates or persisted user data.
        """
        result = collect_slots(
            {
                "other": "\n".join(
                    [
                        "Skill keywords: Python, SQL",
                        "Interest keywords: AI Infrastructure",
                        "Major keywords: Computer Science",
                        "GPA: 3.8 / 4.0",
                        "Experience (months): 0 months",
                        "Current study stage: Master year 1",
                        "Hours per week: 20 hours/week",
                        "Commitment (months): 6 months",
                        "Publication count: 0 papers",
                        "Working arrangement: hybrid",
                        "Available in summer: No",
                    ]
                )
            }
        )
        expected = {
            "skills": ["Python", "SQL"],
            "interests": ["AI Infrastructure"],
            "majors": ["Computer Science"],
            "gpa": 3.8,
            "months_experience": 0.0,
            "academic_level": "MS1",
            "hours_per_week": 20.0,
            "length_of_commitment": 6.0,
            "num_publications": 0,
            "in_person_commitment": "Hybrid",
            "commit_to_summer": False,
        }
        self.assertEqual(result["values"], expected)
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["issues"], {})
        CandidateInput.model_validate({"candidate_id": "english-fixture", **result["values"]})

    def test_conflicts_require_confirmation(self):
        """Invalid GPA or erroneous unit input must be unknown and accompanied by an error code;
        repeated consistent scalars do not constitute a conflict, and no conversion from weeks to
        months is performed.
        """
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
        """Skill title groups can be extracted into categorized lists with single identifiers; for
        inline intent containing contact information, only the direction after the tag is taken.
        """
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
        """Excessively long skills or natural language proficiency levels must not be treated as
        full features; contract overlimit triggers an error code, and evidence is retained.
        """
        result = collect_slots(
            {"skills": "技能：熟悉 Python，负责接口开发", "education": "专业：" + "x" * 513}
        )
        self.assertIsNone(result["values"]["skills"])
        self.assertIsNone(result["values"]["majors"])
        self.assertEqual(result["issues"]["majors"], "invalid_value")
        self.assertTrue(result["evidence"]["majors"])

    def test_english_degree_and_target_roles(self):
        """Functionality: Fill pending fields from explicit English resume phrases.
        Inputs: Anonymous school/degree lines with pipe-separated or layout-spaced dates and goals.
        Outputs: Named majors, target roles, skills and retained evidence, with no inferred year.
        Logic: Run the actual section parser and slot extractor on a complete minimal resume.
        Constraints: No institution lookup, GPA conversion or date-derived recommendation features.
        """
        units = split_units(
            "Email: test@example.test | Target Roles: Agent Engineering / AI Infrastructure\n"
            "EDUCATION\n"
            "Example University | Master of Technology in Artificial Intelligence Systems"
            "    Aug 2026 - Oct 2027\n"
            "Another University | B.Eng. in Computer Science and Technology | Sep 2022 - Jun 2026\n"
            "GPA: 3.8 / 4.0 (Top 5%)\nCORE SKILLS\n"
            "Agent: Python, Tool / Function Calling, RAG\nAI Infrastructure: ONNX Runtime, vLLM\n"
        )
        result = collect_slots(units)
        self.assertEqual(
            result["values"]["majors"],
            [
                "Artificial Intelligence Systems",
                "Computer Science and Technology",
            ],
        )
        self.assertEqual(result["values"]["interests"], ["Agent Engineering", "AI Infrastructure"])
        self.assertEqual(
            result["values"]["skills"],
            [
                "Python",
                "Tool / Function Calling",
                "RAG",
                "ONNX Runtime",
                "vLLM",
            ],
        )
        self.assertEqual(result["values"]["gpa"], 3.8)
        self.assertIsNone(result["values"]["academic_level"])
        self.assertIsNone(result["values"]["months_experience"])
        self.assertIn("Aug 2026", result["evidence"]["majors"][0]["text"])

    def test_degree_without_discipline_is_unknown(self):
        """Functionality: Leave absent disciplines unknown even when a degree is explicit.
        Inputs: Generic degrees and project prose that mentions a degree-like phrase.
        Outputs: Unknown major and academic level with no fabricated evidence.
        Logic: Require an explicit degree-in phrase in the education section.
        Constraints: Deliberately does not infer majors from institution names or degree categories.
        """
        result = collect_slots(
            {
                "education": "Example University | Master of Science | 2026\n"
                "Doctor of Philosophy\n",
                "projects": "Master of Science in Computing",
            }
        )
        self.assertIsNone(result["values"]["majors"])
        self.assertIsNone(result["values"]["academic_level"])
        self.assertEqual(result["evidence"]["majors"], [])
