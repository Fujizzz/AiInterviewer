"""职责：验证本人资料维护的认证、字段边界、持久化与真实 CSRF，不修改生产用户。
实现：真实 Django 用户/session 和 APIClient，测试事务隔离两名用户，不模拟认证或数据库。
关联：api.profile、resumes.html；不发送验证邮件、不调用模型。

目录：
- ProfileTests：基本资料接口边界测试。
- ProfileTests.setUp：创建两名隔离用户，登录其中一名。
- ProfileTests.test_get_and_patch_only_self：公开字段受限，部分修改只影响本人。
- ProfileTests.test_invalid_fields_do_not_partially_save：非法格式/身份/权限字段不产生部分写入。
- ProfileTests.test_session_and_csrf_required：匿名或缺少真实 CSRF 的写请求拒绝。

关键变量：
（无模块级变量。）
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


class ProfileTests(TestCase):
    """功能：验证真实资料契约；逻辑：本人隔离与请求保护；约束：不证明邮箱归属验证。"""

    def setUp(self):
        """无外部输入；创建隔离用户和会话，保存对象用于比较另一用户未被修改。"""
        self.owner = get_user_model().objects.create_user(username="owner", password="a")
        self.other = get_user_model().objects.create_user(username="other", password="b")
        self.client = APIClient()
        self.client.force_login(self.owner)

    def test_get_and_patch_only_self(self):
        """真实 GET 无密码/权限；PATCH 更新本人并允许明确清空邮箱，不改变账号或另一用户。"""
        response = self.client.get("/api/profile/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data), {"id", "username", "first_name", "email", "date_joined"}
        )
        self.assertEqual(response["Cache-Control"], "no-store, private")
        response = self.client.patch(
            "/api/profile/", {"first_name": "姓名", "email": "owner@example.test"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.owner.first_name, "姓名")
        self.assertEqual(self.owner.email, "owner@example.test")
        self.assertEqual(self.other.first_name, "")
        self.assertEqual(self.owner.username, "owner")
        response = self.client.patch("/api/profile/", {"email": ""}, format="json")
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email, "")
        self.assertEqual(self.owner.first_name, "姓名")

    def test_invalid_fields_do_not_partially_save(self):
        """无效邮箱、过长姓名及非编辑字段整次拒绝，权限字段不能被写入或静默忽略。"""
        for data in (
            {"first_name": "Must not save", "email": "invalid"},
            {"first_name": "x" * 151},
            {"id": self.other.pk},
            {"username": "changed"},
            {"is_staff": True},
        ):
            response = self.client.patch("/api/profile/", data, format="json")
            self.assertEqual(response.status_code, 400)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "")
        self.assertFalse(self.owner.is_staff)

    def test_session_and_csrf_required(self):
        """匿名 GET/PATCH 拒绝；真实 session 缺 token 拒绝，个人中心 token 才能完成本人修改。"""
        client = APIClient(enforce_csrf_checks=True)
        self.assertEqual(client.get("/api/profile/").status_code, 403)
        self.assertEqual(client.patch("/api/profile/", {}, format="json").status_code, 403)
        client.force_login(self.owner)
        response = client.patch("/api/profile/", {"first_name": "N"}, format="json")
        self.assertEqual(response.status_code, 403)
        client.get("/resumes/")
        response = client.patch(
            "/api/profile/", {"first_name": "N"}, format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 200)
