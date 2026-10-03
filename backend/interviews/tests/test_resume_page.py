"""职责：验证独立简历页的登录门禁、身份转义、CSRF 和导航，所有用户写入只在测试数据库。

实现：真实 Django 模板、会话和 HTTP Client，不替代权限中间件；关闭全局门禁验证本地仍保护个人页。
关联：demo_asset、config.urls、resumes.html；不调用模型或 PDF 解析器。

目录：
- ResumePageTests：独立页面的真实 HTTP/模板边界测试。
- ResumePageTests.setUp：创建仅存在于隔离数据库的用户。
- ResumePageTests.test_requires_login_even_in_local_mode：页面与静态 HTML 别名都拒绝匿名身份。
- ResumePageTests.test_authenticated_page_and_assets：
  登录用户看到转义身份、CSRF、控件及默认传统模式；复盘模块按白名单返回脚本。
- ResumePageTests.test_navigation_from_existing_pages：主页和面试页均有独立简历入口。
- ResumePageTests.test_interview_has_no_inline_maintenance：面试仅选择已保存版本，旧维护资源删除。

关键变量：
（无模块级变量。）
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


@override_settings(INTERVIEW_REQUIRE_LOGIN=False)
class ResumePageTests(TestCase):
    """功能：验证本地模式下真实页面；逻辑：隔离认证状态；约束：不证明浏览器布局或模型可用。"""

    def setUp(self):
        """无外部输入；创建带 HTML 字符的用户名，验证身份始终按文本渲染。"""
        self.user = get_user_model().objects.create_user(username="<resume-user>", password="a")

    def test_requires_login_even_in_local_mode(self):
        """输入匿名请求；规范路由和静态别名均跳转登录，并保留安全的 next 参数。"""
        for path in (
            "/resumes/",
            "/stream-demo/resumes.html",
            "/agent/",
            "/stream-demo/agent.html",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith("/login/?next="))

    def test_authenticated_page_and_assets(self):
        """输入真实 session；验证个人摘要、CSRF、传统默认值、复盘控件和资源类型，无生产用户变更。"""
        self.client.force_login(self.user)
        response = self.client.get("/resumes/")
        self.assertContains(response, "&lt;resume-user&gt;")
        self.assertContains(response, 'id="upload-form"')
        self.assertContains(response, 'id="profile-form"')
        for control in (
            "uploaded-parse",
            "edition-form",
            "unit-projects",
            "slot-skills",
            "edition-save",
            "history-list",
            "history-dialog",
        ):
            self.assertContains(response, f'id="{control}"')
        self.assertContains(response, 'value="traditional" selected')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertNotContains(response, 'content="NOTPROVIDED"')
        self.assertEqual(response["Cache-Control"], "no-store")
        for name, content_type in (
            ("resumes.js", "text/javascript"),
            ("resumes.css", "text/css"),
            ("interview-progress.js", "text/javascript"),
            ("interview-history.js", "text/javascript"),
            ("interview-history.css", "text/css"),
        ):
            asset = self.client.get("/stream-demo/" + name)
            self.assertEqual(asset.status_code, 200)
            self.assertTrue(asset["Content-Type"].startswith(content_type))

    def test_navigation_from_existing_pages(self):
        """既有页面包含独立入口；不改变面试参数或自动创建简历版本。"""
        self.client.force_login(self.user)
        for path in ("/", "/agent/"):
            self.assertContains(self.client.get(path), 'href="/resumes/"')

    def test_interview_has_no_inline_maintenance(self):
        """本人面试页只保留版本选取，已删除脚本不可通过白名单继续访问；不修改协议文本入口。"""
        self.client.force_login(self.user)
        response = self.client.get("/agent/")
        self.assertContains(response, 'id="resume-select"')
        for name in ('id="pdf-file"', 'id="resume"', 'id="prepare-resume"', "resume-pdf.js"):
            self.assertNotContains(response, name)
        self.assertEqual(self.client.get("/stream-demo/resume-pdf.js").status_code, 404)
