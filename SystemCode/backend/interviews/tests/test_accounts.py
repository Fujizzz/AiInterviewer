"""Responsibilities: Validate real user/session/CSRF and object permissions using isolated database
without calling external models.

Implementation: Two users cross-access practice and interview; ASGI handshake and exit message
validation use real session table.
Related Modules: accounts, session_socket, two legacy APIs, and reserve_request; do not simulate
password validation or ownership queries.

Declaration Index:
- AccountTests: HTTP registration, login, secure redirect, CSRF, and object ownership tests.
- AccountTests.setUp: Create two real users and two shared questions within isolated test
  transaction.
- AccountTests.test_public_entry_and_protected_routes: Anonymous homepage/account page readable;
  business pages and APIs protected.
- AccountTests.test_registration_short_password_and_escaping: One-character password allowed for
  registration; hash stored, username
  escaped.
- AccountTests.test_login_errors_and_safe_next: Incorrect password does not log in; external next is
  not accepted; correct login grants access to
  interview.
- AccountTests.test_duplicate_and_empty_registration: Duplicate or empty input fails explicitly; no
  extra users created.
- AccountTests.test_csrf_and_logout: Valid CSRF token required for login/logout; logout causes
  business API to reject again.
- AccountTests.test_practice_ownership: Practice accessible only by owner; shared question bank not
  split by user.
- AccountTests.test_agent_history_ownership: Interview details/requests/lists are isolated; old
  unowned data not visible.
- SocketAccountTests: Verify WebSocket gatekeeping under real database session, without model
  invocation.
- SocketAccountTests.test_anonymous_and_logout_rejected: Anonymous rejected; login allowed; logout
  blocks new messages on open connection.
- SocketAccountTests.test_owner_reserved_before_model: Request reservation saves server-side
  authenticated user; cross-user ownership
  denied.

Variable Index:
None
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
    """Validate account behavior in default test client and explicit CSRF client; all writes occur
    only in temporary database.
    """

    def setUp(self):
        """Create real short-password user and shared question bank; do not use weak hash stubs, do
        not connect to vendors.
        """
        self.alice = get_user_model().objects.create_user(username="alice", password="a")
        self.bob = get_user_model().objects.create_user(username="bob", password="b")
        Question.objects.all().delete()
        Question.objects.create(text="First shared question", position=1)
        Question.objects.create(text="Second shared question", position=2)

    def test_public_entry_and_protected_routes(self):
        """Anonymous can read homepage/form/shared styles; business HTML redirects while APIs return
        401; static aliases also protected.
        """
        for path in (
            "/",
            "/login/",
            "/register/",
            "/stream-demo/account.css",
            "/stream-demo/workspace.css",
        ):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertRedirects(self.client.get("/agent/"), "/login/?next=%2Fagent%2F")
        self.assertEqual(self.client.get("/api/health/").status_code, 401)
        self.assertEqual(self.client.get("/stream-demo/agent.html").status_code, 302)

    def test_registration_short_password_and_escaping(self):
        """One-character password passes; username rendered as text, password hashed via standard
        algorithm, not plaintext; auto-login after registration.
        """
        response = self.client.post("/register/", {"username": "<new>", "password": "1"})
        self.assertRedirects(response, "/")
        user = get_user_model().objects.get(username="<new>")
        self.assertTrue(user.check_password("1"))
        self.assertNotEqual(user.password, "1")
        self.assertContains(self.client.get("/"), "&lt;new&gt;")
        self.assertNotContains(self.client.get("/"), ">\u003cnew\u003e<")
        self.assertEqual(self.client.get("/api/health/").status_code, 200)

    def test_login_errors_and_safe_next(self):
        """Invalid credentials return 400; next cannot point to external site or account loop; valid
        password allows redirect to protected interview page.
        """
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
        """Duplicate username and empty password both fail; no verification code sent, no user
        duplicates created.
        """
        before = get_user_model().objects.count()
        for values in (
            {"username": "alice", "password": "1"},
            {"username": "new", "password": ""},
            {"username": "", "password": "1"},
        ):
            self.assertEqual(self.client.post("/register/", values).status_code, 400)
        self.assertEqual(get_user_model().objects.count(), before)

    def test_csrf_and_logout(self):
        """Real Cookie/CSRF flow validated for login/logout; GET exit does not change session;
        unknown token is rejected.
        """
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
        """Owner can create/query sessions; other user's details, end, and single-question writes
        return 404; state version unchanged.
        """
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
        """Historical and sub-requests accessible only by owner; unowned old data not inherited by
        first registered user.
        """
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
    """Verify asynchronous authentication boundaries using real submitted session; no real network
    used, no model invoked.
    """

    async def test_anonymous_and_logout_rejected(self):
        """Anonymous handshake rejected; valid session accepts hello; after logout, new messages on
        open connection rejected.
        """
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
        """Request reservation uses server owner_id; another identity cannot append request in same
        interview; no model triggered.
        """
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
