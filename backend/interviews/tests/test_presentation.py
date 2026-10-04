"""Responsibilities: Verify the independent facial behavior boundary with offline doubles.
Implementation: Exercise strict contracts, one-call provider behavior, cache/admission limits and
the real Django middleware/session/CSRF path; all model requests are mocked.
Related Modules: interviews.presentation owns schemas, planning and the HTTP endpoint.

Declaration Index:
- request_fixture: Create a fresh validated question-only request with configurable correlation.
- model_completion: Wrap JSON in a complete offline OpenAI-compatible response.
- provider_status_error: Create a real SDK status exception containing deliberately private text.
- PresentationSchemaTests: Reject unsupported controls and malformed request/plan data.
- PresentationSchemaTests.test_request_identity_and_business_fields_are_strict:
  Reject coercion, oversized metadata and any answer/assessment fields before model access.
- PresentationSchemaTests.test_plan_rejects_unsafe_curves_intensity_and_timing:
  Reject arbitrary controls, incomplete profiles, excessive strength and unusable sampling windows.
- PresentationSchemaTests.test_legacy_profiles_default_motion_to_zero:
  Accept original thirteen-field profiles with head and audio tendencies disabled.
- PresentationServiceTests: Verify independent provider calls and bounded graceful degradation.
- PresentationServiceTests.setUp: Enable a private fixture configuration and fresh service clock.
- PresentationServiceTests.tearDown: Restore process environment after each isolated test.
- PresentationServiceTests.test_disabled_missing_configuration_and_timeout_bounds:
  Fail safely before constructing the provider when disabled or incompletely configured.
- PresentationServiceTests.test_model_request_is_question_only_and_retry_free:
  Assert the exact data boundary and independent provider options through the real adapter.
- PresentationServiceTests.test_before_interview_profiles_without_question:
  Generate complete four-state behavior from explicit null context before a question exists.
- PresentationServiceTests.test_openai_uses_its_own_credentials_and_no_dashscope_options:
  Select OpenAI without mixing Alibaba credentials or enabling unsupported thought options.
- PresentationServiceTests.test_invalid_incomplete_and_refused_output_falls_back:
  Select recorded-animation fallback for unsafe or incomplete output without retries or leaks.
- PresentationServiceTests.test_transport_failure_is_sanitised_and_cached:
  Coalesce question replays after provider failure and never expose its exception content.
- PresentationServiceTests.test_provider_failures_classified_without_secret_logs:
  Distinguish timeout, connection, auth, quota, rate, model and invalid-request failures safely.
- PresentationServiceTests.test_cache_recorrelates_scopes_expires_and_evicts:
  Preserve account separation, new browser identities and fixed memory/retention limits.
- PresentationServiceTests.test_busy_and_start_interval_decline_without_provider_wait:
  Bound concurrent and rapid provider starts while returning immediate recorded fallback markers.
- PresentationHTTPTests: Verify actual route validation and local-origin policy without a provider.
- PresentationHTTPTests.test_valid_response_and_rejected_payloads:
  Accept bounded question-only data and reject malformed or oversized JSON before planning.
- PresentationHTTPTests.test_cross_origin_and_remote_clients_cannot_plan:
  Reuse existing loopback/same-origin enforcement at the independent route.
- PresentationAccountTests: Verify production authentication and authenticated-write CSRF checks.
- PresentationAccountTests.test_account_and_csrf_checks_precede_planning:
  Require login and the real page's CSRF cookie before any presentation service work.

Variable Index:
- ENABLED_ENV: Private offline provider configuration, never real credentials.
- VALID_PROFILE: Bounded facial and optional local-motion parameters for offline provider output.
- VALID_PLAN: Complete four-state behavior output used by SDK doubles.
- urlpatterns: Test-local route plus existing pages for full middleware and CSRF checks.
"""

import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from interviews.presentation.schemas import ExpressionPlan, PresentationRequest
from interviews.presentation.service import (
    PresentationConfig,
    PresentationService,
    request_model_plan,
)
from interviews.presentation.views import plan

ENABLED_ENV = {
    "PRESENTATION_ENABLED": "true",
    "LLM_PROVIDER": "dashscope",
    "DASHSCOPE_API_KEY": "offline-key",
    "DASHSCOPE_MODEL": "offline-model",
    "DASHSCOPE_BASE_URL": "https://offline.invalid/compatible-mode/v1",
}
VALID_PROFILE = {
    "expression": "neutral",
    "intensity": 0.08,
    "variation": 0.3,
    "blink_min_ms": 3500,
    "blink_max_ms": 6500,
    "blink_duration_ms": 180,
    "gaze_amplitude": 0.07,
    "gaze_hold_min_ms": 1800,
    "gaze_hold_max_ms": 3000,
    "eye_contact": 0.9,
    "warmth": 0.03,
    "motion_min_ms": 4500,
    "motion_max_ms": 7500,
    "head_motion_strength": 0.08,
    "head_motion_probability": 0.1,
    "audio_emphasis_strength": 0.0,
}
VALID_PLAN = {
    "states": {
        "idle": dict(VALID_PROFILE),
        "listening": {
            **VALID_PROFILE,
            "expression": "attentive",
            "intensity": 0.12,
            "head_motion_strength": 0.4,
            "head_motion_probability": 0.35,
        },
        "thinking": {
            **VALID_PROFILE,
            "expression": "thoughtful",
            "intensity": 0.1,
            "head_motion_strength": 0.3,
            "head_motion_probability": 0.25,
        },
        "speaking": {
            **VALID_PROFILE,
            "expression": "friendly",
            "intensity": 0.1,
            "head_motion_strength": 0.25,
            "head_motion_probability": 0.2,
            "audio_emphasis_strength": 0.35,
        },
    },
}
urlpatterns = [path("api/presentation/plan/", plan), path("", include("config.urls"))]


def request_fixture(*, generation=1, question_id="q-fixture", text="Describe your contribution."):
    """Create a fresh request so cache/correlation tests cannot accidentally share mutated data."""
    return PresentationRequest.model_validate(
        {
            "version": 2,
            "presentation_id": str(uuid4()),
            "generation": generation,
            "question": {
                "question_id": question_id,
                "text": text,
                "question_type": "project",
                "intent": "Clarify ownership",
                "dialogue_action": "opening_question",
            },
        }
    )


def model_completion(payload=VALID_PLAN, *, finish_reason="stop", refusal=None):
    """Provide one complete SDK choice.

    Retain the caller's JSON/refusal failure controls.
    """
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, refusal=refusal),
            )
        ]
    )


def provider_status_error(status, code=None):
    """Build an SDK failure with private body/message/URL.

    Diagnostics must explicitly filter those private fields.
    """
    request = httpx.Request("POST", "https://private-provider.invalid/v1?key=SECRET_REQUEST_KEY")
    body = {"error": {"code": code, "message": "SECRET_PROVIDER_BODY"}}
    response = httpx.Response(status, request=request, json=body)
    return APIStatusError("SECRET_PROVIDER_MESSAGE", response=response, body=body)


class PresentationSchemaTests(SimpleTestCase):
    """Verify precise ownership and expression limits without Django database or network access."""

    def test_request_identity_and_business_fields_are_strict(self):
        """Reject invalid UUIDs, boolean generations and data outside approved question metadata."""
        base = request_fixture().model_dump()
        invalid = [
            {**base, "version": 1},
            {**base, "version": "2"},
            {**base, "presentation_id": "invalid"},
            {**base, "generation": True},
            {**base, "generation": "2"},
            {**base, "generation": 2147483648},
            {**base, "answer": "Do not disclose candidate answers."},
            {**base, "question": {**base["question"], "assessment": {"score": 0.2}}},
            {**base, "question": {**base["question"], "intent": "x" * 401}},
            {**base, "question": {**base["question"], "question_type": "x" * 81}},
            {**base, "question": {**base["question"], "dialogue_action": "x" * 81}},
            {**base, "question": {**base["question"], "text": " "}},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    PresentationRequest.model_validate(payload)

    def test_plan_rejects_unsafe_curves_intensity_and_timing(self):
        """Reject unsupported controls, coercion and unusable blink, gaze or expression ranges."""
        invalid = []
        for change in (
            {"intensity": 0.36},
            {"intensity": float("nan")},
            {"intensity": "0.2"},
            {"expression": "angry"},
            {"raw_curves": {"jawOpen": 1.0}},
            {"variation": 1.001},
            {"variation": float("inf")},
            {"blink_min_ms": 1799},
            {"blink_max_ms": 12001},
            {"blink_min_ms": 4000, "blink_max_ms": 4300},
            {"blink_duration_ms": 119},
            {"blink_duration_ms": True},
            {"gaze_amplitude": 0.151},
            {"gaze_amplitude": "0.1"},
            {"gaze_hold_min_ms": 999},
            {"gaze_hold_max_ms": 6501},
            {"gaze_hold_min_ms": 3000, "gaze_hold_max_ms": 3200},
            {"eye_contact": 0.64},
            {"warmth": 0.151},
            {"motion_min_ms": 2499},
            {"motion_max_ms": 16001},
            {"motion_min_ms": 6000, "motion_max_ms": 6200},
            {"head_motion_strength": -0.01},
            {"head_motion_strength": 1.01},
            {"head_motion_strength": float("nan")},
            {"head_motion_strength": "0.4"},
            {"head_motion_probability": -0.01},
            {"head_motion_probability": 1.01},
            {"head_motion_probability": True},
            {"head_motion_probability": float("inf")},
            {"audio_emphasis_strength": -0.01},
            {"audio_emphasis_strength": 1.01},
            {"audio_emphasis_strength": False},
            {"audio_emphasis_strength": "0.2"},
        ):
            payload = copy.deepcopy(VALID_PLAN)
            payload["states"]["idle"].update(change)
            invalid.append(payload)
        incomplete = copy.deepcopy(VALID_PLAN)
        del incomplete["states"]["idle"]["variation"]
        no_speaking_profile = copy.deepcopy(VALID_PLAN)
        del no_speaking_profile["states"]["speaking"]
        invalid.extend(
            [
                incomplete,
                no_speaking_profile,
                {"states": {"idle": dict(VALID_PROFILE)}},
                {**VALID_PLAN, "generation": 999},
                {**VALID_PLAN, "speaking": []},
                {**VALID_PLAN, "valid_ms": 2147483647},
            ]
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    ExpressionPlan.model_validate(payload)

    def test_legacy_profiles_default_motion_to_zero(self):
        """Old cached profiles remain valid.

        They acquire no head motion or audio emphasis by default.
        """
        legacy = copy.deepcopy(VALID_PLAN)
        optional_fields = (
            "head_motion_strength",
            "head_motion_probability",
            "audio_emphasis_strength",
        )
        for profile in legacy["states"].values():
            for field in optional_fields:
                del profile[field]
        normalized = ExpressionPlan.model_validate(legacy).model_dump()
        for state, profile in normalized["states"].items():
            for field in optional_fields:
                self.assertEqual(profile[field], 0.0)
            self.assertEqual(
                {key: value for key, value in profile.items() if key not in optional_fields},
                legacy["states"][state],
            )
        boundary = copy.deepcopy(legacy)
        for profile in boundary["states"].values():
            profile.update({field: 1.0 for field in optional_fields})
        self.assertEqual(ExpressionPlan.model_validate(boundary).model_dump(), boundary)


class PresentationServiceTests(SimpleTestCase):
    """Exercise production planning through SDK doubles; these tests never contact a model."""

    def setUp(self):
        """Clear ambient provider values so fixture credentials/model alone govern each test."""
        self.environment = patch.dict(os.environ, ENABLED_ENV, clear=True)
        self.environment.start()
        self.clock = Mock(return_value=10.0)
        self.service = PresentationService(clock=self.clock)

    def tearDown(self):
        """Restore process environment after service tests.

        Include independent Django settings.
        """
        self.environment.stop()

    def test_disabled_missing_configuration_and_timeout_bounds(self):
        """Disabled and malformed configuration selects recorded fallback.

        This happens before SDK construction.
        """
        for changes, reason in (
            ({"PRESENTATION_ENABLED": "false"}, "disabled"),
            ({"DASHSCOPE_API_KEY": ""}, "not_configured"),
            ({"PRESENTATION_PROVIDER": "unsupported"}, "not_configured"),
            ({"PRESENTATION_TIMEOUT_SECONDS": "0.24"}, "not_configured"),
            ({"PRESENTATION_TIMEOUT_SECONDS": "31"}, "not_configured"),
            ({"PRESENTATION_TIMEOUT_SECONDS": "nan"}, "not_configured"),
        ):
            with (
                self.subTest(changes=changes),
                patch.dict(os.environ, changes),
                patch("interviews.presentation.service.OpenAI") as sdk,
            ):
                result = self.service.build(request_fixture(), "fixture-owner")
                self.assertEqual((result["source"], result["reason"]), ("fallback", reason))
                self.assertNotIn("speaking", result)
                for profile in result["states"].values():
                    self.assertEqual(profile["intensity"], 0.0)
                    self.assertEqual(profile["variation"], 0.0)
                    self.assertEqual(profile["warmth"], 0.0)
                    self.assertEqual(profile["gaze_amplitude"], 0.0)
                    self.assertEqual(profile["head_motion_strength"], 0.0)
                    self.assertEqual(profile["head_motion_probability"], 0.0)
                    self.assertEqual(profile["audio_emphasis_strength"], 0.0)
                sdk.assert_not_called()
        with patch.dict(os.environ, {"PRESENTATION_TIMEOUT_SECONDS": "30"}):
            self.assertEqual(PresentationConfig.load().timeout_seconds, 30.0)

    def test_model_request_is_question_only_and_retry_free(self):
        """Provider input excludes IDs, answers and scoring while response identity stays local."""
        request = request_fixture(generation=4)
        with patch("interviews.presentation.service.OpenAI") as sdk:
            sdk.return_value.chat.completions.create.return_value = model_completion()
            result = self.service.build(request, "fixture-owner")
        self.assertEqual((result["source"], result["reason"]), ("model", None))
        self.assertEqual(result["generation"], 4)
        self.assertEqual(result["presentation_id"], request.presentation_id)
        self.assertEqual(result["question_id"], "q-fixture")
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["transition_ms"], 350)
        self.assertEqual(result["valid_ms"], 600000)
        self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)
        self.assertEqual(sdk.call_args.kwargs["timeout"], 25.0)
        options = sdk.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(options["extra_body"], {"enable_thinking": False})
        self.assertEqual(options["response_format"], {"type": "json_object"})
        self.assertEqual(options["max_completion_tokens"], 2200)
        data = json.loads(options["messages"][1]["content"])
        self.assertEqual(set(data), {"state_context", "question"})
        self.assertEqual(data["state_context"], "approved_question")
        self.assertEqual(
            set(data["question"]), {"text", "question_type", "intent", "dialogue_action"}
        )
        self.assertNotIn("q-fixture", options["messages"][1]["content"])
        sdk.return_value.chat.completions.create.assert_called_once()
        sdk.return_value.close.assert_called_once()

    def test_before_interview_profiles_without_question(self):
        """Null question bootstraps all four states without IDs, answers or business model work."""
        request = PresentationRequest.model_validate(
            {
                "version": 2,
                "presentation_id": str(uuid4()),
                "generation": 1,
                "question": None,
            }
        )
        with patch("interviews.presentation.service.OpenAI") as sdk:
            sdk.return_value.chat.completions.create.return_value = model_completion()
            result = self.service.build(request, "fixture-owner")
            replay = self.service.build(
                request.model_copy(update={"generation": 2}), "fixture-owner"
            )
        self.assertEqual(result["question_id"], "session-idle")
        self.assertEqual((result["source"], result["reason"]), ("model", None))
        self.assertEqual(result["states"], VALID_PLAN["states"])
        self.assertEqual(replay["generation"], 2)
        data = json.loads(
            sdk.return_value.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        )
        self.assertEqual(data, {"state_context": "before_interview", "question": None})
        sdk.return_value.chat.completions.create.assert_called_once()

    def test_openai_uses_its_own_credentials_and_no_dashscope_options(self):
        """The independent provider override selects its own key/model.

        Preserve the base URL.
        """
        changes = {
            "PRESENTATION_PROVIDER": "openai",
            "PRESENTATION_MODEL": "offline-expression",
            "OPENAI_API_KEY": "other-offline-key",
            "OPENAI_BASE_URL": "https://openai.invalid/v1",
        }
        with (
            patch.dict(os.environ, changes),
            patch("interviews.presentation.service.OpenAI") as sdk,
        ):
            sdk.return_value.chat.completions.create.return_value = model_completion()
            config = PresentationConfig.load()
            request_model_plan(config, request_fixture())
        self.assertEqual(sdk.call_args.kwargs["api_key"], "other-offline-key")
        self.assertEqual(sdk.call_args.kwargs["base_url"], "https://openai.invalid/v1")
        options = sdk.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(options["model"], "offline-expression")
        self.assertNotIn("extra_body", options)

    def test_invalid_incomplete_and_refused_output_falls_back(self):
        """Unsafe, truncated or refused output is not repaired or passed to the renderer."""
        cases = [
            model_completion("{not-json SECRET}"),
            model_completion({**VALID_PLAN, "raw_curves": {"jawOpen": 1}}),
            model_completion(finish_reason="length"),
            model_completion(refusal="SECRET"),
            SimpleNamespace(choices=[]),
        ]
        for completion in cases:
            with (
                self.subTest(completion=completion),
                patch("interviews.presentation.service.OpenAI") as sdk,
            ):
                sdk.return_value.chat.completions.create.return_value = completion
                result = PresentationService().build(request_fixture(), "fixture-owner")
                self.assertEqual(result["reason"], "invalid_output")
                self.assertNotIn("speaking", result)
                self.assertEqual(result["states"]["listening"]["intensity"], 0.0)
                self.assertNotIn("SECRET", json.dumps(result))
                sdk.return_value.chat.completions.create.assert_called_once()

    def test_transport_failure_is_sanitised_and_cached(self):
        """Failed question replays retain recorded fallback without retrying or exposing errors."""
        first = request_fixture()
        second = request_fixture(generation=2)
        with patch("interviews.presentation.service.OpenAI") as sdk:
            sdk.return_value.chat.completions.create.side_effect = TimeoutError("SECRET quota key")
            result = self.service.build(first, "fixture-owner")
            replay = self.service.build(second, "fixture-owner")
        self.assertEqual(result["reason"], "unavailable")
        self.assertEqual(replay["generation"], 2)
        self.assertEqual(replay["presentation_id"], second.presentation_id)
        self.assertNotIn("SECRET", json.dumps(result))
        sdk.return_value.chat.completions.create.assert_called_once()

    def test_provider_failures_classified_without_secret_logs(self):
        """Cache safe failure reasons and log only type/status/allowlisted code.

        Never retry calls.
        """
        request = httpx.Request("POST", "https://private-provider.invalid?key=SECRET_REQUEST_KEY")
        cases = [
            (APITimeoutError(request), "provider_timeout", None, None),
            (
                APIConnectionError(message="SECRET_NETWORK_MESSAGE", request=request),
                "provider_connection_error",
                None,
                None,
            ),
            (
                provider_status_error(400, "insufficient_quota"),
                "provider_quota_exhausted",
                400,
                "insufficient_quota",
            ),
            (
                provider_status_error(429, "insufficient_quota"),
                "provider_quota_exhausted",
                429,
                "insufficient_quota",
            ),
            (provider_status_error(401, "SECRET_UNKNOWN_CODE"), "provider_auth_error", 401, None),
            (provider_status_error(403), "provider_auth_error", 403, None),
            (provider_status_error(402), "provider_quota_exhausted", 402, None),
            (provider_status_error(429), "provider_rate_limited", 429, None),
            (
                provider_status_error(429, "Throttling.RateQuota"),
                "provider_rate_limited",
                429,
                "throttling.ratequota",
            ),
            (
                provider_status_error(404, "ModelNotFound"),
                "provider_model_unavailable",
                404,
                "modelnotfound",
            ),
            (
                provider_status_error(400, "unsupported_parameter"),
                "provider_invalid_request",
                400,
                "unsupported_parameter",
            ),
            (provider_status_error(422), "provider_invalid_request", 422, None),
            (provider_status_error(504), "provider_timeout", 504, None),
            (provider_status_error(500, "SECRET_UNKNOWN_CODE"), "unavailable", 500, None),
            (OSError("SECRET_LOCAL_IO_MESSAGE"), "unavailable", None, None),
        ]
        for error, reason, status, code in cases:
            with self.subTest(reason=reason, status=status, code=code):
                service = PresentationService(clock=self.clock)
                first = request_fixture()
                with patch(
                    "interviews.presentation.service.request_model_plan", side_effect=error
                ) as provider:
                    with self.assertLogs("interviews.presentation", level="WARNING") as captured:
                        result = service.build(first, "fixture-owner")
                        replay = service.build(request_fixture(generation=2), "fixture-owner")
                self.assertEqual((result["source"], result["reason"]), ("fallback", reason))
                self.assertEqual(replay["reason"], reason)
                self.assertEqual(replay["generation"], 2)
                provider.assert_called_once()
                self.assertEqual(len(captured.output), 1)
                diagnostic = captured.output[0]
                self.assertIn(f"reason={reason}", diagnostic)
                self.assertIn(f"type={type(error).__name__}", diagnostic)
                self.assertIn(f"status={status}", diagnostic)
                self.assertIn(f"code={code}", diagnostic)
                self.assertNotIn("SECRET", diagnostic)
                self.assertNotIn("private-provider", diagnostic)
                self.assertNotIn("SECRET", json.dumps(result))
                self.assertEqual(result["states"]["listening"]["intensity"], 0.0)

    def test_cache_recorrelates_scopes_expires_and_evicts(self):
        """Owner-scoped replay reuses a plan.

        Distinct owners never share, and TTL/LRU bounds hold.
        """
        self.service = PresentationService(ttl=5, max_items=2, clock=self.clock)
        with patch(
            "interviews.presentation.service.request_model_plan",
            return_value=ExpressionPlan.model_validate(VALID_PLAN),
        ) as provider:
            request = request_fixture()
            self.service.build(request, "first-owner")
            replay = self.service.build(request_fixture(generation=2), "first-owner")
            self.assertEqual(replay["generation"], 2)
            self.assertEqual(provider.call_count, 1)
            self.clock.return_value = 12
            self.service.build(request, "second-owner")
            self.assertEqual(provider.call_count, 2)
            self.clock.return_value = 13
            self.service.build(request_fixture(text="Different approved question."), "first-owner")
            self.assertEqual(len(self.service.cache), 2)
            self.clock.return_value = 14
            self.service.build(request, "first-owner")
            self.assertEqual(provider.call_count, 4)
            self.clock.return_value = 19
            self.service.build(request, "first-owner")
            self.assertEqual(provider.call_count, 5)

    def test_busy_and_start_interval_decline_without_provider_wait(self):
        """An occupied call slot or rapid new-question request returns busy without another call."""
        with patch(
            "interviews.presentation.service.request_model_plan",
            return_value=ExpressionPlan.model_validate(VALID_PLAN),
        ) as provider:
            self.service.admission.acquire()
            try:
                result = self.service.build(request_fixture(), "fixture-owner")
                self.assertEqual(result["reason"], "busy")
                provider.assert_not_called()
            finally:
                self.service.admission.release()
            self.service.build(request_fixture(), "fixture-owner")
            result = self.service.build(request_fixture(text="Another question."), "fixture-owner")
            self.assertEqual(result["reason"], "busy")
            self.assertEqual(provider.call_count, 1)


@override_settings(ROOT_URLCONF=__name__)
class PresentationHTTPTests(SimpleTestCase):
    """Exercise real HTTP size/schema checks and existing origin middleware without cloud access."""

    def test_valid_response_and_rejected_payloads(self):
        """Validate correlation and no-store response.

        Verify early malformed/oversized request rejection.
        """
        payload = request_fixture().model_dump()
        with patch.dict(os.environ, {"PRESENTATION_ENABLED": "false"}):
            response = self.client.post(
                "/api/presentation/plan/",
                payload,
                content_type="application/json",
            )
            before_interview = self.client.post(
                "/api/presentation/plan/",
                {**payload, "question": None},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["presentation_id"], payload["presentation_id"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(before_interview.status_code, 200)
        self.assertEqual(before_interview.json()["question_id"], "session-idle")
        self.assertEqual(before_interview.json()["source"], "fallback")
        self.assertEqual(
            set(before_interview.json()["states"]),
            {
                "idle",
                "listening",
                "thinking",
                "speaking",
            },
        )
        with patch("interviews.presentation.views.build_plan") as provider:
            for data, status in (
                ({**payload, "answer_text": "private answer"}, 400),
                ({**payload, "generation": "1"}, 400),
                ({"padding": "x" * 8193}, 413),
            ):
                with self.subTest(data=data):
                    response = self.client.post(
                        "/api/presentation/plan/",
                        data,
                        content_type="application/json",
                    )
                    self.assertEqual(response.status_code, status)
            provider.assert_not_called()

    def test_cross_origin_and_remote_clients_cannot_plan(self):
        """Reject remote clients and foreign browser origins before the presentation planner."""
        with patch("interviews.presentation.views.build_plan") as provider:
            for headers in (
                {"REMOTE_ADDR": "192.0.2.1"},
                {"HTTP_ORIGIN": "https://untrusted.invalid"},
            ):
                response = self.client.post(
                    "/api/presentation/plan/",
                    request_fixture().model_dump(),
                    content_type="application/json",
                    **headers,
                )
                self.assertEqual(response.status_code, 403)
            provider.assert_not_called()


@override_settings(ROOT_URLCONF=__name__, INTERVIEW_REQUIRE_LOGIN=True)
class PresentationAccountTests(TestCase):
    """Exercise login/session/CSRF order with the real page and an offline presentation service."""

    def test_account_and_csrf_checks_precede_planning(self):
        """Authentication and CSRF failures cannot bill the model.

        Accepted requests use owner keys.
        """
        client = Client(enforce_csrf_checks=True)
        payload = request_fixture().model_dump()
        with patch(
            "interviews.presentation.views.build_plan", return_value={"source": "fallback"}
        ) as provider:
            response = client.post(
                "/api/presentation/plan/",
                payload,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)
            user = get_user_model().objects.create_user(
                "presentation-fixture", password="offline-only"
            )
            client.force_login(user)
            response = client.post(
                "/api/presentation/plan/",
                payload,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 403)
            provider.assert_not_called()
            page = client.get("/agent/")
            self.assertEqual(page.status_code, 200)
            response = client.post(
                "/api/presentation/plan/",
                payload,
                content_type="application/json",
                HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
            )
            self.assertEqual(response.status_code, 200)
            provider.assert_called_once()
            self.assertEqual(provider.call_args.args[1], str(user.pk))
