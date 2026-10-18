"""Responsibilities: Verify login gateways, identity escaping, CSRF, and navigation for resume and
standalone review pages; all user writes occur only in test database.
Implementation: Use real Django templates, sessions, and HTTP client; do not substitute permission
middleware; disable global gateway but still protect personal pages locally.
Related Modules: demo_asset, config.urls, resumes.html, interview-review.html; do not invoke model
or PDF parser.

Declaration Index:
- ResumePageTests: Real HTTP/template boundary tests for independent pages.
- ResumePageTests.setUp: Create users existing only in isolated database.
- ResumePageTests.test_requires_login_even_in_local_mode: Page and static HTML alias both reject
  anonymous identity.
- ResumePageTests.test_authenticated_page_and_assets:
  Authenticated user sees escaped identity, CSRF, traditional defaults; review delivered
  independently without loading resume management.
- ResumePageTests.test_navigation_from_existing_pages:
  Homepage, interview, resume, review, and diagnosis all share top task navigation.
- ResumePageTests.test_interview_has_no_inline_maintenance: Interview only selects saved versions;
  old maintenance resources removed.

Variable Index:
None
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


@override_settings(INTERVIEW_REQUIRE_LOGIN=False)
class ResumePageTests(TestCase):
    """Function: Verify real page in local mode; logic: isolate authentication state; constraint: do
    not prove browser layout or model availability.
    """

    def setUp(self):
        """No external input; create username with HTML characters, verify identity always rendered
        as text.
        """
        self.user = get_user_model().objects.create_user(username="<resume-user>", password="a")

    def test_requires_login_even_in_local_mode(self):
        """Input anonymous request; standard routing and static aliases redirect to login,
        preserving secure next parameter.
        """
        for path in (
            "/resumes/",
            "/stream-demo/resumes.html",
            "/agent/",
            "/stream-demo/agent.html",
            "/interview-review/",
            "/stream-demo/interview-review.html",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith("/login/?next="))

    def test_authenticated_page_and_assets(self):
        """Input real session; verify resume and standalone review templates, traditional defaults,
        escaping, and resource types, without modifying production users.
        """
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
        ):
            self.assertContains(response, f'id="{control}"')
        self.assertNotContains(response, 'id="history-list"')
        self.assertNotContains(response, "/stream-demo/interview-history.js")
        self.assertContains(response, 'value="traditional" selected')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertNotContains(response, 'content="NOTPROVIDED"')
        self.assertEqual(response["Cache-Control"], "no-store")
        for path in ("/interview-review/", "/stream-demo/interview-review.html"):
            review = self.client.get(path)
            self.assertContains(review, "&lt;resume-user&gt;")
            self.assertContains(review, 'id="history-list"')
            self.assertContains(review, 'id="history-dialog"')
            self.assertContains(review, "/stream-demo/interview-history.js")
            self.assertContains(review, 'data-i18n="review_nav" aria-current="page"')
            self.assertNotContains(review, "/stream-demo/resumes.js")
            self.assertEqual(review["Cache-Control"], "no-store")
        for name, content_type in (
            ("resumes.js", "text/javascript"),
            ("resumes.css", "text/css"),
            ("workspace.css", "text/css"),
            ("interview-progress.js", "text/javascript"),
            ("interview-history.js", "text/javascript"),
            ("interview-history.css", "text/css"),
        ):
            asset = self.client.get("/stream-demo/" + name)
            self.assertEqual(asset.status_code, 200)
            self.assertTrue(asset["Content-Type"].startswith(content_type))

    def test_navigation_from_existing_pages(self):
        """Top navigation shared across homepage, interview, resume, review, and diagnosis; resume
        page does not embed review; no interview created or parameters changed.
        """
        self.client.force_login(self.user)
        for path in ("/", "/agent/", "/resumes/", "/interview-review/", "/stream-demo/"):
            response = self.client.get(path)
            self.assertContains(response, 'class="workspace-navigation"')
            self.assertContains(response, 'href="/resumes/#job-recommendations"')
            self.assertContains(response, 'href="/resumes/"')
            self.assertContains(response, 'href="/interview-review/"')
            self.assertNotContains(response, 'href="/resumes/#interview-history"')

    def test_interview_has_no_inline_maintenance(self):
        """Personal interview page retains only version selection; deleted scripts cannot be
        accessed via whitelist; protocol text entry unchanged.
        """
        self.client.force_login(self.user)
        response = self.client.get("/agent/")
        self.assertContains(response, 'id="resume-select"')
        for name in ('id="pdf-file"', 'id="resume"', 'id="prepare-resume"', "resume-pdf.js"):
            self.assertNotContains(response, name)
        self.assertEqual(self.client.get("/stream-demo/resume-pdf.js").status_code, 404)
