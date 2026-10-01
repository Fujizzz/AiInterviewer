"""职责：验证版本 API 的用户隔离、解析生命周期和面试绑定，不访问真实模型服务。
实现：真实隔离测试数据库与 HTTP 请求，解析流使用显式替身，保留生产管线调用边界。
关联：resume_versions、agent_records、agent_history 与迁移后的关系约束。
目录：
- parsing_fixture：提供一个确定性成功解析事件。
- failed_fixture：提供显式解析失败事件。
- pending_fixture：提供尚未结束的解析进度。
- ResumeVersionTests：版本与权限集成验证。
- ResumeVersionTests.setUp：建立两个账号和已登录客户端。
- ResumeVersionTests.test_versions_current_and_permissions：验证不可变版本、当前选择与跨用户拒绝。
- ResumeVersionTests.test_pdf_parse_and_private_download：
  验证上传/解析分离、持久化文本与原文件下载。
- ResumeVersionTests.test_interview_binding_and_deletion：验证绑定快照、历史归属及引用删除限制。
- ResumeVersionTests.test_invalid_source_and_unready_version：验证输入互斥和未就绪版本不触发模型。
- ResumeVersionTests.test_failed_and_interrupted_parse：验证失败和关闭流的状态，不声称解析成功。
- VersionSocketTests：真实协议与数据库的版本接入回归。
- VersionSocketTests.test_prepare_start_binds_same_version：
  验证授权版本通过安全网关并固定资料与历史。
关键变量：
（无模块级变量。）
"""

import json
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from interviews.agent_models import AgentInterview
from interviews.agent_records import reserve_request
from interviews.agent_socket import Start, agent_socket, parse_command
from interviews.api.resume_versions import resolve_resume_version, version_events
from interviews.resume_models import ResumeVersion

from .agent_fixtures import RESUME, FixtureLLM, SafetyTestMixin
from .test_agent_progress import collect_until, disconnect, read, send_command


async def parsing_fixture(data, *, mode="traditional"):
    """输入替身 PDF 和模式（默认传统），输出确定性 result；模拟外部解析，不验证 PDF 或视觉服务。"""
    yield b'{"type":"result","text":"Built Python APIs","pages":[]}'


async def failed_fixture(data, *, mode="traditional"):
    """输入替身字节和模式，输出固定错误事件，模拟管线已报告失败，不吞掉真实异常。"""
    yield b'{"type":"error","detail":"test-only"}'


async def pending_fixture(data, *, mode="traditional"):
    """输入替身字节和模式，输出非终态进度，供消费后关闭流并验证 interrupted。"""
    yield b'{"type":"progress","stage":"rules"}'


class ResumeVersionTests(APITestCase):
    """功能：验证授权与持久化；输入为隔离账号和数据库；约束：所有收费模型均不调用。"""

    def setUp(self):
        """建立两个测试用户并认证第一个；测试框架隔离数据库，不依赖本地用户或真实秘密。"""
        self.owner = get_user_model().objects.create_user(
            username="resume-owner", password="test-only"
        )
        self.other = get_user_model().objects.create_user(
            username="resume-other", password="test-only"
        )
        self.client.force_authenticate(self.owner)

    def test_versions_current_and_permissions(self):
        """两次上传生成不同版本，选择当前不覆盖文本；跨用户查询/选择/删除返回 404，匿名拒绝。"""
        first = self.client.post("/api/resume-versions/", {"text": "Version one"}, format="json")
        second = self.client.post("/api/resume-versions/", {"text": "Version two"}, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertNotEqual(first.data["id"], second.data["id"])
        for item in [first, second]:
            self.assertEqual(
                self.client.post(f"/api/resume-versions/{item.data['id']}/current/").status_code,
                200,
            )
        self.assertEqual(ResumeVersion.objects.filter(is_current=True).count(), 1)
        self.assertEqual(ResumeVersion.objects.get(pk=first.data["id"]).text, "Version one")
        self.client.force_authenticate(self.other)
        url = f"/api/resume-versions/{first.data['id']}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url + "current/").status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.get("/api/resume-versions/").data["count"], 0)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/resume-versions/").status_code, 403)

    def test_pdf_parse_and_private_download(self):
        """使用非真实 PDF 的解析替身；上传只存文件，显式消费流才完成解析。
        下载原字节且不可重复解析。
        """
        data = b"%PDF-test-only"
        response = self.client.post(
            "/api/resume-versions/",
            {"file": SimpleUploadedFile("resume.pdf", data)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        url = f"/api/resume-versions/{response.data['id']}/"
        self.assertEqual(response.data["status"], "uploaded")
        self.assertEqual(self.client.post(url + "current/").status_code, 400)
        downloaded = self.client.get(url + "download/")
        self.assertEqual(downloaded.content, data)
        self.assertEqual(downloaded["Cache-Control"], "no-store, private")
        with (
            patch("interviews.resume_api.resume_events", parsing_fixture),
            patch("interviews.api.resume_versions.settings.PDF_TASK_EXECUTION", "inline"),
        ):
            stream = self.client.post(url + "parse/")
            self.assertEqual(stream.status_code, 200)
            list(stream)
        version = ResumeVersion.objects.get(pk=response.data["id"])
        self.assertEqual(version.status, "ready")
        self.assertEqual(version.extraction_mode, "traditional")
        self.assertEqual(version.text, "Built Python APIs")
        self.assertEqual(self.client.post(url + "parse/").status_code, 409)
        advanced = ResumeVersion.objects.create(owner=self.owner, original_pdf=data)
        with (
            patch("interviews.resume_api.resume_events", parsing_fixture),
            patch("interviews.api.resume_versions.settings.PDF_TASK_EXECUTION", "inline"),
        ):
            list(
                self.client.post(
                    f"/api/resume-versions/{advanced.pk}/parse/",
                    {"mode": "advanced"},
                    format="json",
                )
            )
        advanced.refresh_from_db()
        self.assertEqual(advanced.extraction_mode, "advanced")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url + "download/").status_code, 404)

    def test_interview_binding_and_deletion(self):
        """真实事务绑定版本；后续当前选择不改变历史文本，引用版本拒删，无引用版本可删。"""
        version = ResumeVersion.objects.create(
            owner=self.owner, text="Original input", status="ready"
        )
        command = Start(request_id=uuid4(), type="start", resume_version_id=version.pk)
        resolved = async_to_sync(resolve_resume_version)(command, self.owner.pk)
        interview_id = uuid4()
        async_to_sync(reserve_request)(
            interview_id, resolved, owner_id=self.owner.pk, resume_version_id=version.pk
        )
        interview = AgentInterview.objects.get(pk=interview_id)
        self.assertEqual(interview.resume_text_snapshot, "Original input")
        self.assertEqual(interview.resume_version_id, version.pk)
        newer = ResumeVersion.objects.create(owner=self.owner, text="New input", status="ready")
        self.client.post(f"/api/resume-versions/{newer.pk}/current/")
        interview.refresh_from_db()
        self.assertEqual(interview.resume_text_snapshot, "Original input")
        history = self.client.get(f"/api/agent-interviews/{interview_id}/")
        self.assertEqual(str(history.data["resume_version_id"]), str(version.pk))
        self.assertEqual(history.data["processing"]["status"], "running")
        self.assertEqual(self.client.delete(f"/api/resume-versions/{version.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/resume-versions/{newer.pk}/").status_code, 204)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/agent-interviews/{interview_id}/").status_code, 404)

    def test_invalid_source_and_unready_version(self):
        """校验互斥来源及本人 ready 条件；无模型替身调用，因为失败发生在读取输入之前。"""
        version = ResumeVersion.objects.create(owner=self.owner)
        command = Start(request_id=uuid4(), type="start", resume_version_id=version.pk)
        for owner_id in [self.owner.pk, self.other.pk, None]:
            with self.assertRaises(ValueError):
                async_to_sync(resolve_resume_version)(command, owner_id)
        raw = {
            "type": "start",
            "request_id": str(uuid4()),
            "resume_text": "text",
            "resume_version_id": str(version.pk),
        }
        with self.assertRaises(ValueError):
            parse_command(json.dumps(raw))
        self.assertEqual(
            self.client.post(
                f"/api/resume-versions/{version.pk}/parse/", {"mode": ["advanced"]}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/resume-versions/", {"text": "x", "file": "x"}, format="json"
            ).status_code,
            400,
        )

    async def test_failed_and_interrupted_parse(self):
        """模拟既有错误事件和提前关闭；真实数据库状态不得变成 ready，不验证外部服务故障。"""
        from asgiref.sync import sync_to_async

        version = await sync_to_async(ResumeVersion.objects.create)(
            owner=self.owner, status="parsing"
        )
        with (
            patch("interviews.resume_api.resume_events", failed_fixture),
            patch("interviews.api.resume_versions.settings.PDF_TASK_EXECUTION", "inline"),
        ):
            async for _ in version_events(version.pk, b"%PDF-test"):
                pass
        await sync_to_async(version.refresh_from_db)()
        self.assertEqual(version.status, "failed")
        version.status = "parsing"
        await sync_to_async(version.save)()
        with (
            patch("interviews.resume_api.resume_events", pending_fixture),
            patch("interviews.api.resume_versions.settings.PDF_TASK_EXECUTION", "inline"),
        ):
            stream = version_events(version.pk, b"%PDF-test")
            await anext(stream)
            await stream.aclose()
        await sync_to_async(version.refresh_from_db)()
        self.assertEqual(version.status, "interrupted")


class VersionSocketTests(SafetyTestMixin, TransactionTestCase):
    """功能：验证版本完整协议路径；逻辑：真实 Agent/数据库配合模型与安全替身。
    约束：无真实供应商。
    """

    async def test_prepare_start_binds_same_version(self):
        """认证 scope 使用本人 ready 版本；prepare/start 均走网关。
        结构化资料保存在上下文且引用固定。
        """
        from asgiref.sync import sync_to_async

        owner = await sync_to_async(get_user_model().objects.create_user)(username="version-socket")
        version = await ResumeVersion.objects.acreate(owner=owner, text=RESUME, status="ready")
        with patch("interviews.agent_session.BackendLLM", return_value=FixtureLLM()):
            comm = ApplicationCommunicator(
                agent_socket,
                {
                    "type": "websocket",
                    "path": "/ws/agent/",
                    "scheme": "ws",
                    "user": owner,
                    "client": ("127.0.0.1", 12345),
                    "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")],
                },
            )
            await comm.send_input({"type": "websocket.connect"})
            self.assertEqual((await comm.receive_output())["type"], "websocket.accept")
            await read(comm)
            try:
                rid = await send_command(comm, "prepare", resume_version_id=str(version.pk))
                prepared = (await collect_until(comm, "prepared", rid))[-1]
                rid = await send_command(comm, "start", resume_version_id=str(version.pk))
                question = (await collect_until(comm, "question", rid))[-1]
                interview = await AgentInterview.objects.aget(pk=question["interview_id"])
                self.assertEqual(interview.resume_version_id, version.pk)
                self.assertEqual(interview.resume_text_snapshot, RESUME)
                self.assertEqual(
                    interview.context["candidate_profile"], prepared["candidate_profile"]
                )
            finally:
                await disconnect(comm)
