"""职责：验证个人中心岗位推荐的真实排序、保存边界、岗位来源故障和私有访问控制。
实现：隔离数据库与临时合成岗位 JSON；粗排执行实际冻结模型；仅在外部 API 边界注入明确的精排替身。
关联：resume_versions.recommendations、recommendation.catalog/runtime/rerank；不访问正式岗位或外部模型。
目录：
- PersonalRecommendationTests：本人快照推荐集成测试。
- PersonalRecommendationTests.setUp：创建两用户、保存槽位和合成目录。
- PersonalRecommendationTests.tearDown：关闭配置覆盖并清理临时目录。
- PersonalRecommendationTests.write_catalog：写入显式测试目录，不触碰生产配置。
- PersonalRecommendationTests.model_output：外部 API 替身按倒序返回有界岗位和合成双语理由。
- PersonalRecommendationTests.test_real_model_uses_saved_snapshot：
  粗排分数不变，最终次序由精排决定。
- PersonalRecommendationTests.test_llm_failure_is_explicit：精排失败返回固定状态，不输出粗排替代项。
- PersonalRecommendationTests.test_permissions_and_csrf：跨用户、匿名和缺失 CSRF 请求拒绝。
- PersonalRecommendationTests.test_saved_ready_and_empty_boundaries：未解析、未知及未保存输入拒绝。
- PersonalRecommendationTests.test_catalog_failures_do_not_fabricate_results：
  缺失、非法、重复、超量目录失败。
- PersonalRecommendationTests.test_model_failure_is_explicit：模型不可用返回故障而非替代结果。
- PersonalRecommendationTests.test_invalid_saved_values_are_diagnostic：
  保存数据损坏与数值越界明确失败。
- PersonalRecommendationTests.test_bundled_experience_catalog：实验岗位全部通过契约并调用原模型。
关键变量：
（无模块级变量。）
约束：
真实本地推理通过不代表模型效果或生产岗位可用；所有简历和岗位均为合成数据。
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from interviews.recommendation.rerank import RerankOutput, RerankUnavailable
from interviews.recommendation.runtime import ModelUnavailable, rank_pairs
from interviews.recommendation.schemas import CandidateInput, JobInput
from interviews.resume_models import ResumeVersion


class PersonalRecommendationTests(APITestCase):
    """功能：集成测试个人推荐；逻辑：真实权限、ORM、目录和排序；约束：不读写真实用户记录。"""

    def setUp(self):
        """无外部输入；创建隔离用户与 ready 保存快照，临时目录显式提供两岗，不修改模型。"""
        self.owner = get_user_model().objects.create_user(
            username="job-owner", password="test-pass"
        )
        self.other = get_user_model().objects.create_user(username="job-other")
        self.version = ResumeVersion.objects.create(
            owner=self.owner,
            status="ready",
            text="Synthetic resume",
            recommendation_slots={"skills": ["Python"], "months_experience": 6},
        )
        self.url = f"/api/resume-versions/{self.version.pk}/recommendations/"
        self.directory = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.directory.name) / "jobs.json"
        self.catalog = {
            "source_name": "Test jobs",
            "source_kind": "experience",
            "jobs": [
                {
                    "title": "Python internship",
                    "company": "Test company",
                    "requirements": {
                        "job_id": "python",
                        "required_skills": ["Python"],
                        "min_months_experience": 3,
                    },
                },
                {
                    "title": "Java internship",
                    "requirements": {"job_id": "java", "required_skills": ["Java"]},
                },
            ],
        }
        self.write_catalog(self.catalog)
        self.override = override_settings(RECOMMENDATION_JOB_CATALOG=str(self.catalog_path))
        self.override.enable()
        self.client.force_authenticate(self.owner)
        self.llm_patch = patch(
            "interviews.recommendation.rerank.request_rerank", side_effect=self.model_output
        )
        self.llm_call = self.llm_patch.start()
        self.addCleanup(self.llm_patch.stop)

    def tearDown(self):
        """读取实例配置与临时目录，恢复 Django 设置并删除测试文件；不涉及正式目录。"""
        self.override.disable()
        self.directory.cleanup()

    def write_catalog(self, data):
        """输入可 JSON 编码数据，写入当前临时目录；无返回，非法契约用于边界测试。"""
        self.catalog_path.write_text(json.dumps(data), encoding="utf-8")

    def model_output(self, payload):
        """输入候选 JSON，输出倒序前 final_count 个及固定理由；仅替代外部 API，不替代粗排。"""
        jobs = [
            {
                "job_id": item["job_id"],
                "reason_zh": "已保存 Python 技能；其他条件仍需核对。",
                "reason_en": "Python is recorded; other requirements still need review.",
            }
            for item in reversed(payload["shortlist"])
        ][: payload["final_count"]]
        return RerankOutput.model_validate({"jobs": jobs}), "test-api-model"

    def test_llm_failure_is_explicit(self):
        """外部调用替身显式抛错；API/配置为 503、坏输出为 502，不返回粗排或自动再次调用。"""
        for code, status in [
            ("recommendation_llm_not_configured", 503),
            ("recommendation_llm_unavailable", 503),
            ("recommendation_llm_invalid_output", 502),
        ]:
            self.llm_call.reset_mock()
            self.llm_call.side_effect = RerankUnavailable(code)
            response = self.client.post(self.url)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.data, {"code": code})
            self.llm_call.assert_called_once()

    def test_real_model_uses_saved_snapshot(self):
        """粗排运行实际 LightGBM，模拟 API 反转名次；验证原分数不变和精排理由，不验证供应商。"""
        candidate = CandidateInput(
            candidate_id=str(self.version.pk), **self.version.recommendation_slots
        )
        expected = rank_pairs(
            [(candidate, JobInput(**job["requirements"])) for job in self.catalog["jobs"]], "jobs"
        )
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.assertEqual(response.data["resume_version_id"], str(self.version.pk))
        self.assertTrue(response.data["experimental"])
        self.assertEqual(response.data["source_kind"], "experience")
        for actual, raw in zip(
            response.data["results"], reversed(expected["results"]), strict=True
        ):
            self.assertEqual(
                {key: actual[key] for key in raw if key != "rank"},
                {key: raw[key] for key in raw if key != "rank"},
            )
            self.assertEqual(actual["coarse_rank"], raw["rank"])
            self.assertTrue(actual["recommendation_reason"]["zh"])
            self.assertEqual(
                actual["matched_skills"], ["Python"] if actual["job_id"] == "python" else []
            )
        self.assertEqual(response.data["sorted_by"], "llm_order")
        self.assertEqual([item["rank"] for item in response.data["results"]], [1, 2])
        payload = self.llm_call.call_args.args[0]
        self.assertNotIn("candidate_id", payload["candidate"])
        self.assertEqual(payload["candidate"]["months_experience"], 6)
        self.assertNotIn("text", payload)
        self.version.refresh_from_db()
        self.assertEqual(
            self.version.recommendation_slots, {"skills": ["Python"], "months_experience": 6}
        )

    def test_permissions_and_csrf(self):
        """跨用户和匿名拒绝；真实 SessionAuthentication 缺 CSRF 拒绝，有正确 token 才实际排序。"""
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        session = APIClient(enforce_csrf_checks=True)
        session.force_login(self.owner)
        self.assertEqual(session.post(self.url).status_code, 403)
        session.get("/resumes/")
        token = session.cookies["csrftoken"].value
        self.assertEqual(session.post(self.url, HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_saved_ready_and_empty_boundaries(self):
        """不接受前端候选人覆盖、未 ready 或全未知快照；原件文本中的技能不自动成为确认字段。"""
        self.assertEqual(
            self.client.post(self.url, {"skills": ["Java"]}, format="json").status_code, 400
        )
        self.version.status = "uploaded"
        self.version.save()
        self.assertEqual(self.client.post(self.url).status_code, 409)
        self.version.status = "ready"
        self.version.text = "Skills: Python"
        self.version.recommendation_slots = {}
        self.version.save()
        self.assertEqual(self.client.post(self.url).data["code"], "recommendation_profile_empty")

    def test_catalog_failures_do_not_fabricate_results(self):
        """显式空配置、坏 JSON、额外字段、重复 ID 和超过 100 岗均 503，无截断或替代来源。"""
        with override_settings(RECOMMENDATION_JOB_CATALOG=""):
            self.assertEqual(self.client.post(self.url).data["code"], "job_catalog_not_configured")
        self.catalog_path.write_text("broken JSON", encoding="utf-8")
        self.assertEqual(self.client.post(self.url).status_code, 503)
        for data in [
            {**self.catalog, "extra": True},
            {**self.catalog, "jobs": [self.catalog["jobs"][0]] * 2},
            {**self.catalog, "jobs": [self.catalog["jobs"][0]] * 101},
        ]:
            self.write_catalog(data)
            response = self.client.post(self.url)
            self.assertEqual(response.data, {"code": "job_catalog_invalid"})
            self.assertEqual(response.status_code, 503)

    def test_model_failure_is_explicit(self):
        """只模拟模型不可用异常验证 503，不将该 mocked 测试当真实服务验证，不产生替代推荐。"""
        with patch(
            "interviews.api.resume_versions.rank_pairs", side_effect=ModelUnavailable("test")
        ):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data, {"code": "recommendation_model_unavailable"})

    def test_invalid_saved_values_are_diagnostic(self):
        """真实保存损坏类型返回 503；有限数值超出原 float32 特征范围返回 422，不替代评分。"""
        for slots, expected in [
            ({"skills": "bad"}, "recommendation_profile_invalid"),
            ({"months_experience": 1e308}, "recommendation_features_invalid"),
        ]:
            self.version.recommendation_slots = slots
            self.version.save()
            self.assertEqual(self.client.post(self.url).data["code"], expected)

    def test_bundled_experience_catalog(self):
        """显式选定同源的 100 岗并实际调用粗排，验证 20 岗送入 API 替身及 5 岗展示。"""
        path = Path(__file__).resolve().parents[1] / "recommendation/data/experience-jobs.json"
        with override_settings(RECOMMENDATION_JOB_CATALOG=str(path)):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["results"]), 5)
        self.assertEqual(response.data["pipeline"]["catalog_count"], 100)
        self.assertEqual(len(self.llm_call.call_args.args[0]["shortlist"]), 20)
        self.assertEqual(response.data["source_kind"], "experience")
        self.assertTrue(
            {item["job_id"] for item in response.data["results"]}.issubset(
                {f"J{number:04}" for number in range(1, 101)}
            )
        )
        self.assertTrue(
            all("体验岗位" not in item["job"]["title"] for item in response.data["results"])
        )
