"""Responsibilities: Verify real ranking, save boundary, job source failure, and private access
control in personal center job recommendations.
Implementation: Isolated database and temporarily synthesized job JSON; coarse ranking executes
actual frozen model; inject explicit precision ranking stubs only at external API boundary.
Related Modules: resume_versions.recommendations, recommendation.catalog/runtime/rerank; do not
access official jobs or external models.
Declaration Index:
- PersonalRecommendationTests: Integrated test for personal snapshot recommendations.
- PersonalRecommendationTests.setUp: Create two users, save slots, and synthesize directory.
- PersonalRecommendationTests.tearDown: Disable configuration override and clean up temporary
  directory.
- PersonalRecommendationTests.write_catalog: Write explicit test directory, do not touch production
  configuration.
- PersonalRecommendationTests.model_output: External API stub returns bounded jobs in reverse order
  and synthesized bilingual rationale.
- PersonalRecommendationTests.test_real_model_uses_saved_snapshot:
  Coarse ranking scores unchanged, final order determined by precision ranking.
- PersonalRecommendationTests.test_llm_failure_is_explicit: Precision ranking failure returns fixed
  status, no coarse ranking fallback.
- PersonalRecommendationTests.test_permissions_and_csrf: Reject cross-user, anonymous, and missing
  CSRF requests.
- PersonalRecommendationTests.test_saved_ready_and_empty_boundaries: Reject unprocessed, unknown,
  and unsaved inputs.
- PersonalRecommendationTests.test_catalog_failures_do_not_fabricate_results:
  Fail on missing, invalid, duplicate, or excessive directories.
- PersonalRecommendationTests.test_model_failure_is_explicit: Model unavailable returns failure, not
  fallback result.
- PersonalRecommendationTests.test_invalid_saved_values_are_diagnostic:
  Corrupted saved data and out-of-range values fail explicitly.
- PersonalRecommendationTests.test_bundled_experience_catalog: All experimental jobs pass contract
  and invoke original model.
Variable Index:
None
Constraints:
Real local inference does not imply model effectiveness or production job availability; all resumes
and jobs are synthetic data.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from interviews.recommendation.rerank import RerankOutput, RerankUnavailable
from interviews.recommendation.runtime import ModelUnavailable, rank_pairs
from interviews.recommendation.schemas import CandidateInput, JobInput
from interviews.resume_models import ResumeVersion


class PersonalRecommendationTests(APITestCase):
    """Function: Integrated test for personal recommendations; Logic: Real permissions, ORM,
    directory, and sorting; Constraint: No reading or writing real user records.
    """

    def setUp(self):
        """No external input; create isolated users and save ready snapshots, explicitly provide
        temporary directories for two roles, do not modify models.
        """
        self.owner = get_user_model().objects.create_user(
            username="job-owner", password="test-pass"
        )
        self.other = get_user_model().objects.create_user(username="job-other")
        self.version = ResumeVersion.objects.create(
            owner=self.owner,
            status="ready",
            text="Synthetic resume",
            recommendation_slots={"skills": ["Python"], "months_experience": 6},
        )
        self.url = f"/api/resume-versions/{self.version.pk}/recommendations/"
        self.directory = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.directory.name) / "jobs.json"
        self.catalog = {
            "source_name": "Test jobs",
            "source_kind": "experience",
            "jobs": [
                {
                    "title": "Python internship",
                    "company": "Test company",
                    "requirements": {
                        "job_id": "python",
                        "required_skills": ["Python"],
                        "min_months_experience": 3,
                    },
                },
                {
                    "title": "Java internship",
                    "requirements": {"job_id": "java", "required_skills": ["Java"]},
                },
            ],
        }
        self.write_catalog(self.catalog)
        self.override = override_settings(RECOMMENDATION_JOB_CATALOG=str(self.catalog_path))
        self.override.enable()
        self.client.force_authenticate(self.owner)
        self.llm_patch = patch(
            "interviews.recommendation.rerank.request_rerank", side_effect=self.model_output
        )
        self.llm_call = self.llm_patch.start()
        self.addCleanup(self.llm_patch.stop)

    def tearDown(self):
        """Read instance configuration and temporary directory, restore Django settings and delete
        test files; no involvement with production directories.
        """
        self.override.disable()
        self.directory.cleanup()

    def write_catalog(self, data):
        """Input: JSON-encodable data; write to current temporary directory; no return value;
        invalid contracts used for boundary testing.
        """
        self.catalog_path.write_text(json.dumps(data), encoding="utf-8")

    def model_output(self, payload):
        """Input: candidate JSON; output: the final_count items in reverse order and fixed
        rationale; only substitutes external API, does not substitute coarse ranking.
        """
        jobs = [
            {
                "job_id": item["job_id"],
                "reason_zh": "已保存 Python 技能；其他条件仍需核对。",
                "reason_en": "Python is recorded; other requirements still need review.",
            }
            for item in reversed(payload["shortlist"])
        ][: payload["final_count"]]
        return RerankOutput.model_validate({"jobs": jobs}), "test-api-model"

    def test_llm_failure_is_explicit(self):
        """External call stub explicitly throws error; API/configuration returns 503, bad output
        returns 502, no coarse ranking returned or automatic re-call.
        """
        for code, status in [
            ("recommendation_llm_not_configured", 503),
            ("recommendation_llm_unavailable", 503),
            ("recommendation_llm_invalid_output", 502),
        ]:
            self.llm_call.reset_mock()
            self.llm_call.side_effect = RerankUnavailable(code)
            response = self.client.post(self.url)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.data, {"code": code})
            self.llm_call.assert_called_once()

    def test_real_model_uses_saved_snapshot(self):
        """Coarse ranking runs actual LightGBM, simulates API reversal of rankings; verify original
        scores unchanged and fine-ranking rationale, do not verify vendor.
        """
        candidate = CandidateInput(
            candidate_id=str(self.version.pk), **self.version.recommendation_slots
        )
        expected = rank_pairs(
            [(candidate, JobInput(**job["requirements"])) for job in self.catalog["jobs"]], "jobs"
        )
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.assertEqual(response.data["resume_version_id"], str(self.version.pk))
        self.assertTrue(response.data["experimental"])
        self.assertEqual(response.data["source_kind"], "experience")
        for actual, raw in zip(
            response.data["results"], reversed(expected["results"]), strict=True
        ):
            self.assertEqual(
                {key: actual[key] for key in raw if key != "rank"},
                {key: raw[key] for key in raw if key != "rank"},
            )
            self.assertEqual(actual["coarse_rank"], raw["rank"])
            self.assertTrue(actual["recommendation_reason"]["zh"])
            self.assertEqual(
                actual["matched_skills"], ["Python"] if actual["job_id"] == "python" else []
            )
        self.assertEqual(response.data["sorted_by"], "llm_order")
        self.assertEqual([item["rank"] for item in response.data["results"]], [1, 2])
        payload = self.llm_call.call_args.args[0]
        self.assertNotIn("candidate_id", payload["candidate"])
        self.assertEqual(payload["candidate"]["months_experience"], 6)
        self.assertNotIn("text", payload)
        self.version.refresh_from_db()
        self.assertEqual(
            self.version.recommendation_slots, {"skills": ["Python"], "months_experience": 6}
        )

    def test_permissions_and_csrf(self):
        """Reject across users and anonymous requests; real SessionAuthentication rejects without
        CSRF, only accepts actual sorting with correct token.
        """
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        session = APIClient(enforce_csrf_checks=True)
        session.force_login(self.owner)
        self.assertEqual(session.post(self.url).status_code, 403)
        session.get("/resumes/")
        token = session.cookies["csrftoken"].value
        self.assertEqual(session.post(self.url, HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_saved_ready_and_empty_boundaries(self):
        """Do not accept frontend candidate overrides, unready state, or fully unknown snapshots;
        skills in original text do not automatically become confirmed fields.
        """
        self.assertEqual(
            self.client.post(self.url, {"skills": ["Java"]}, format="json").status_code, 400
        )
        self.version.status = "uploaded"
        self.version.save()
        self.assertEqual(self.client.post(self.url).status_code, 409)
        self.version.status = "ready"
        self.version.text = "Skills: Python"
        self.version.recommendation_slots = {}
        self.version.save()
        self.assertEqual(self.client.post(self.url).data["code"], "recommendation_profile_empty")

    def test_catalog_failures_do_not_fabricate_results(self):
        """Explicitly empty config, malformed JSON, extra fields, duplicate IDs, and pools exceeding
        100 roles all return 503; no truncation or alternative source used.
        """
        with override_settings(RECOMMENDATION_JOB_CATALOG=""):
            self.assertEqual(self.client.post(self.url).data["code"], "job_catalog_not_configured")
        self.catalog_path.write_text("broken JSON", encoding="utf-8")
        self.assertEqual(self.client.post(self.url).status_code, 503)
        for data in [
            {**self.catalog, "extra": True},
            {**self.catalog, "jobs": [self.catalog["jobs"][0]] * 2},
            {**self.catalog, "jobs": [self.catalog["jobs"][0]] * 101},
        ]:
            self.write_catalog(data)
            response = self.client.post(self.url)
            self.assertEqual(response.data, {"code": "job_catalog_invalid"})
            self.assertEqual(response.status_code, 503)

    def test_model_failure_is_explicit(self):
        """Only simulate model unavailable exception to validate 503; do not treat this mocked test
        as real service validation, do not generate alternative recommendations.
        """
        with patch(
            "interviews.api.resume_versions.rank_pairs", side_effect=ModelUnavailable("test")
        ):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data, {"code": "recommendation_model_unavailable"})

    def test_invalid_saved_values_are_diagnostic(self):
        """Real save with corrupted type returns 503; limited numeric values exceeding original
        float32 feature range return 422, no score replacement.
        """
        for slots, expected in [
            ({"skills": "bad"}, "recommendation_profile_invalid"),
            ({"months_experience": 1e308}, "recommendation_features_invalid"),
        ]:
            self.version.recommendation_slots = slots
            self.version.save()
            self.assertEqual(self.client.post(self.url).data["code"], expected)

    def test_bundled_experience_catalog(self):
        """Explicitly select 100 roles from same source and actually invoke coarse ranking; verify
        20 roles sent to API stub and 5 roles displayed.
        """
        path = Path(__file__).resolve().parents[1] / "recommendation/data/experience-jobs.json"
        with override_settings(RECOMMENDATION_JOB_CATALOG=str(path)):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["results"]), 5)
        self.assertEqual(response.data["pipeline"]["catalog_count"], 100)
        self.assertEqual(len(self.llm_call.call_args.args[0]["shortlist"]), 20)
        self.assertEqual(response.data["source_kind"], "experience")
        self.assertTrue(
            {item["job_id"] for item in response.data["results"]}.issubset(
                {f"J{number:04}" for number in range(1, 101)}
            )
        )
        self.assertTrue(
            all("体验岗位" not in item["job"]["title"] for item in response.data["results"])
        )
