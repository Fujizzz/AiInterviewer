"""职责：验证在线单元编辑、独立快照、私有来源及推荐槽位的真实数据库/API 契约。
实现：隔离测试用户与数据库，真实 DRF 校验和文件响应；不调用外部提取或收费模型。
关联：resume_editor、resume_models、resume_versions 和现有 CandidateInput。
目录：
- ResumeEditorTests：独立编辑版本的持久化与权限回归。
- ResumeEditorTests.setUp：建立本人、其他用户和虚构 ready 原件。
- ResumeEditorTests.test_independent_editions_and_export：原件不可覆盖，连续保存可追溯，引用受保护。
- ResumeEditorTests.test_slots_reusable_without_inference：严格槽位可直接组成推荐请求，未知不猜测。
- ResumeEditorTests.test_invalid_editions_do_not_write：
  未知字段、超长正文、非法类型拒绝且不新增版本。
- ResumeEditorTests.test_owner_and_ready_boundaries：所有新增接口保持本人授权与 ready 前置条件。
- ResumeEditorTests.test_heading_grouping_retains_content：标题分组保留正文，未识别段落不丢失。
- ResumeEditorTests.test_extracted_fields_are_not_confirmed_until_saved：
  提取覆盖契约，GET 不写库，编辑稿保留人工清空。
关键变量：
（无模块级变量。）

约束：
API 与数据库真实执行；测试不能证明真实 PDF 提取、模型效果或浏览器排版。
"""

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from interviews.recommendation.schemas import JobsRequest
from interviews.resume_editor import UNIT_LABELS, split_units
from interviews.resume_models import ResumeVersion


class ResumeEditorTests(APITestCase):
    """功能：验证编辑契约；逻辑：使用真实授权 API；约束：所有记录只在测试数据库生成。"""

    def setUp(self):
        """无外部输入；建立两个隔离账号和虚构原件字节，认证本人，不解析 PDF。"""
        self.owner = get_user_model().objects.create_user(username="editor-owner")
        self.other = get_user_model().objects.create_user(username="editor-other")
        self.original = ResumeVersion.objects.create(
            owner=self.owner,
            label="Original",
            status="ready",
            original_name="sample.pdf",
            original_pdf=b"%PDF-test-only",
            text="教育经历\n计算机专业\n项目经历\nPython API\n",
        )
        self.url = f"/api/resume-versions/{self.original.pk}/"
        self.client.force_authenticate(self.owner)

    def test_independent_editions_and_export(self):
        """连续保存保留根和基线、原字节及原正文；下载独立正文，引用版本删除返回 409。"""
        first = self.client.post(
            self.url + "editions/",
            {
                "label": "Backend",
                "units": {"projects": "缓存项目\n优化延迟 <script>"},
            },
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        edition = ResumeVersion.objects.get(pk=first.data["id"])
        self.assertEqual(edition.source_version, self.original)
        self.assertEqual(edition.edited_from, self.original)
        self.assertIsNone(edition.original_pdf)
        self.assertEqual(edition.text, "项目经历\n缓存项目\n优化延迟 <script>")
        self.assertFalse(edition.is_current)
        edition_url = f"/api/resume-versions/{edition.pk}/"
        second = self.client.post(
            edition_url + "editions/",
            {
                "units": {"projects": "第二稿"},
            },
            format="json",
        )
        self.assertEqual(second.status_code, 201)
        derived = ResumeVersion.objects.get(pk=second.data["id"])
        self.assertEqual(derived.source_version, self.original)
        self.assertEqual(derived.edited_from, edition)
        self.original.refresh_from_db()
        edition.refresh_from_db()
        self.assertEqual(self.original.text, "教育经历\n计算机专业\n项目经历\nPython API\n")
        self.assertEqual(self.client.get(self.url + "download/").content, b"%PDF-test-only")
        exported = self.client.get(edition_url + "export/")
        self.assertEqual(exported.content.decode("utf-8"), edition.text)
        self.assertEqual(exported["Cache-Control"], "no-store, private")
        editor = self.client.get(edition_url + "editor/").data
        self.assertEqual(editor["units"]["projects"], "缓存项目\n优化延迟 <script>")
        self.assertEqual(editor["original_text"], self.original.text)
        self.assertEqual(self.client.delete(self.url).status_code, 409)
        self.assertEqual(self.client.delete(edition_url).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/resume-versions/{derived.pk}/").status_code, 204)

    def test_slots_reusable_without_inference(self):
        """确认 0/false/[] 保持；自然语言不推断未知字段；推荐 JSON 通过现有真实 schema。"""
        initial = self.client.get(self.url + "editor/").data
        self.assertEqual(set(initial["units"]), set(UNIT_LABELS))
        self.assertIsNone(initial["slots"]["skills"])
        self.assertIsNone(initial["slots"]["majors"])
        response = self.client.post(
            self.url + "editions/",
            {
                "units": {"skills": "Python", "projects": "API"},
                "slots": {
                    "skills": ["Python"],
                    "interests": [],
                    "months_experience": 0,
                    "num_publications": 0,
                    "commit_to_summer": False,
                    "gpa": None,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        profile = self.client.get(
            f"/api/resume-versions/{response.data['id']}/recommendation-profile/"
        ).data
        candidate = profile["candidate"]
        self.assertEqual(candidate["skills"], ["Python"])
        self.assertEqual(candidate["interests"], [])
        self.assertEqual(candidate["months_experience"], 0)
        self.assertIs(candidate["commit_to_summer"], False)
        self.assertIsNone(candidate["academic_level"])
        self.assertIsNone(candidate["gpa"])
        request = JobsRequest.model_validate(
            {
                "candidate": candidate,
                "jobs": [
                    {
                        "job_id": "api-engineer",
                        "required_skills": ["Python"],
                    }
                ],
            }
        )
        self.assertEqual(request.candidate.candidate_id, response.data["id"])

    def test_invalid_editions_do_not_write(self):
        """严格校验拒绝未知 ID、无正文、超长总正文和非法字段类型；原件/版本数不改变。"""
        invalid = [
            {"units": {}},
            {"units": {"unknown": "Text"}},
            {"units": {"projects": "x" * 200000}},
            {"units": {"projects": "API"}, "owner": self.other.pk},
            {"units": {"projects": "API"}, "slots": {"candidate_id": "other"}},
            {"units": {"projects": "API"}, "slots": {"months_experience": "2"}},
            {"units": {"projects": "API"}, "slots": {"num_publications": 1.5}},
            {"units": {"projects": "API"}, "slots": {"commit_to_summer": "false"}},
            {"units": {"projects": "API"}, "slots": {"academic_level": "Senior"}},
        ]
        for body in invalid:
            with self.subTest(body_keys=list(body)):
                self.assertEqual(
                    self.client.post(self.url + "editions/", body, format="json").status_code, 400
                )
        self.assertEqual(ResumeVersion.objects.count(), 1)

    def test_owner_and_ready_boundaries(self):
        """跨用户所有新动作 404，匿名拒绝；本人 uploaded 原件不得编辑/导出/获取推荐资料。"""
        self.client.force_authenticate(self.other)
        for action in ("editor/", "export/", "recommendation-profile/"):
            self.assertEqual(self.client.get(self.url + action).status_code, 404)
        body = {"units": {"projects": "API"}}
        self.assertEqual(
            self.client.post(self.url + "editions/", body, format="json").status_code, 404
        )
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url + "editor/").status_code, 403)
        self.client.force_authenticate(self.owner)
        self.original.status = "uploaded"
        self.original.save(update_fields=["status"])
        for action in ("editor/", "export/", "recommendation-profile/"):
            self.assertEqual(self.client.get(self.url + action).status_code, 400)
        self.assertEqual(
            self.client.post(self.url + "editions/", body, format="json").status_code, 400
        )

    def test_heading_grouping_retains_content(self):
        """只识别整行标题；正文空白/HTML 字符保留，未知段落留在 other，不伪造槽位。"""
        units = split_units("姓名 <img>\n1. 教育经历：\n  计算机专业\nProjects\nAPI\n")
        self.assertEqual(units["other"], "姓名 <img>\n")
        self.assertEqual(units["education"], "  计算机专业\n")
        self.assertEqual(units["projects"], "API\n")
        self.assertEqual(units["skills"], "")

    def test_extracted_fields_are_not_confirmed_until_saved(self):
        """真实授权 editor 提供全字段建议及证据；GET 不确认，显式 editions 保存后推荐才读取值。"""
        self.original.text = "关键技能\nPython, Django\n教育经历\nGPA: 3.8 / 4.0\n"
        self.original.save(update_fields=["text"])
        editor = self.client.get(self.url + "editor/").data
        suggestions = editor["slot_suggestions"]
        self.assertEqual(set(suggestions["values"]), set(editor["slot_units"]))
        self.assertEqual(suggestions["values"]["skills"], ["Python", "Django"])
        self.assertEqual(suggestions["values"]["gpa"], 3.8)
        self.assertEqual(suggestions["evidence"]["gpa"][0]["unit"], "education")
        self.assertIsNone(editor["slots"]["skills"])
        self.original.refresh_from_db()
        self.assertEqual(self.original.recommendation_slots, {})
        self.assertIsNone(
            self.client.get(self.url + "recommendation-profile/").data["candidate"]["skills"]
        )
        saved = self.client.post(
            self.url + "editions/",
            {"units": editor["units"], "slots": {"skills": ["SQL"], "gpa": None}},
            format="json",
        )
        self.assertEqual(saved.status_code, 201, saved.data)
        url = f"/api/resume-versions/{saved.data['id']}/"
        reloaded = self.client.get(url + "editor/").data
        self.assertIsNone(reloaded["slot_suggestions"])
        self.assertEqual(reloaded["slots"]["skills"], ["SQL"])
        self.assertIsNone(reloaded["slots"]["gpa"])
        self.assertEqual(
            self.client.get(url + "recommendation-profile/").data["candidate"]["skills"], ["SQL"]
        )
