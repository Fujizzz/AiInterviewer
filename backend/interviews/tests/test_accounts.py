"""职责：验证真实用户/session/CSRF 与对象权限，使用隔离数据库且不调用外部模型。

实现：两名用户交叉访问练习和面试；ASGI 握手及退出后消息校验使用真实 session 表。
关联：accounts、session_socket、两个历史 API 与 reserve_request；不模拟密码校验或归属查询。

目录：
- AccountTests：HTTP 注册、登录、安全跳转、CSRF 和对象归属测试。
- AccountTests.setUp：在独立测试事务中创建两名真实用户和两道共享题。
- AccountTests.test_public_entry_and_protected_routes：匿名主页/账号页可读，业务页面和 API 受保护。
- AccountTests.test_registration_short_password_and_escaping：
  一字符密码可注册，哈希保存且用户名转义。
- AccountTests.test_login_errors_and_safe_next：
  错误密码不登录，外站 next 不被采用，正确登录可进入面试。
- AccountTests.test_duplicate_and_empty_registration：重名/空输入明确失败，不创建额外用户。
- AccountTests.test_csrf_and_logout：真实 CSRF token 才可登录/注销；注销使业务 API 再次拒绝。
- AccountTests.test_practice_ownership：练习只能由本人查询和修改，共享题库不按用户拆分。
- AccountTests.test_agent_history_ownership：面试详情/请求/列表都隔离，旧未归属数据不可见。
- SocketAccountTests：真实数据库 session 下验证 WebSocket 门禁，不调用模型。
- SocketAccountTests.test_anonymous_and_logout_rejected：
  匿名拒绝，登录允许，注销阻止已开连接的新消息。
- SocketAccountTests.test_owner_reserved_before_model：
  请求预留保存服务器认证用户，跨用户归属被拒绝。

关键变量：
（无模块级变量。）
"""

import json
from uuid import uuid4

from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from config.asgi import application
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings

from interviews.agent_models import AgentInterview, AgentRequest
from interviews.agent_records import reserve_request
from interviews.agent_socket import Start
from interviews.models import PracticeSession, Question


@override_settings(INTERVIEW_REQUIRE_LOGIN=True)
class AccountTests(TestCase):
    """在默认测试客户端与显式 CSRF 客户端中验证账号行为，所有写入只发生在临时数据库。"""

    def setUp(self):
        """创建真实短密码用户与共享题库；不使用弱哈希替身，不连接供应商。"""
        self.alice = get_user_model().objects.create_user(username="alice", password="a")
        self.bob = get_user_model().objects.create_user(username="bob", password="b")
        Question.objects.all().delete()
        Question.objects.create(text="First shared question", position=1)
        Question.objects.create(text="Second shared question", position=2)

    def test_public_entry_and_protected_routes(self):
        """匿名可读主页/表单/公开样式；业务 HTML 跳转而 API 明确 401，不能靠静态别名绕过。"""
        for path in ("/", "/login/", "/register/", "/stream-demo/account.css"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertRedirects(self.client.get("/agent/"), "/login/?next=%2Fagent%2F")
        self.assertEqual(self.client.get("/api/health/").status_code, 401)
        self.assertEqual(self.client.get("/stream-demo/agent.html").status_code, 302)

    def test_registration_short_password_and_escaping(self):
        """一字符密码通过；用户名按文本渲染，密码只有标准哈希而非明文，注册后自动登录。"""
        response = self.client.post("/register/", {"username": "<new>", "password": "1"})
        self.assertRedirects(response, "/")
        user = get_user_model().objects.get(username="<new>")
        self.assertTrue(user.check_password("1"))
        self.assertNotEqual(user.password, "1")
        self.assertContains(self.client.get("/"), "&lt;new&gt;")
        self.assertNotContains(self.client.get("/"), ">\u003cnew\u003e<")
        self.assertEqual(self.client.get("/api/health/").status_code, 200)

    def test_login_errors_and_safe_next(self):
        """错误凭据返回 400；next 不可指向外站或账号循环，正确密码可跳转受保护面试页。"""
        self.assertEqual(
            self.client.post("/login/", {"username": "alice", "password": "x"}).status_code, 400
        )
        self.assertRedirects(
            self.client.post(
                "/login/",
                {
                    "username": "alice",
                    "password": "a",
                    "next": "https://foreign.example/",
                },
            ),
            "/",
        )
        self.client.logout()
        self.assertRedirects(
            self.client.post(
                "/login/",
                {
                    "username": "alice",
                    "password": "a",
                    "next": "/agent/",
                },
            ),
            "/agent/",
        )

    def test_duplicate_and_empty_registration(self):
        """用户名重复和空密码均失败；不发送验证码，不创建用户副本。"""
        before = get_user_model().objects.count()
        for values in (
            {"username": "alice", "password": "1"},
            {"username": "new", "password": ""},
            {"username": "", "password": "1"},
        ):
            self.assertEqual(self.client.post("/register/", values).status_code, 400)
        self.assertEqual(get_user_model().objects.count(), before)

    def test_csrf_and_logout(self):
        """真实 Cookie/CSRF 流程验证登录和注销，GET 退出不改变会话；未知 token 不接受。"""
        client = Client(enforce_csrf_checks=True)
        client.get("/login/")
        self.assertEqual(
            client.post("/login/", {"username": "alice", "password": "a"}).status_code, 403
        )
        token = client.cookies["csrftoken"].value
        response = client.post(
            "/login/", {"username": "alice", "password": "a"}, HTTP_X_CSRFTOKEN=token
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get("/logout/").status_code, 405)
        self.assertEqual(client.post("/logout/").status_code, 403)
        self.assertEqual(
            client.post("/logout/", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code,
            302,
        )
        self.assertEqual(client.get("/api/health/").status_code, 401)

    def test_practice_ownership(self):
        """本人可创建/查询场次；另一账号的详情、结束和单题写入均 404，状态版本不变。"""
        self.client.force_login(self.alice)
        response = self.client.post("/api/sessions/", {}, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(PracticeSession.objects.get(pk=data["id"]).owner, self.alice)
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get("/api/sessions/").json()["count"], 0)
        self.assertEqual(self.client.get(f"/api/sessions/{data['id']}/").status_code, 404)
        self.assertEqual(
            self.client.post(
                f"/api/sessions/{data['id']}/finish/",
                {"version": 1},
                content_type="application/json",
            ).status_code,
            404,
        )
        path = f"/api/sessions/{data['id']}/items/{data['items'][0]['id']}/"
        self.assertEqual(
            self.client.patch(
                path, {"action": "start", "version": 1}, content_type="application/json"
            ).status_code,
            404,
        )
        self.assertEqual(PracticeSession.objects.get(pk=data["id"]).version, 1)
        self.assertEqual(self.client.get("/api/questions/").json()["count"], 2)

    def test_agent_history_ownership(self):
        """历史及子请求只能由本人读取，未归属旧数据不会被首个注册用户继承。"""
        own = AgentInterview.objects.create(owner=self.alice)
        other = AgentInterview.objects.create(owner=self.bob)
        AgentInterview.objects.create()
        request = AgentRequest.objects.create(id=uuid4(), interview=other, kind="start")
        self.client.force_login(self.alice)
        base = "/api/agent-interviews/"
        self.assertEqual(self.client.get(base).json()["count"], 1)
        self.assertEqual(self.client.get(base + str(own.pk) + "/").status_code, 200)
        for suffix in ("", "requests/", f"requests/{request.pk}/"):
            self.assertEqual(self.client.get(base + str(other.pk) + "/" + suffix).status_code, 404)


@override_settings(INTERVIEW_REQUIRE_LOGIN=True)
class SocketAccountTests(TransactionTestCase):
    """用真实提交的 session 行验证异步认证边界；不通过真实网络或调用模型。"""

    async def test_anonymous_and_logout_rejected(self):
        """匿名握手拒绝；有效 session 可接收 hello，注销后已开连接的消息被拒绝。"""
        scope = {
            "type": "websocket",
            "path": "/ws/echo/",
            "scheme": "ws",
            "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")],
            "client": ("127.0.0.1", 12345),
        }
        anonymous = ApplicationCommunicator(application, scope)
        await anonymous.send_input({"type": "websocket.connect"})
        self.assertEqual((await anonymous.receive_output())["type"], "websocket.close")
        await anonymous.wait()
        user = await sync_to_async(get_user_model().objects.create_user)(
            username="socket", password="1"
        )
        await sync_to_async(self.client.force_login)(user)
        cookie = self.client.cookies["sessionid"].value
        scope["headers"] = [*scope["headers"], (b"cookie", ("sessionid=" + cookie).encode())]
        communicator = ApplicationCommunicator(application, scope)
        await communicator.send_input({"type": "websocket.connect"})
        self.assertEqual((await communicator.receive_output())["type"], "websocket.accept")
        self.assertEqual(json.loads((await communicator.receive_output())["text"])["type"], "hello")
        await sync_to_async(self.client.logout)()
        await communicator.send_input({"type": "websocket.receive", "text": "{}"})
        self.assertEqual((await communicator.receive_output())["type"], "websocket.close")
        await communicator.wait()

    async def test_owner_reserved_before_model(self):
        """请求预留使用服务器 owner_id；另一身份无法在相同面试下追加请求，不触发模型。"""
        user = await sync_to_async(get_user_model().objects.create_user)(
            username="owner", password="1"
        )
        interview_id = uuid4()
        command = Start(type="start", request_id=uuid4(), resume_text="Synthetic test")
        await reserve_request(interview_id, command, owner_id=user.pk)
        saved = await AgentInterview.objects.aget(pk=interview_id)
        self.assertEqual(saved.owner_id, user.pk)
        with self.assertRaises(PermissionError):
            await reserve_request(interview_id, command, owner_id=None)
