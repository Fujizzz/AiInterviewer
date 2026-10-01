"""缺失资料推荐的真实本地推理与API回归测试。无数据库、LLM或外网依赖。

目录：
- RecommendationTests：固定模型及HTTP契约测试集合。
- RecommendationTests.setUp：创建本机API客户端及已知完整资料样例。
- RecommendationTests.test_feature_formula_and_unknowns：核对独立预期11维、零/false及未知区别。
- RecommendationTests.test_actual_models_match_research_predictions：
  重放五个研究场景的20个冻结预测。
- RecommendationTests.test_jobs_endpoint_sorts_and_reports_missing：
  真实调用岗位接口并核对排序与缺失。
- RecommendationTests.test_candidates_endpoint_sorts：真实调用候选人接口并核对岗位方向。
- RecommendationTests.test_no_evidence_is_unranked：完全无依据配对不产生分数或名次。
- RecommendationTests.test_ties_preserve_request_order：同分保持输入顺序。
- RecommendationTests.test_invalid_inputs_are_rejected：拒绝非法类型、额外字段和空技能要求。
- RecommendationTests.test_duplicate_and_oversized_pools：拒绝重复ID、空池和超过资源上限。
- RecommendationTests.test_errors_do_not_echo_profile：校验错误不回显原始简历字段值。
- RecommendationTests.test_corrupt_bundle_fails_closed：损坏副本返回503，无备用评分。
- RecommendationTests.test_local_access_and_method：保留本机访问策略并限制POST方法。

关键变量：
（无模块级变量。）

关键状态说明：
client为隔离HTTP客户端，candidate/job为虚构边界样例；权重来自批准的v4-B。
golden文件是已核验研究预测，不包含人物身份或简历正文；损坏测试只操作临时副本。
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from interviews.recommendation import runtime
from interviews.recommendation.features import build_features
from interviews.recommendation.schemas import CandidateInput, JobInput


class RecommendationTests(SimpleTestCase):
    """功能：保护输入语义及真实推理；逻辑：HTTP与固定预测双重验证；约束：不访问真实外部服务。"""

    def setUp(self):
        """功能：建立已知样例与客户端；输入无，输出实例状态；数据不写数据库或用户文件。"""
        self.client = APIClient(HTTP_HOST="localhost")
        self.candidate = {
            "candidate_id": "example-person",
            "skills": ["python"],
            "interests": ["tech"],
            "majors": ["CS"],
            "in_person_commitment": "In Person",
            "gpa": 3.5,
            "months_experience": 0,
            "academic_level": "UG4",
            "hours_per_week": 20,
            "length_of_commitment": 6,
            "num_publications": 0,
            "commit_to_summer": False,
        }
        self.job = {
            "job_id": "example-job",
            "required_skills": ["python", "sql"],
            "industry": "tech",
            "acceptable_majors": ["CS"],
            "job_in_person_commitment": "In Person",
            "min_gpa": 3,
            "min_months_experience": 6,
            "min_academic_level": "UG3",
            "min_hours_per_week": 10,
            "min_length_of_commitment": 3,
            "min_num_publications": 0,
        }

    def test_feature_formula_and_unknowns(self):
        """功能：以手算值核验特征；输入完整/部分未知/明确空技能；输出逐项一致和NaN断言。"""
        candidate, job = CandidateInput(**self.candidate), JobInput(**self.job)
        expected = [0.5, 1, 1, 1, 0.5, -6, 1, 10, 3, 0, 0]
        np.testing.assert_array_equal(build_features(candidate, job), expected)
        missing = CandidateInput(**{**self.candidate, "gpa": None, "commit_to_summer": None})
        self.assertTrue(np.isnan(build_features(missing, job)[[4, 10]]).all())
        empty = CandidateInput(candidate_id="empty", skills=[])
        self.assertEqual(build_features(empty, job)[0], 0)
        unknown = CandidateInput(candidate_id="unknown")
        self.assertTrue(np.isnan(build_features(unknown, job)).all())
        flexible = CandidateInput(candidate_id="flexible", in_person_commitment="No Preference")
        self.assertEqual(build_features(flexible, job)[3], 0)
        online = JobInput(job_id="online", job_in_person_commitment="Online")
        self.assertEqual(build_features(candidate, online)[3], 0)

    def test_actual_models_match_research_predictions(self):
        """功能：验证真实权重未漂移；输入20组冻结研究向量，输出分数一致断言；非mock推理。"""
        fixtures = json.loads(Path(__file__).with_name("recommendation_golden.json").read_text())
        _, models = runtime.load_bundle()
        matrix = np.asarray([case["features"] for case in fixtures["cases"]], dtype=np.float32)
        actual = np.column_stack([model.predict(matrix, num_threads=2) for model in models])
        expected = np.asarray([case["scores"] for case in fixtures["cases"]])
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)

    def test_jobs_endpoint_sorts_and_reports_missing(self):
        """功能：真实POST岗位列表；输入只有技能的候选人及两个岗位；输出有限分数、缺失列及排序断言。"""
        response = self.client.post(
            "/api/recommendations/jobs/",
            {
                "candidate": {"candidate_id": "c", "skills": ["python"]},
                "jobs": [self.job, {**self.job, "job_id": "other", "required_skills": ["sql"]}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertTrue(data["experimental"])
        self.assertFalse(data["release_gate_passed"])
        self.assertEqual(data["model_id"], "jobrec-v4-B")
        scores = [row["pref_score"] for row in data["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for row in data["results"]:
            self.assertEqual(row["available_feature_count"], 1)
            self.assertIn("gpa_margin", row["missing_features"])
            self.assertTrue(np.isfinite(row["qual_score"]))
        self.assertNotIn("NaN", response.content.decode())

    def test_candidates_endpoint_sorts(self):
        """功能：真实POST候选人列表；输入一岗两人；输出按岗位分数排序，方向及ID准确。"""
        response = self.client.post(
            "/api/recommendations/candidates/",
            {
                "job": self.job,
                "candidates": [
                    self.candidate,
                    {**self.candidate, "candidate_id": "other", "gpa": None},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["sorted_by"], "qual_score")
        scores = [row["qual_score"] for row in response.data["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(
            all(row["job_id"] == self.job["job_id"] for row in response.data["results"])
        )

    def test_no_evidence_is_unranked(self):
        """功能：全未知不伪造排名；输入无资料者与已知者同池；输出未知者末尾且分数/名次为null。"""
        response = self.client.post(
            "/api/recommendations/candidates/",
            {
                "job": self.job,
                "candidates": [{"candidate_id": "unknown"}, self.candidate],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        result = response.data["results"][-1]
        self.assertEqual(result["candidate_id"], "unknown")
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["rank"])
        self.assertIsNone(result["pref_score"])
        self.assertIsNone(result["qual_score"])

    def test_ties_preserve_request_order(self):
        """功能：并列稳定性；输入同资料不同ID的两个岗位；输出请求次序不被ID排序替换。"""
        response = self.client.post(
            "/api/recommendations/jobs/",
            {
                "candidate": self.candidate,
                "jobs": [{**self.job, "job_id": "z"}, {**self.job, "job_id": "a"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["job_id"] for row in response.data["results"]], ["z", "a"])

    def test_invalid_inputs_are_rejected(self):
        """功能：校验类型与范围；输入字符串数字、布尔数字、未知代码、额外字段及溢出；输出400。"""
        for change in (
            {"gpa": "3.5"},
            {"gpa": True},
            {"gpa": -1},
            {"gpa": 1e308},
            {"academic_level": "senior"},
            {"in_person_commitment": True},
            {"num_publications": 10**400},
            {"unexpected": 1},
            {"skills": [1]},
        ):
            with self.subTest(change=change):
                response = self.client.post(
                    "/api/recommendations/jobs/",
                    {
                        "candidate": {**self.candidate, **change},
                        "jobs": [self.job],
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(
            "/api/recommendations/jobs/",
            {
                "candidate": self.candidate,
                "jobs": [{**self.job, "required_skills": []}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/recommendations/jobs/",
            '{"candidate":{"gpa":NaN}}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_and_oversized_pools(self):
        """功能：请求池边界；输入重复ID/空池/101个对象；输出400，不静默截断或去重。"""
        for jobs in (
            [],
            [self.job, self.job],
            [{**self.job, "job_id": str(i)} for i in range(101)],
        ):
            response = self.client.post(
                "/api/recommendations/jobs/",
                {
                    "candidate": self.candidate,
                    "jobs": jobs,
                },
                format="json",
            )
            self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/recommendations/candidates/",
            {
                "job": self.job,
                "candidates": [self.candidate, self.candidate],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_errors_do_not_echo_profile(self):
        """功能：错误脱敏；输入非法数值中的哨兵文本；输出字段定位但不含输入正文。"""
        response = self.client.post(
            "/api/recommendations/jobs/",
            {
                "candidate": {**self.candidate, "gpa": "PRIVATE_PROFILE_SENTINEL"},
                "jobs": [self.job],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("PRIVATE_PROFILE_SENTINEL", response.content.decode())

    def test_corrupt_bundle_fails_closed(self):
        """功能：损坏模型明确失败；输入篡改的临时权重副本；输出503并恢复缓存，不修改真实模型。"""
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "model"
            shutil.copytree(runtime.MODEL_DIR, target)
            (target / "pref.txt").write_text("invalid-model", encoding="utf-8")
            runtime.load_bundle.cache_clear()
            try:
                with patch.object(runtime, "MODEL_DIR", target):
                    response = self.client.post(
                        "/api/recommendations/jobs/",
                        {
                            "candidate": self.candidate,
                            "jobs": [self.job],
                        },
                        format="json",
                    )
                    self.assertEqual(response.status_code, 503, response.data)
                    self.assertEqual(response.data["error"]["code"], "recommendation_unavailable")
            finally:
                runtime.load_bundle.cache_clear()

    def test_local_access_and_method(self):
        """功能：继承服务访问边界；输入GET及非本机POST；输出405/403，不放宽现有中间件。"""
        self.assertEqual(self.client.get("/api/recommendations/jobs/").status_code, 405)
        response = self.client.post(
            "/api/recommendations/jobs/", {}, format="json", REMOTE_ADDR="192.0.2.1"
        )
        self.assertEqual(response.status_code, 403)
