"""REST 与数据库回归测试。以隔离数据库覆盖快照、状态、并发版本和错误边界。

目录：
- ApiTests：
  隔离数据库下的 API 回归集合；每例独立准备两道题以保持可复现。
- ApiTests.setUp：
  创建 APIClient 与确定性题库；清除测试库种子，不操作开发库。
- ApiTests.create：
  通过公开创建接口构造测试场次，并要求 HTTP 201，返回响应数据。
- ApiTests.update：
  根据场次快照构造单题 PATCH 地址，供各状态测试复用。
- ApiTests.test_health_and_question_crud：
  验证健康检查、分页数量、空白拒绝和题目新增/停用，覆盖基本写契约。
- ApiTests.test_snapshot_and_defaults_survive_question_edit：
  编辑或删除源题后查询场次，验证历史快照及固定 10/90 秒时长未改变。
- ApiTests.test_question_selection_order_validation：
  验证显式 ID 顺序被保留，并拒绝空、重复、缺失和停用题目。
- ApiTests.test_empty_bank_and_configuration_override_rejected：
  验证只读时长不可覆盖，空题库创建失败且未留下场次。
- ApiTests.test_full_lifecycle_and_stale_version：
  走通开始、提交、结束，检查旧版本被拒绝以及剩余题目被跳过。
- ApiTests.test_question_limit_and_explicit_subset：
  验证默认 100 题可创建、101 题整体拒绝，而大题库中的显式子集仍可使用。
- ApiTests.test_invalid_transition_rolls_back_version：
  验证非法转换和并行 answering 被拒绝，失败请求不消耗版本。
- ApiTests.test_payload_validation_and_cross_session_item：
  覆盖缺失版本、负时长、动作字段冲突及跨场次单题，检查事务回滚。
- ApiTests.test_local_origin_restriction：
  构造非本机和跨源请求，验证访问策略在业务处理前返回 403。
- ApiTests.test_database_rejects_duplicate_position：
  直接构造重复场次顺序，验证数据库唯一约束独立于 API 仍然有效。
- ApiTests.test_demo_assets_and_missing_resource：
  检查资源白名单与缺失场次响应，防止测试页暴露任意文件。

关键变量：
（无模块级变量。）
"""

import uuid

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from interviews.models import PracticeSession, Question, SessionQuestion


class ApiTests(TestCase):
    """隔离数据库下的 API 回归集合；每例独立准备两道题以保持可复现。"""

    def setUp(self):
        """创建 APIClient 与确定性题库；清除测试库种子，不操作开发库。"""
        self.client = APIClient()
        Question.objects.all().delete()
        self.q1 = Question.objects.create(text="First question", position=1)
        self.q2 = Question.objects.create(text="Second question", position=2)

    def create(self):
        """通过公开创建接口构造测试场次，并要求 HTTP 201，返回响应数据。"""
        response = self.client.post("/api/sessions/", {}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def update(self, session, index=0, **data):
        """根据场次快照构造单题 PATCH 地址，供各状态测试复用。"""
        return self.client.patch(
            f"/api/sessions/{session['id']}/items/{session['items'][index]['id']}/",
            data,
            format="json",
        )

    def test_health_and_question_crud(self):
        """验证健康检查、分页数量、空白拒绝和题目新增/停用，覆盖基本写契约。"""
        self.assertEqual(
            self.client.get("/api/health/").data, {"status": "ok", "database": "sqlite"}
        )
        self.assertEqual(self.client.get("/api/questions/").data["count"], 2)
        invalid = self.client.post("/api/questions/", {"text": "   "}, format="json")
        self.assertEqual(invalid.status_code, 400)
        created = self.client.post("/api/questions/", {"text": "Third"}, format="json")
        self.assertEqual(created.status_code, 201)
        response = self.client.patch(
            f"/api/questions/{created.data['id']}/", {"enabled": False}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_snapshot_and_defaults_survive_question_edit(self):
        """编辑或删除源题后查询场次，验证历史快照及固定 10/90 秒时长未改变。"""
        session = self.create()
        self.assertEqual((session["prep_seconds"], session["answer_seconds"]), (10, 90))
        self.q1.text = "Edited"
        self.q1.save()
        self.q2.delete()
        response = self.client.get(f"/api/sessions/{session['id']}/")
        self.assertEqual(response.data["items"][0]["question_text"], "First question")
        self.assertEqual(response.data["items"][1]["question_text"], "Second question")
        self.assertIsNone(response.data["items"][1]["question_id"])

    def test_question_selection_order_validation(self):
        """验证显式 ID 顺序被保留，并拒绝空、重复、缺失和停用题目。"""
        response = self.client.post(
            "/api/sessions/", {"question_ids": [str(self.q2.id), str(self.q1.id)]}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["items"][0]["question_text"], "Second question")
        for ids in [[], [str(self.q1.id)] * 2, [str(uuid.uuid4())]]:
            self.assertEqual(
                self.client.post(
                    "/api/sessions/", {"question_ids": ids}, format="json"
                ).status_code,
                400,
            )
        self.q1.enabled = False
        self.q1.save()
        self.assertEqual(
            self.client.post(
                "/api/sessions/", {"question_ids": [str(self.q1.id)]}, format="json"
            ).status_code,
            400,
        )

    def test_empty_bank_and_configuration_override_rejected(self):
        """验证只读时长不可覆盖，空题库创建失败且未留下场次。"""
        self.assertEqual(
            self.client.post("/api/sessions/", {"prep_seconds": 1}, format="json").status_code, 400
        )
        Question.objects.all().delete()
        self.assertEqual(self.client.post("/api/sessions/", {}, format="json").status_code, 400)
        self.assertFalse(PracticeSession.objects.exists())

    def test_full_lifecycle_and_stale_version(self):
        """走通开始、提交、结束，检查旧版本被拒绝以及剩余题目被跳过。"""
        session = self.create()
        started = self.update(session, action="start", version=1)
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.data["version"], 2)
        self.assertEqual(self.update(session, action="complete", version=1).status_code, 409)
        completed = self.update(
            session, action="complete", version=2, answer_text="My answer", duration_ms=90000
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.data["items"][0]["duration_ms"], 90000)
        response = self.client.post(
            f"/api/sessions/{session['id']}/finish/", {"version": 3}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["items"][1]["status"], "skipped")
        self.assertEqual(self.update(session, index=1, action="start", version=4).status_code, 409)

    def test_question_limit_and_explicit_subset(self):
        """验证默认 100 题可创建、101 题整体拒绝，而大题库中的显式子集仍可使用。"""
        Question.objects.bulk_create(
            [Question(text=f"Question {index}", position=index) for index in range(3, 101)]
        )
        self.assertEqual(len(self.create()["items"]), 100)
        Question.objects.create(text="Overflow question", position=101)
        self.assertEqual(self.client.post("/api/sessions/", {}, format="json").status_code, 400)
        self.assertEqual(PracticeSession.objects.count(), 1)
        response = self.client.post(
            "/api/sessions/", {"question_ids": [str(self.q2.id)]}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data["items"]), 1)

    def test_invalid_transition_rolls_back_version(self):
        """验证非法转换和并行 answering 被拒绝，失败请求不消耗版本。"""
        session = self.create()
        self.assertEqual(self.update(session, action="complete", version=1).status_code, 409)
        self.assertEqual(PracticeSession.objects.get(pk=session["id"]).version, 1)
        self.assertEqual(self.update(session, action="start", version=1).status_code, 200)
        self.assertEqual(self.update(session, index=1, action="start", version=2).status_code, 409)
        self.assertEqual(PracticeSession.objects.get(pk=session["id"]).version, 2)

    def test_payload_validation_and_cross_session_item(self):
        """覆盖缺失版本、负时长、动作字段冲突及跨场次单题，检查事务回滚。"""
        session, other = self.create(), self.create()
        for data in [
            {"action": "start"},
            {"action": "complete", "version": 1, "duration_ms": -1},
            {"action": "start", "version": 1, "answer_text": "unexpected"},
        ]:
            self.assertEqual(self.update(session, **data).status_code, 400)
        response = self.client.patch(
            f"/api/sessions/{session['id']}/items/{other['items'][0]['id']}/",
            {"action": "start", "version": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(PracticeSession.objects.get(pk=session["id"]).version, 1)

    def test_local_origin_restriction(self):
        """构造非本机和跨源请求，验证访问策略在业务处理前返回 403。"""
        self.assertEqual(self.client.get("/api/health/", REMOTE_ADDR="192.0.2.1").status_code, 403)
        self.assertEqual(
            self.client.get("/api/health/", HTTP_ORIGIN="https://example.com").status_code, 403
        )

    def test_database_rejects_duplicate_position(self):
        """直接构造重复场次顺序，验证数据库唯一约束独立于 API 仍然有效。"""
        session = self.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SessionQuestion.objects.create(
                session_id=session["id"], question_text="Duplicate", position=1
            )

    def test_demo_assets_and_missing_resource(self):
        """检查资源白名单与缺失场次响应，防止测试页暴露任意文件。"""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        response.close()
        for name in ["app.js", "view.js", "media.js", "stream-client.js"]:
            response = self.client.get(f"/stream-demo/{name}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Cache-Control"], "no-store")
            response.close()
        self.assertEqual(self.client.get("/stream-demo/settings.py").status_code, 404)
        self.assertEqual(self.client.get(f"/api/sessions/{uuid.uuid4()}/").status_code, 404)
        self.assertEqual(self.client.get("/api/sessions/not-a-uuid/").status_code, 404)
