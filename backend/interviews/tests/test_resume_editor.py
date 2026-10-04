"""Responsibilities: Verify resume edition persistence, ownership, source handling, and
recommendation-slot API contracts.
Implementation: Use isolated database users and real DRF validation/download responses; external
extraction and paid models are not called.
Related Modules: interviews.resume_editor, resume_models, resume_versions, and recommendation
CandidateInput.
Declaration Index:
- ResumeEditorTests: Verify edition persistence, access boundaries, section parsing, and slot
  confirmation.
- ResumeEditorTests.setUp: Create isolated users and a synthetic ready source resume.
- ResumeEditorTests.test_independent_editions_and_export: Ensure editions remain traceable and
  protect referenced versions.
- ResumeEditorTests.test_slots_reusable_without_inference: Validate reusable confirmed slots without
  inferred values.
- ResumeEditorTests.test_invalid_editions_do_not_write: Reject invalid edit payloads without
  creating versions.
- ResumeEditorTests.test_owner_and_ready_boundaries: Enforce owner authorization and ready-state
  prerequisites.
- ResumeEditorTests.test_heading_grouping_retains_content: Preserve content while grouping
  recognized and unknown headings.
- ResumeEditorTests.test_english_labels_round_trip_through_section_parser: Verify every English
  display label maps back
  to its section
  identifier.
- ResumeEditorTests.test_extracted_fields_are_not_confirmed_until_saved: Keep suggestions
  unconfirmed until an
  explicit edition save.
Variable Index:
None

Constraints:
Database and API behavior execute against the test database; these tests do not establish real PDF
extraction, model quality, or browser layout.
"""

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from interviews.recommendation.schemas import JobsRequest
from interviews.resume_editor import UNIT_LABELS, render_units, split_units
from interviews.resume_models import ResumeVersion


class ResumeEditorTests(APITestCase):
    """Functionality: Verify the resume-edition API contract.
    Inputs: Isolated test users, synthetic resume data, and authenticated HTTP requests.
    Outputs: Assertions over API responses and test-database state.
    Logic: Exercise real authorization, validation, persistence, and export paths.
    Constraints: All records are created in the isolated test database.
    """

    def setUp(self):
        """Functionality: Create the shared test owner, second user, source resume, and
        authenticated client.
        Inputs: No external data; all account and PDF bytes are synthetic.
        Outputs: Initializes owner, other, original, URL, and authenticated client state.
        Logic: Persist the ready source resume directly without invoking PDF parsing.
        Constraints: The database is isolated by APITestCase and no real file is read.
        """
        self.owner = get_user_model().objects.create_user(username="editor-owner")
        self.other = get_user_model().objects.create_user(username="editor-other")
        self.original = ResumeVersion.objects.create(
            owner=self.owner,
            label="Original",
            status="ready",
            original_name="sample.pdf",
            original_pdf=b"%PDF-test-only",
            text="教育经历\n计算机专业\n项目经历\nPython API\n",
        )
        self.url = f"/api/resume-versions/{self.original.pk}/"
        self.client.force_authenticate(self.owner)

    def test_independent_editions_and_export(self):
        """Functionality: Verify edition lineage, source preservation, exports, and protected
        deletion.
        Inputs: The synthetic ready source resume and two edition payloads.
        Outputs: Assertions over saved lineage, source bytes/text, export headers/content, and
        delete statuses.
        Logic: Create two successive editions, inspect the source and snapshots, then exercise
        download/export/delete routes.
        Constraints: No external extraction or model call occurs; referenced source editions cannot
        be deleted.
        """
        first = self.client.post(
            self.url + "editions/",
            {
                "label": "Backend",
                "units": {"projects": "缓存项目\n优化延迟 <script>"},
            },
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        edition = ResumeVersion.objects.get(pk=first.data["id"])
        self.assertEqual(edition.source_version, self.original)
        self.assertEqual(edition.edited_from, self.original)
        self.assertIsNone(edition.original_pdf)
        self.assertEqual(edition.text, "Projects\n缓存项目\n优化延迟 <script>")
        self.assertFalse(edition.is_current)
        edition_url = f"/api/resume-versions/{edition.pk}/"
        second = self.client.post(
            edition_url + "editions/",
            {
                "units": {"projects": "第二稿"},
            },
            format="json",
        )
        self.assertEqual(second.status_code, 201)
        derived = ResumeVersion.objects.get(pk=second.data["id"])
        self.assertEqual(derived.source_version, self.original)
        self.assertEqual(derived.edited_from, edition)
        self.original.refresh_from_db()
        edition.refresh_from_db()
        self.assertEqual(self.original.text, "教育经历\n计算机专业\n项目经历\nPython API\n")
        self.assertEqual(self.client.get(self.url + "download/").content, b"%PDF-test-only")
        exported = self.client.get(edition_url + "export/")
        self.assertEqual(exported.content.decode("utf-8"), edition.text)
        self.assertEqual(exported["Cache-Control"], "no-store, private")
        editor = self.client.get(edition_url + "editor/").data
        self.assertEqual(editor["units"]["projects"], "缓存项目\n优化延迟 <script>")
        self.assertEqual(editor["original_text"], self.original.text)
        self.assertEqual(self.client.delete(self.url).status_code, 409)
        self.assertEqual(self.client.delete(edition_url).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/resume-versions/{derived.pk}/").status_code, 204)

    def test_slots_reusable_without_inference(self):
        """Functionality: Verify confirmed recommendation slots remain typed and reusable.
        Inputs: An initial profile request and an edition with explicit list, zero, false, and null
        values.
        Outputs: A validated JobsRequest built from the saved candidate profile.
        Logic: Save explicit slots, retrieve the profile, and validate it with the existing
        recommendation schema.
        Constraints: Unknown fields remain null; natural language is not used to infer slot values.
        """
        initial = self.client.get(self.url + "editor/").data
        self.assertEqual(set(initial["units"]), set(UNIT_LABELS))
        self.assertIsNone(initial["slots"]["skills"])
        self.assertIsNone(initial["slots"]["majors"])
        response = self.client.post(
            self.url + "editions/",
            {
                "units": {"skills": "Python", "projects": "API"},
                "slots": {
                    "skills": ["Python"],
                    "interests": [],
                    "months_experience": 0,
                    "num_publications": 0,
                    "commit_to_summer": False,
                    "gpa": None,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        profile = self.client.get(
            f"/api/resume-versions/{response.data['id']}/recommendation-profile/"
        ).data
        candidate = profile["candidate"]
        self.assertEqual(candidate["skills"], ["Python"])
        self.assertEqual(candidate["interests"], [])
        self.assertEqual(candidate["months_experience"], 0)
        self.assertIs(candidate["commit_to_summer"], False)
        self.assertIsNone(candidate["academic_level"])
        self.assertIsNone(candidate["gpa"])
        request = JobsRequest.model_validate(
            {
                "candidate": candidate,
                "jobs": [
                    {
                        "job_id": "api-engineer",
                        "required_skills": ["Python"],
                    }
                ],
            }
        )
        self.assertEqual(request.candidate.candidate_id, response.data["id"])

    def test_invalid_editions_do_not_write(self):
        """Functionality: Verify invalid edition payloads are rejected without persistence.
        Inputs: Payloads with missing/unknown sections, oversized text, identity injection, or
        invalid slot types.
        Outputs: HTTP 400 for every invalid payload and an unchanged version count.
        Logic: Submit each invalid body independently and inspect the resulting database state.
        Constraints: The source version remains untouched and no external service is called.
        """
        invalid = [
            {"units": {}},
            {"units": {"unknown": "Text"}},
            {"units": {"projects": "x" * 200000}},
            {"units": {"projects": "API"}, "owner": self.other.pk},
            {"units": {"projects": "API"}, "slots": {"candidate_id": "other"}},
            {"units": {"projects": "API"}, "slots": {"months_experience": "2"}},
            {"units": {"projects": "API"}, "slots": {"num_publications": 1.5}},
            {"units": {"projects": "API"}, "slots": {"commit_to_summer": "false"}},
            {"units": {"projects": "API"}, "slots": {"academic_level": "Senior"}},
        ]
        for body in invalid:
            with self.subTest(body_keys=list(body)):
                self.assertEqual(
                    self.client.post(self.url + "editions/", body, format="json").status_code, 400
                )
        self.assertEqual(ResumeVersion.objects.count(), 1)

    def test_owner_and_ready_boundaries(self):
        """Functionality: Verify ownership and ready-state access boundaries for edition endpoints.
        Inputs: The owner, a different authenticated user, an anonymous client state, and the source
        version.
        Outputs: Expected not-found, forbidden, and bad-request responses for protected operations.
        Logic: Exercise editor, export, profile, and edition creation under each identity and source
        state.
        Constraints: Authorization failures do not expose another user's source content.
        """
        self.client.force_authenticate(self.other)
        for action in ("editor/", "export/", "recommendation-profile/"):
            self.assertEqual(self.client.get(self.url + action).status_code, 404)
        body = {"units": {"projects": "API"}}
        self.assertEqual(
            self.client.post(self.url + "editions/", body, format="json").status_code, 404
        )
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url + "editor/").status_code, 403)
        self.client.force_authenticate(self.owner)
        self.original.status = "uploaded"
        self.original.save(update_fields=["status"])
        for action in ("editor/", "export/", "recommendation-profile/"):
            self.assertEqual(self.client.get(self.url + action).status_code, 400)
        self.assertEqual(
            self.client.post(self.url + "editions/", body, format="json").status_code, 400
        )

    def test_heading_grouping_retains_content(self):
        """Functionality: Verify standalone heading grouping preserves source content.
        Inputs: Text containing an unknown heading-like line, a recognized education heading, and an
        English projects heading.
        Outputs: Section text retaining whitespace and HTML-like characters, with unknown text under
        other.
        Logic: Call split_units and compare exact section strings.
        Constraints: Only standalone recognized headings change the active section; unknown content
        is not discarded.
        """
        units = split_units("姓名 <img>\n1. 教育经历：\n  计算机专业\nProjects\nAPI\n")
        self.assertEqual(units["other"], "姓名 <img>\n")
        self.assertEqual(units["education"], "  计算机专业\n")
        self.assertEqual(units["projects"], "API\n")
        self.assertEqual(units["skills"], "")

    def test_english_labels_round_trip_through_section_parser(self):
        """Functionality: Verify each English display label is recognized by the section parser.
        Inputs: Every section identifier with a unique nonempty synthetic body, tested one at a
        time.
        Outputs: The original section mapping after render_units followed by split_units.
        Logic: Render each single-section payload and parse the generated heading back to its stable
        identifier.
        Constraints: Single-section cases isolate heading compatibility from separator whitespace
        between multiple sections.
        """
        for key in UNIT_LABELS:
            with self.subTest(unit=key):
                units = {name: f"content for {name}" if name == key else "" for name in UNIT_LABELS}
                self.assertEqual(split_units(render_units(units)), units)

    def test_extracted_fields_are_not_confirmed_until_saved(self):
        """Functionality: Verify extracted slot suggestions stay unconfirmed until explicitly saved.
        Inputs: A source resume with synthetic skills and GPA evidence.
        Outputs: Assertions that editor GET is read-only and recommendation reads only saved slot
        values.
        Logic: Compare initial suggestions and persisted state, save an edition, and reload its
        editor/profile.
        Constraints: Suggestions are not written automatically; manual clearing of a slot is
        preserved.
        """
        self.original.text = "关键技能\nPython, Django\n教育经历\nGPA: 3.8 / 4.0\n"
        self.original.save(update_fields=["text"])
        editor = self.client.get(self.url + "editor/").data
        suggestions = editor["slot_suggestions"]
        self.assertEqual(set(suggestions["values"]), set(editor["slot_units"]))
        self.assertEqual(suggestions["values"]["skills"], ["Python", "Django"])
        self.assertEqual(suggestions["values"]["gpa"], 3.8)
        self.assertEqual(suggestions["evidence"]["gpa"][0]["unit"], "education")
        self.assertIsNone(editor["slots"]["skills"])
        self.original.refresh_from_db()
        self.assertEqual(self.original.recommendation_slots, {})
        self.assertIsNone(
            self.client.get(self.url + "recommendation-profile/").data["candidate"]["skills"]
        )
        saved = self.client.post(
            self.url + "editions/",
            {"units": editor["units"], "slots": {"skills": ["SQL"], "gpa": None}},
            format="json",
        )
        self.assertEqual(saved.status_code, 201, saved.data)
        url = f"/api/resume-versions/{saved.data['id']}/"
        reloaded = self.client.get(url + "editor/").data
        self.assertIsNone(reloaded["slot_suggestions"])
        self.assertEqual(reloaded["slots"]["skills"], ["SQL"])
        self.assertIsNone(reloaded["slots"]["gpa"])
        self.assertEqual(
            self.client.get(url + "recommendation-profile/").data["candidate"]["skills"], ["SQL"]
        )
