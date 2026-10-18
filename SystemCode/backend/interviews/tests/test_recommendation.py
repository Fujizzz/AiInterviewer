"""Responsibilities: Verify recommendation feature construction, fixed-model inference, and local
HTTP API contracts.

Implementation: Use frozen local model artifacts, synthetic profiles, and Django's in-process
APIClient; no database, LLM, or external network is used.
Related Modules: interviews.recommendation.runtime, interviews.recommendation.features, and
interviews.recommendation.schemas.

Declaration Index:
- RecommendationTests: Test suite for fixed models and HTTP contracts.
- RecommendationTests.setUp: Create local API client and known complete profile sample.
- RecommendationTests.test_feature_formula_and_unknowns: Verify 11-dimensional independent
  expectations, zero/false, and unknown
  distinctions.
- RecommendationTests.test_actual_models_match_research_predictions:
  Replay 20 frozen predictions from five research scenarios.
- RecommendationTests.test_jobs_endpoint_sorts_and_reports_missing:
  Call the local job endpoint through APIClient and verify sorting and missing entries.
- RecommendationTests.test_candidates_endpoint_sorts: Make real call to candidate endpoint and
  verify job direction.
- RecommendationTests.test_no_evidence_is_unranked: Completely evidence-free pairings produce no
  score or rank.
- RecommendationTests.test_ties_preserve_request_order: Tied scores preserve input order.
- RecommendationTests.test_invalid_inputs_are_rejected: Reject invalid types, extra fields, and
  empty skill requirements.
- RecommendationTests.test_duplicate_and_oversized_pools: Reject duplicate IDs, empty pools, and
  pools exceeding resource limits.
- RecommendationTests.test_errors_do_not_echo_profile: Validate errors do not echo original resume
  field values.
- RecommendationTests.test_corrupt_bundle_fails_closed: Corrupted bundle returns 503, no fallback
  scoring.
- RecommendationTests.test_local_access_and_method: Maintain local access policy and restrict POST
  method.

Variable Index:
None

Key State Explanation:
client is an in-process HTTP client; candidate and job values are synthetic boundary examples;
weights come from the approved v4-B model bundle.
Golden files hold the expected predictions for five fixed scenarios and contain no person identity
or resume content; corruption tests operate on temporary copies.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from interviews.recommendation import runtime
from interviews.recommendation.features import build_features
from interviews.recommendation.schemas import CandidateInput, JobInput


class RecommendationTests(SimpleTestCase):
    """Function: Protect input semantics and real inference; Logic: Dual validation via HTTP and
    fixed predictions; Constraint: No access to real external services.
    """

    def setUp(self):
        """Function: Establish known examples and client; Input: none; Output: instance state; Data
        not written to database or user files.
        """
        self.client = APIClient(HTTP_HOST="localhost")
        self.candidate = {
            "candidate_id": "example-person",
            "skills": ["python"],
            "interests": ["tech"],
            "majors": ["CS"],
            "in_person_commitment": "In Person",
            "gpa": 3.5,
            "months_experience": 0,
            "academic_level": "UG4",
            "hours_per_week": 20,
            "length_of_commitment": 6,
            "num_publications": 0,
            "commit_to_summer": False,
        }
        self.job = {
            "job_id": "example-job",
            "required_skills": ["python", "sql"],
            "industry": "tech",
            "acceptable_majors": ["CS"],
            "job_in_person_commitment": "In Person",
            "min_gpa": 3,
            "min_months_experience": 6,
            "min_academic_level": "UG3",
            "min_hours_per_week": 10,
            "min_length_of_commitment": 3,
            "min_num_publications": 0,
        }

    def test_feature_formula_and_unknowns(self):
        """Function: Verify features using hand-calculated values; Input: complete/partial
        unknown/explicitly empty skills; Output: item-by-item consistency and NaN assertions.
        """
        candidate, job = CandidateInput(**self.candidate), JobInput(**self.job)
        expected = [0.5, 1, 1, 1, 0.5, -6, 1, 10, 3, 0, 0]
        np.testing.assert_array_equal(build_features(candidate, job), expected)
        missing = CandidateInput(**{**self.candidate, "gpa": None, "commit_to_summer": None})
        self.assertTrue(np.isnan(build_features(missing, job)[[4, 10]]).all())
        empty = CandidateInput(candidate_id="empty", skills=[])
        self.assertEqual(build_features(empty, job)[0], 0)
        unknown = CandidateInput(candidate_id="unknown")
        self.assertTrue(np.isnan(build_features(unknown, job)).all())
        flexible = CandidateInput(candidate_id="flexible", in_person_commitment="No Preference")
        self.assertEqual(build_features(flexible, job)[3], 0)
        online = JobInput(job_id="online", job_in_person_commitment="Online")
        self.assertEqual(build_features(candidate, online)[3], 0)

    def test_actual_models_match_research_predictions(self):
        """Function: Verify real weights have not drifted; Input: 20 sets of frozen research
        vectors; Output: consistent score assertions; non-mocked inference.
        """
        fixtures = json.loads(Path(__file__).with_name("recommendation_golden.json").read_text())
        _, models = runtime.load_bundle()
        matrix = np.asarray([case["features"] for case in fixtures["cases"]], dtype=np.float32)
        actual = np.column_stack([model.predict(matrix, num_threads=2) for model in models])
        expected = np.asarray([case["scores"] for case in fixtures["cases"]])
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)

    def test_jobs_endpoint_sorts_and_reports_missing(self):
        """Function: Real POST job list; Input: candidates with only skills and two jobs; Output:
        limited scores, missing columns, and sorting assertions.
        """
        response = self.client.post(
            "/api/recommendations/jobs/",
            {
                "candidate": {"candidate_id": "c", "skills": ["python"]},
                "jobs": [self.job, {**self.job, "job_id": "other", "required_skills": ["sql"]}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertTrue(data["experimental"])
        self.assertFalse(data["release_gate_passed"])
        self.assertEqual(data["model_id"], "jobrec-v4-B")
        scores = [row["pref_score"] for row in data["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for row in data["results"]:
            self.assertEqual(row["available_feature_count"], 1)
            self.assertIn("gpa_margin", row["missing_features"])
            self.assertTrue(np.isfinite(row["qual_score"]))
        self.assertNotIn("NaN", response.content.decode())

    def test_candidates_endpoint_sorts(self):
        """Function: Real POST candidate list; Input: one job, two candidates; Output: sorted by job
        score, direction and ID accurate.
        """
        response = self.client.post(
            "/api/recommendations/candidates/",
            {
                "job": self.job,
                "candidates": [
                    self.candidate,
                    {**self.candidate, "candidate_id": "other", "gpa": None},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["sorted_by"], "qual_score")
        scores = [row["qual_score"] for row in response.data["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(
            all(row["job_id"] == self.job["job_id"] for row in response.data["results"])
        )

    def test_no_evidence_is_unranked(self):
        """Function: Unknown profiles do not fabricate ranking; Input: unknown and known profiles in
        same pool; Output: unknown profiles at end with null score/rank.
        """
        response = self.client.post(
            "/api/recommendations/candidates/",
            {
                "job": self.job,
                "candidates": [{"candidate_id": "unknown"}, self.candidate],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        result = response.data["results"][-1]
        self.assertEqual(result["candidate_id"], "unknown")
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["rank"])
        self.assertIsNone(result["pref_score"])
        self.assertIsNone(result["qual_score"])

    def test_ties_preserve_request_order(self):
        """Function: Tie stability; Input: two jobs with same data but different IDs; Output:
        request order preserved, not replaced by ID sorting.
        """
        response = self.client.post(
            "/api/recommendations/jobs/",
            {
                "candidate": self.candidate,
                "jobs": [{**self.job, "job_id": "z"}, {**self.job, "job_id": "a"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["job_id"] for row in response.data["results"]], ["z", "a"])

    def test_invalid_inputs_are_rejected(self):
        """Function: Validate type and range; Input: string numbers, boolean numbers, unknown codes,
        extra fields, overflow; Output: 400.
        """
        for change in (
            {"gpa": "3.5"},
            {"gpa": True},
            {"gpa": -1},
            {"gpa": 1e308},
            {"academic_level": "senior"},
            {"in_person_commitment": True},
            {"num_publications": 10**400},
            {"unexpected": 1},
            {"skills": [1]},
        ):
            with self.subTest(change=change):
                response = self.client.post(
                    "/api/recommendations/jobs/",
                    {
                        "candidate": {**self.candidate, **change},
                        "jobs": [self.job],
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(
            "/api/recommendations/jobs/",
            {
                "candidate": self.candidate,
                "jobs": [{**self.job, "required_skills": []}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/recommendations/jobs/",
            '{"candidate":{"gpa":NaN}}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_and_oversized_pools(self):
        """Function: Request pool boundary; Input: duplicate IDs, empty pool, 101 objects; Output:
        400, no silent truncation or deduplication.
        """
        for jobs in (
            [],
            [self.job, self.job],
            [{**self.job, "job_id": str(i)} for i in range(101)],
        ):
            response = self.client.post(
                "/api/recommendations/jobs/",
                {
                    "candidate": self.candidate,
                    "jobs": jobs,
                },
                format="json",
            )
            self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/recommendations/candidates/",
            {
                "job": self.job,
                "candidates": [self.candidate, self.candidate],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_errors_do_not_echo_profile(self):
        """Function: Error sanitization; Input: sentinel text within illegal values; Output: field
        location indicated but no input content revealed.
        """
        response = self.client.post(
            "/api/recommendations/jobs/",
            {
                "candidate": {**self.candidate, "gpa": "PRIVATE_PROFILE_SENTINEL"},
                "jobs": [self.job],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("PRIVATE_PROFILE_SENTINEL", response.content.decode())

    def test_corrupt_bundle_fails_closed(self):
        """Function: Corrupted model fails explicitly; Input: tampered temporary weight copy;
        Output: 503 returned, cache restored, no modification to real model.
        """
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "model"
            shutil.copytree(runtime.MODEL_DIR, target)
            (target / "pref.txt").write_text("invalid-model", encoding="utf-8")
            runtime.load_bundle.cache_clear()
            try:
                with patch.object(runtime, "MODEL_DIR", target):
                    response = self.client.post(
                        "/api/recommendations/jobs/",
                        {
                            "candidate": self.candidate,
                            "jobs": [self.job],
                        },
                        format="json",
                    )
                    self.assertEqual(response.status_code, 503, response.data)
                    self.assertEqual(response.data["error"]["code"], "recommendation_unavailable")
            finally:
                runtime.load_bundle.cache_clear()

    def test_local_access_and_method(self):
        """Function: Inherit service access boundaries; Input: GET and non-local POST; Output:
        405/403, no relaxation of existing middleware.
        """
        self.assertEqual(self.client.get("/api/recommendations/jobs/").status_code, 405)
        response = self.client.post(
            "/api/recommendations/jobs/", {}, format="json", REMOTE_ADDR="192.0.2.1"
        )
        self.assertEqual(response.status_code, 403)
