"""Responsibilities: Verify one canonical resume feeds editing, recommendation and interviews.
Implementation: Use real schemas/database/API with one explicit offline model output; verify source
coverage failures, type alignment, confirmations, cache reuse and ownership without provider calls.
Related Modules: shared.resume_contract, app.parsing.resume, resume_profile and resume_versions.
Declaration Index:
- UnifiedResumeTests: Exercise canonical lifecycle and cross-consumer contracts.
- UnifiedResumeTests.setUp: Create an isolated owner and install a recording model boundary.
- UnifiedResumeTests.model: Return explicit canonical fixture facts with original source references.
- UnifiedResumeTests.upload: Create an original through the real upload API.
- UnifiedResumeTests.test_all_consumers_share_one_output: Compare facts without further model calls.
- UnifiedResumeTests.test_manual_changes_and_clearing_are_authoritative: Preserve typed user edits.
- UnifiedResumeTests.test_invalid_model_coverage_fails_without_rule_fallback: Reject missing lines.
- UnifiedResumeTests.test_invalid_types_and_evidence_fail: Reject coercion and fabricated
  references.
- UnifiedResumeTests.test_old_cache_requires_explicit_owned_preparation: Prevent silent migration.
- UnifiedResumeTests.test_field_contracts_match_frontend_and_recommendation: Guard names and types.
- UnifiedResumeTests.test_source_assignment_round_trip: Preserve source text without model
  rewriting.
- UnifiedResumeTests.test_incomplete_experiences_fail_before_publication: Preserve source
  on failure.
- UnifiedResumeTests.test_corrupt_experience_cache_rejected: Guard model-free interview reads.
Variable Index:
- SOURCE: Synthetic source text used by the isolated API and projection tests.
Constraints:
Mocked semantic output verifies plumbing, not real model recognition quality. No external service
or production user data is accessed; question preparation is explicitly disabled in this suite.
"""

import json
import re
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from pydantic import ValidationError
from rest_framework.test import APITestCase

from app.parsing.resume import document_units, validate_document
from interviews.recommendation.schemas import CandidateInput
from interviews.resume_models import INTERVIEW_PROFILE_VERSION, ResumeVersion
from interviews.resume_profile import read_document, read_profile
from shared.resume_contract import UNIT_LABELS, ResumeExtraction, ResumeFacts
from tests.app.resume_fixture import resume_output

SOURCE = "Demo Candidate\nBuilt a documented Python API with tests.\nAvailable 20 hours per week."


@override_settings(QUESTION_PREPARATION_EXECUTION="disabled")
class UnifiedResumeTests(APITestCase):
    """Functionality: Verify shared canonical values and immutable user-version boundaries.
    Logic: Exercise production projections and APIs with a deliberately explicit model substitute.
    Constraints: Database is isolated; no network, model retries or external question preparation.
    """

    def setUp(self):
        """Functionality: Install one recording model substitute and an authenticated owner.
        Inputs: Test database. Outputs: owner, client, calls and factory patch state.
        Logic: Patch only the provider factory; keep parser/validation/API implementations real.
        Constraints: Cleanup restores the factory after each test; model outputs are synthetic.
        """
        self.owner = get_user_model().objects.create_user(username="canonical-owner")
        self.client.force_authenticate(self.owner)
        self.calls = []
        patcher = patch("interviews.resume_profile.OpenAILLM", return_value=self.model)
        patcher.start()
        self.addCleanup(patcher.stop)

    def model(self, prompt, data, schema):
        """Functionality: Supply explicit canonical facts and deterministic source attribution.
        Inputs: Production prompt/request/schema. Outputs: Real ResumeExtraction instance.
        Logic: Record the request, keep experience block names and return known fixture values.
        Constraints: Values are test assertions, not inferred facts or a production classifier.
        """
        self.calls.append(schema)
        self.assertIs(schema, ResumeExtraction)
        projects = [
            {
                "name": block["header"],
                "claims": [block["text"]],
                "source_lines": block["line_numbers"],
            }
            for block in data["source_blocks"]
        ]
        if not projects:
            projects = [{"name": "Documented API", "claims": [SOURCE.splitlines()[1]]}]
        return resume_output(
            data,
            skills=["Python"],
            interests=["Backend"],
            gpa=3.7,
            hours_per_week=20,
            length_of_commitment=6,
            in_person_commitment="Hybrid",
            commit_to_summer=False,
            months_experience=0,
            num_publications=0,
            projects=projects,
        )

    def upload(self):
        """Functionality: Create an original via the real API and return its stored version.
        Inputs: SOURCE and the authenticated client. Outputs: Ready canonical version.
        Logic: Assert successful creation and structured readiness before returning.
        Constraints: No manual database cache injection bypasses the production parser.
        """
        result = self.client.post("/api/resume-versions/", {"text": SOURCE}, format="json")
        self.assertEqual(result.status_code, 201)
        self.assertTrue(result.data["has_structured_resume"], result.data)
        return ResumeVersion.objects.get(pk=result.data["id"])

    def test_all_consumers_share_one_output(self):
        """Functionality: Demonstrate one extraction supplies editor, recommendation and interview.
        Inputs: One uploaded original and subsequent read requests.
        Outputs: Identical skills and eleven fact values; exactly one recorded model call.
        Logic: Compare canonical storage, API projections and bound CandidateProfile adaptation.
        Constraints: Original recommendations require a saved confirmation; reads are model-free.
        """
        version = self.upload()
        url = f"/api/resume-versions/{version.pk}/"
        document = read_document(version)
        editor = self.client.get(url + "editor/").data
        recommendation = self.client.get(url + "recommendation-profile/").data
        profile, _ = read_profile(version, candidate_id="interview-specific")
        facts = document.model_dump(include=set(ResumeFacts.model_fields))
        self.assertEqual(editor["slot_suggestions"]["values"], facts)
        self.assertEqual({k: recommendation["candidate"][k] for k in facts}, facts)
        self.assertEqual(profile.skills, facts["skills"])
        self.assertEqual(profile.projects[0].claims[0].text, document.projects[0].claims[0])
        self.assertEqual(version.interview_profile_version, INTERVIEW_PROFILE_VERSION)
        self.assertEqual(set(version.interview_profile), {"document", "confirmed_fields"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.client.post(url + "prepare-interview/").status_code, 200)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(recommendation["confirmed"])
        self.assertEqual(
            self.client.post(url + "recommendations/", {}, format="json").status_code, 422
        )

    def test_manual_changes_and_clearing_are_authoritative(self):
        """Functionality: Preserve user overrides in every consumer, including null/false/zero/[].
        Inputs: A canonical original and two explicitly authored edition payloads.
        Outputs: Shared overridden skills; original document unchanged; cleared facts stay empty.
        Logic: Save editions through production parsing and compare all downstream projections.
        Constraints: Model fixture still proposes Python; it cannot silently override user choices.
        """
        original = self.upload()
        before = json.dumps(original.interview_profile, sort_keys=True)
        for skills in (["SQL"], []):
            response = self.client.post(
                f"/api/resume-versions/{original.pk}/editions/",
                {
                    "units": {"projects": SOURCE},
                    "slots": {
                        "skills": skills,
                        "gpa": None,
                        "months_experience": 0,
                        "commit_to_summer": False,
                        "interests": [],
                    },
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201)
            version = ResumeVersion.objects.get(pk=response.data["id"])
            document = read_document(version)
            editor = self.client.get(f"/api/resume-versions/{version.pk}/editor/").data
            candidate = self.client.get(
                f"/api/resume-versions/{version.pk}/recommendation-profile/"
            ).data["candidate"]
            profile, _ = read_profile(version, candidate_id="bound")
            self.assertEqual(document.skills, skills)
            self.assertEqual(editor["slots"]["skills"], skills)
            self.assertEqual(candidate["skills"], profile.skills)
            self.assertEqual(profile.skills, skills)
            self.assertIsNone(candidate["gpa"])
            self.assertFalse(candidate["commit_to_summer"])
            self.assertEqual(candidate["months_experience"], 0)
            self.assertEqual(editor["units"]["projects"], SOURCE)
            self.assertIsNone(editor["slot_suggestions"])
        original.refresh_from_db()
        self.assertEqual(json.dumps(original.interview_profile, sort_keys=True), before)
        self.assertEqual(original.text, SOURCE)

    def test_invalid_model_coverage_fails_without_rule_fallback(self):
        """Functionality: Fail explicitly when the model omits source lines.
        Inputs: A schema-valid output assigning only the first source line.
        Outputs: Preserved original text, failed structure, and unavailable editor projection.
        Logic: Corrupt source coverage at the model boundary and inspect actual failure state.
        Constraints: No alternate extractor or automatic retry is allowed.
        """
        output = resume_output(
            {"resume_text": SOURCE}, skills=["Python"], section_lines={"basic": [1]}
        )
        with patch("interviews.resume_profile.OpenAILLM", return_value=lambda *args: output):
            result = self.client.post("/api/resume-versions/", {"text": SOURCE}, format="json")
        version = ResumeVersion.objects.get(pk=result.data["id"])
        self.assertEqual(version.text, SOURCE)
        self.assertEqual(version.interview_profile_status, "failed")
        self.assertIsNone(version.interview_profile)
        self.assertEqual(
            self.client.get(f"/api/resume-versions/{version.pk}/editor/").status_code, 409
        )

    def test_invalid_types_and_evidence_fail(self):
        """Functionality: Reject type coercion, invalid source IDs and unsupported field values.
        Inputs: Canonical payload mutations across numeric, Boolean, category and evidence fields.
        Outputs: Schema/coverage errors instead of substituted values.
        Logic: Validate production schemas and reference checks directly for each invalid case.
        Constraints: The absence of inferred values is the contract under test, not model quality.
        """
        base = resume_output({"resume_text": SOURCE}, skills=["Python"]).model_dump()
        for key, value in (
            ("hours_per_week", "20"),
            ("commit_to_summer", "false"),
            ("num_publications", 1.5),
            ("academic_level", "Senior"),
            ("gpa", float("nan")),
            ("skills", "Python"),
        ):
            with self.subTest(field=key), self.assertRaises(ValidationError):
                ResumeExtraction.model_validate({**base, key: value})
        for changes in (
            {"field_evidence": {"skills": [99]}},
            {"field_evidence": {}},
            {"section_lines": {"basic": [1, 2, 3], "other": [2]}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_document(ResumeExtraction.model_validate({**base, **changes}), SOURCE)

    def test_old_cache_requires_explicit_owned_preparation(self):
        """Functionality: Keep old cache schemas from silently serving divergent facts.
        Inputs: A legacy cache and a second authenticated user.
        Outputs: Explicit 409 for stale editor data; 404 for cross-owner preparation; one owned
        repair.
        Logic: Verify reads never call the model and only the owner's explicit POST prepares data.
        Constraints: Original text remains unchanged and no old parser is used as a fallback.
        """
        version = ResumeVersion.objects.create(
            owner=self.owner,
            text=SOURCE,
            status="ready",
            interview_profile_status="ready",
            interview_profile_version="2.0:resume-profile-v1",
            interview_profile={"candidate_profile": {"skills": ["OLD"]}},
        )
        url = f"/api/resume-versions/{version.pk}/"
        self.assertEqual(self.client.get(url + "editor/").status_code, 409)
        self.assertEqual(self.calls, [])
        other = get_user_model().objects.create_user(username="different-owner")
        self.client.force_authenticate(other)
        self.assertEqual(self.client.post(url + "prepare-interview/").status_code, 404)
        self.assertEqual(self.calls, [])
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(url + "prepare-interview/").status_code, 200)
        version.refresh_from_db()
        self.assertEqual(read_document(version).skills, ["Python"])

    def test_field_contracts_match_frontend_and_recommendation(self):
        """Functionality: Prevent model/API/frontend field-name and type drift.
        Inputs: Actual schemas and frontend declaration source.
        Outputs: Equal fact property definitions, section IDs and input-type categories.
        Logic: Compare shared JSON-schema properties and explicit frontend field maps.
        Constraints: This checks the real contract, not rendered browser layout.
        """
        facts = ResumeFacts.model_json_schema()["properties"]
        extracted = ResumeExtraction.model_json_schema()["properties"]
        candidate = CandidateInput.model_json_schema()["properties"]
        for key, definition in facts.items():
            self.assertEqual(extracted[key], definition)
            self.assertEqual(candidate[key], definition)
        source = (Path(__file__).parents[2] / "frontend/resumes.js").read_text(encoding="utf-8")
        units = re.findall(r'"([^"\n]+)"', re.search(r"const RM_UNITS = \[([^]]+)\]", source)[1])
        slots = dict(
            re.findall(r'(\w+):\s*"(\w+)"', re.search(r"const RM_SLOTS = \{([^}]+)\}", source)[1])
        )
        self.assertEqual(set(units), set(UNIT_LABELS))
        self.assertEqual(set(slots), set(facts))
        self.assertEqual(slots["num_publications"], "integer")
        self.assertEqual(slots["commit_to_summer"], "boolean")
        self.assertEqual(slots["skills"], "tags")

    def test_source_assignment_round_trip(self):
        """Functionality: Keep source text and multi-line phrases independent of rewritten model
        text.
        Inputs: Original whitespace and explicit model line/heading assignment.
        Outputs: Exact source slices in the intended sections, including unknown content.
        Logic: Validate and materialize the assignment without any title matching.
        Constraints: The model chooses placement; software preserves the original characters.
        """
        text = "Demo\n Strange heading\n  API work\nUnclassified <text>\n"
        document = resume_output(
            {"resume_text": text},
            section_lines={"basic": [1], "projects": [2, 3], "other": [4]},
            heading_lines=[2],
            projects=[{"name": "API work", "source_lines": [3]}],
        )
        validate_document(document, text)
        units = document_units(document, text)
        self.assertEqual(units["basic"], "Demo\n")
        self.assertEqual(units["projects"], "  API work\n")
        self.assertEqual(units["other"], "Unclassified <text>\n")

    def test_incomplete_experiences_fail_before_publication(self):
        """Inputs: Two-job source and one-job output with complete editor section coverage.
        Outputs: Failed canonical preparation, preserved original text and no interviewable cache.
        Logic: Exercise the real upload/preparation path with one scripted model response.
        Constraints: No retry, alternate extractor or live service; isolated test database only.
        """
        source = "Experience\nFirstCo\nBuilt an API\nSecondCo\nTested a service"
        document = resume_output(
            {"resume_text": source},
            section_lines={"experience": [1, 2, 3, 4, 5]},
            heading_lines=[1],
            projects=[{"name": "SecondCo", "source_lines": [4, 5]}],
        )
        with patch("interviews.resume_profile.OpenAILLM") as factory:
            factory.return_value.return_value = document
            response = self.client.post("/api/resume-versions/", {"text": source}, format="json")
            self.assertEqual(factory.return_value.call_count, 1)
        version = ResumeVersion.objects.get(pk=response.data["id"])
        self.assertEqual(version.text, source)
        self.assertEqual(version.interview_profile_status, "failed")
        self.assertIsNone(version.interview_profile)
        self.assertFalse(version.can_interview)

    def test_corrupt_experience_cache_rejected(self):
        """Inputs: Previously ready cache with an experience line removed from its project.
        Outputs: Read rejection before CandidateProfile can reach interview startup.
        Logic: Mutate only cached ownership, preserving sections, source hash and schema version.
        Constraints: Read path must not invoke a model or repair user data implicitly.
        """
        version = self.upload()
        version.interview_profile["document"]["projects"][0]["source_lines"].pop()
        version.save(update_fields=["interview_profile"])
        with self.assertRaisesRegex(ValueError, "resume_experience_coverage_incomplete"):
            read_profile(version, candidate_id="interview")
        self.assertEqual(len(self.calls), 1)
