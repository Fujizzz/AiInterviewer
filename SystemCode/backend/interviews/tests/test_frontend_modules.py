"""Check entry-page ES module imports through real authenticated HTTP asset routes.

The browser cannot bind any page handlers if a static import returns 404. Local
JavaScript unit tests bypass this boundary by reading files directly. Dynamic
avatar imports are covered separately by the avatar bundle deployment checks.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

STATIC_IMPORT = re.compile(
    r"""^\s*import\s+(?:[^;]*?\s+from\s+)?["']([^"']+)["']""", re.MULTILINE
)


class ModuleScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "module":
            if source := attributes.get("src"):
                self.sources.append(source)


@override_settings(INTERVIEW_REQUIRE_LOGIN=True)
class FrontendModuleTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="module-route-test")
        self.client.force_login(user)

    def assert_page_modules_load(self, page):
        response = self.client.get(page)
        self.assertEqual(response.status_code, 200)
        parser = ModuleScripts()
        parser.feed(response.content.decode())
        self.assertTrue(parser.sources, "The page must load its module entry points")
        pending = [(page, urljoin(page, src)) for src in parser.sources]
        visited = set()
        while pending:
            importer, path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            self.assertTrue(path.startswith("/stream-demo/"), (importer, path))
            asset = self.client.get(path)
            self.assertEqual(asset.status_code, 200, f"{importer} imports unavailable {path}")
            self.assertIn(
                asset["Content-Type"].split(";")[0], {"text/javascript", "application/javascript"}
            )
            self.assertEqual(asset["Cache-Control"], "no-store")
            pending.extend(
                (path, urljoin(path, dependency))
                for dependency in STATIC_IMPORT.findall(asset.content.decode())
            )

    def test_interview_page_static_imports_are_served(self):
        self.assert_page_modules_load("/agent/")

    def test_history_page_static_imports_are_served(self):
        self.assert_page_modules_load("/interview-review/")
