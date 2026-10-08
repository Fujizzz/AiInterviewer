"""Responsibilities: Verify schema regeneration, redacted feedback and resume stream outcomes.
Implementation: Exercise the real vision adapter/Agent/stream with mocked SDK and PDF boundaries.
Related Modules: interviews.resume_vision, interviews.resume_api, agents.resume_cleanup.
Declaration Index:
- completion: Construct one synthetic Chat Completions response.
- ResumeVisionRetryTests: Cover retry recovery, exhaustion, cancellation and strictness.
- ResumeVisionRetryTests.setUp: Isolate environment and SDK using a deterministic synthetic page.
- ResumeVisionRetryTests.test_schema_failures_recover: Regenerate malformed or schema-invalid JSON.
- ResumeVisionRetryTests.test_empty_rule_text_requests_transcription: Request missing text from PNG.
- ResumeVisionRetryTests.test_feedback_is_bounded_and_redacted: Keep only latest safe diagnostics.
- ResumeVisionRetryTests.test_exhaustion_preserves_last_error: Re-raise the final validation error.
- ResumeVisionRetryTests.test_retry_configuration: Check explicit bounds before SDK creation.
- ResumeVisionRetryTests.test_zero_retries: Allow an explicit single-request policy.
- ResumeVisionRetryTests.test_non_schema_failures_are_not_retried: Preserve terminal error handling.
- ResumeVisionRetryTests.test_cancellation_during_retry: Cancel an in-flight second request.
- ResumeVisionRetryTests.test_cancellation_during_retry.wait_for_cancel: Gate a mocked SDK request.
- ResumeVisionRetryTests.test_semantic_checks_still_apply: Reject unsafe patches after recovery.
- ResumeVisionRetryTests.test_stream_recovers_or_fails_atomically: Verify final stream outcomes.
- ResumeVisionRetryTests.test_concurrent_feedback_is_page_local: Isolate two pages sharing a client.
- ResumeVisionRetryTests.test_concurrent_feedback_is_page_local.respond: Gate per-page responses.
Variable Index:
None
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase
from httpx import Request, Response
from openai import APITimeoutError, BadRequestError
from pydantic import ValidationError

from agents.resume_cleanup import ResumeCleanupAgent, ResumePage
from interviews.resume_api import resume_events, resume_failure_code
from interviews.resume_vision import ResumeVision


def completion(content, *, finish_reason="stop", refusal=None):
    """Functionality: Build a minimal SDK response with synthetic content and token counts.
    Inputs: JSON/string content, finish reason and optional refusal.
    Outputs: A response-shaped SimpleNamespace; no SDK or network execution.
    Logic: Populate the fields read by the production adapter.
    Constraints: This fixture cannot verify provider availability or real token usage.
    """
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, refusal=refusal),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


class ResumeVisionRetryTests(SimpleTestCase):
    """Functionality: Verify retry behavior through real local validation and patch application.
    Logic: Mock only external model/PDF work and control asynchronous interleavings using events.
    Constraints: Tests do not assess live OCR accuracy, external availability or database writes.
    """

    def setUp(self):
        """Functionality: Install isolated model configuration and deterministic SDK responses.
        Inputs: Fresh test instance; environment is replaced for each test.
        Outputs: Sets page, valid_json, create, sdk, constructor and provider fixtures.
        Logic: Register patch cleanup before constructing a provider with a mocked SDK client.
        Constraints: All credentials/content are synthetic and no real network client is created.
        """
        env = patch.dict(
            "os.environ",
            {
                "RESUME_VISION_PROVIDER": "dashscope",
                "RESUME_VISION_MODEL": "test-vision",
                "RESUME_VISION_ENABLE_THINKING": "false",
                "DASHSCOPE_API_KEY": "synthetic-key",
            },
            clear=True,
        )
        env.start()
        self.addCleanup(env.stop)
        self.page = ResumePage(1, "raw", "Original\r\nKeep", image_png=b"synthetic-png")
        self.valid_json = json.dumps(
            {
                "number": 1,
                "corrections": [
                    {
                        "start_line": 1,
                        "end_line": 1,
                        "text": "Corrected",
                        "reason": "Visible text",
                    }
                ],
                "uncertainties": [],
            }
        )
        self.create = AsyncMock(return_value=completion(self.valid_json))
        self.sdk = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)),
            close=AsyncMock(),
        )
        sdk_patch = patch("interviews.resume_vision.AsyncOpenAI", return_value=self.sdk)
        self.constructor = sdk_patch.start()
        self.addCleanup(sdk_patch.stop)
        self.provider = ResumeVision()

    async def test_schema_failures_recover(self):
        """Functionality: Verify malformed JSON and strict schema errors can recover.
        Inputs: Mocked invalid responses followed by a complete valid correction.
        Outputs: Assertions on exact text, requests, preserved evidence and feedback.
        Logic: Run the real Agent for each syntax/type/required-field failure.
        Constraints: No coercion or parser repair; successful text must come from the second call.
        """
        invalid_outputs = [
            "not json",
            None,
            "",
            "null",
            "[]",
            "```json\n{}\n```",
            "{}",
            '{"number":"1","corrections":[],"uncertainties":[]}',
            '{"number":1,"corrections":null,"uncertainties":[]}',
            '{"number":1,"corrections":[],"uncertainties":[{"text":"check"}]}',
            '{"number":1,"corrections":[{"start_line":1}],"uncertainties":[]}',
        ]
        for invalid in invalid_outputs:
            with self.subTest(invalid=invalid):
                self.create.reset_mock()
                self.create.side_effect = [completion(invalid), completion(self.valid_json)]
                result = await ResumeCleanupAgent(self.provider).clean_page(self.page)
                self.assertEqual(result.text, "Corrected\r\nKeep")
                self.assertTrue(result.changed)
                self.assertEqual(self.create.await_count, 2)
                first, retry = [call.kwargs for call in self.create.await_args_list]
                self.assertEqual(first["messages"], retry["messages"][:2])
                for key in ("model", "response_format", "extra_body"):
                    self.assertEqual(first[key], retry[key])
                self.assertTrue(json.loads(retry["messages"][-1]["content"])["validation_feedback"])
                self.assertEqual(self.constructor.call_args.kwargs["max_retries"], 0)

    async def test_empty_rule_text_requests_transcription(self):
        """Functionality: Give empty rule text explicit transcription guidance and valid schema.
        Inputs: Mocked responses and synthetic empty or whitespace-only source text on page seven.
        Outputs: Assertions on page-specific examples, first-line bounds and unchanged evidence.
        Logic: Inspect the actual SDK payload for a correction example when rule text is absent.
        Constraints: This checks prompt selection, not whether a real image is blank or readable.
        """
        for text in ("", "  \n"):
            with self.subTest(text=text):
                page = ResumePage(7, text, text, image_png=b"synthetic-png")
                self.create.return_value = completion(
                    '{"number":7,"corrections":[],"uncertainties":[]}'
                )
                await self.provider.review(page, "system")
                messages = self.create.await_args.kwargs["messages"]
                system = messages[0]["content"]
                self.assertIn("规则文本为空，这不代表图片空白", system)
                example = json.loads(system.split("\nJSON schema:\n")[0].splitlines()[-1])
                self.assertEqual(example["number"], 7)
                self.assertEqual(example["corrections"][0]["start_line"], 1)
                self.assertEqual(example["corrections"][0]["end_line"], 1)
                evidence = json.loads(messages[1]["content"][0]["text"])
                self.assertEqual(evidence["rule_lines"][0]["text"], text.rstrip("\n"))

    async def test_feedback_is_bounded_and_redacted(self):
        """Functionality: Prevent untrusted field names, values and prior errors from leaking.
        Inputs: Two distinct invalid outputs containing synthetic private markers, then success.
        Outputs: Assertions on feedback length, safe schema locations and captured log content.
        Logic: Unknown keys are masked and only the latest eight-or-fewer issues are retained.
        Constraints: Original page evidence is intentionally present only in the base user message.
        """
        first_bad = {
            "number": 1,
            "corrections": [],
            "uncertainties": [],
            "PRIVATE_FIELD": "PRIVATE_RESPONSE",
        }
        second_bad = {"number": "PRIVATE_NUMBER", "corrections": [], "uncertainties": []}
        self.create.side_effect = [
            completion(json.dumps(first_bad)),
            completion(json.dumps(second_bad)),
            completion(self.valid_json),
        ]
        with self.assertLogs("interviews.resume_vision", level="INFO") as logs:
            await self.provider.review(self.page, "system")
        calls = self.create.await_args_list
        self.assertEqual([len(call.kwargs["messages"]) for call in calls], [2, 3, 3])
        feedback = [json.loads(call.kwargs["messages"][-1]["content"]) for call in calls[1:]]
        self.assertEqual(feedback[0]["validation_feedback"], ["*:extra_forbidden"])
        self.assertEqual(feedback[1]["validation_feedback"], ["number:int_type"])
        diagnostic_text = json.dumps(feedback) + "\n".join(logs.output)
        for marker in (
            "PRIVATE_FIELD",
            "PRIVATE_RESPONSE",
            "PRIVATE_NUMBER",
            "synthetic-key",
            self.page.text,
            "Corrected",
        ):
            self.assertNotIn(marker, diagnostic_text)
        self.assertIn("max_attempts=3", diagnostic_text)
        self.assertIn("attempt=3", diagnostic_text)

    async def test_exhaustion_preserves_last_error(self):
        """Functionality: Fail after the configured request budget without accepting bad types.
        Inputs: Three schema-invalid SDK responses, with a distinct final integer type error.
        Outputs: The propagated final ValidationError and stable public invalid_json code.
        Logic: Assert exactly three attempts and the final error's field/type rather than message.
        Constraints: No partial result or raw exception contents enter the public error contract.
        """
        self.create.side_effect = [
            completion("not json"),
            completion("{}"),
            completion('{"number":"1","corrections":[],"uncertainties":[]}'),
        ]
        with self.assertRaises(ValidationError) as caught:
            await self.provider.review(self.page, "system")
        self.assertEqual(self.create.await_count, 3)
        self.assertEqual(caught.exception.errors()[0]["loc"], ("number",))
        self.assertEqual(caught.exception.errors()[0]["type"], "int_type")
        self.assertEqual(resume_failure_code(caught.exception), "invalid_json")

    def test_retry_configuration(self):
        """Functionality: Validate retry bounds before creating an SDK client.
        Inputs: Explicit valid and invalid environment strings.
        Outputs: Assertions on stored counts, configuration failures and constructor calls.
        Logic: Accept only integers 0..5; invalid strings must not silently use the default.
        Constraints: Environment patches and mocked clients cannot affect live configuration.
        """
        for value in ("", "-1", "6", "1.5", "abc", " 2", "true"):
            with (
                self.subTest(value=value),
                patch.dict("os.environ", {"RESUME_VISION_JSON_RETRIES": value}),
            ):
                self.constructor.reset_mock()
                with self.assertRaisesRegex(ValueError, "RESUME_VISION_JSON_RETRIES"):
                    ResumeVision()
                self.constructor.assert_not_called()
        for value in ("0", "1", "5"):
            with patch.dict("os.environ", {"RESUME_VISION_JSON_RETRIES": value}):
                self.assertEqual(ResumeVision().json_retries, int(value))

    async def test_zero_retries(self):
        """Functionality: Respect explicit disabling of JSON retries.
        Inputs: Retry count zero and invalid JSON from the mocked SDK.
        Outputs: ValidationError after exactly one request.
        Logic: Construct with the explicit environment override and run real validation.
        Constraints: No partial parsing or replacement result is permitted.
        """
        with patch.dict("os.environ", {"RESUME_VISION_JSON_RETRIES": "0"}):
            provider = ResumeVision()
        self.create.return_value = completion("not json")
        with self.assertRaises(ValidationError):
            await provider.review(self.page, "system")
        self.create.assert_awaited_once()

    async def test_non_schema_failures_are_not_retried(self):
        """Functionality: Preserve refusal, truncation and transport failure semantics.
        Inputs: Mocked terminal outputs and provider timeout/HTTP errors.
        Outputs: Original exception identity where applicable and exactly one awaited request.
        Logic: These conditions occur outside the local schema-error retry block.
        Constraints: Provider calls are mocked; the test cannot prove actual cancellation upstream.
        """
        request = Request("POST", "https://example.invalid/chat/completions")
        failures = [
            completion("{}", finish_reason="length"),
            completion("{}", finish_reason="content_filter"),
            completion(self.valid_json, refusal="refused"),
            SimpleNamespace(choices=[]),
            APITimeoutError(request=request),
            BadRequestError("synthetic error", response=Response(400, request=request), body=None),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.create.reset_mock()
                self.create.side_effect = [failure]
                with self.assertRaises((ValueError, APITimeoutError, BadRequestError)) as caught:
                    await self.provider.review(self.page, "system")
                self.create.assert_awaited_once()
                if isinstance(failure, Exception):
                    self.assertIs(caught.exception, failure)

    async def test_cancellation_during_retry(self):
        """Functionality: Interrupt regeneration without starting another request.
        Inputs: First invalid result and a second mocked call blocked by an asyncio event.
        Outputs: CancelledError, exactly two calls and no further attempt.
        Logic: Wait for second-request entry before cancelling the review task.
        Constraints: Event waits are bounded; always cancel/gather the task on assertion failure.
        """
        retry_started = asyncio.Event()

        async def wait_for_cancel(**kwargs):
            """Functionality: Return invalid JSON once, then block the retry for cancellation.
            Inputs: SDK keyword arguments and the enclosing mock's current await count.
            Outputs: First synthetic response; second call propagates cancellation.
            Logic: Signal the event before waiting forever on a fresh event.
            Constraints: The enclosing test always cancels and gathers the waiting task.
            """
            if self.create.await_count == 1:
                return completion("{}")
            retry_started.set()
            await asyncio.Event().wait()

        self.create.side_effect = wait_for_cancel
        task = asyncio.create_task(self.provider.review(self.page, "system"))
        try:
            await asyncio.wait_for(retry_started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.create.await_count, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_semantic_checks_still_apply(self):
        """Functionality: Keep original page and patch safeguards after schema recovery.
        Inputs: Invalid JSON followed by schema-valid but semantically invalid corrections.
        Outputs: Existing page/range errors from the real Agent after two requests.
        Logic: Check wrong page number and out-of-range replacement separately.
        Constraints: JSON success cannot bypass semantic validation or silently preserve bad output.
        """
        for payload, code in [
            ({"number": 2, "corrections": [], "uncertainties": []}, "resume_page_mismatch"),
            (
                {
                    "number": 1,
                    "corrections": [
                        {"start_line": 99, "end_line": 99, "text": "x", "reason": "visible"}
                    ],
                    "uncertainties": [],
                },
                "resume_correction_range_invalid",
            ),
        ]:
            with self.subTest(code=code):
                self.create.reset_mock()
                self.create.side_effect = [completion("{}"), completion(json.dumps(payload))]
                with self.assertRaisesRegex(ValueError, code):
                    await ResumeCleanupAgent(self.provider).clean_page(self.page)
                self.assertEqual(self.create.await_count, 2)

    async def test_stream_recovers_or_fails_atomically(self):
        """Functionality: Return final text only when schema recovery succeeds.
        Inputs: One synthetic page from the mocked PDF parser and failing/recovering SDK outputs.
        Outputs: Public result/error events, stable error code and closed client.
        Logic: Consume the real resume_events pipeline for success and exhaustion.
        Constraints: This checks stream atomicity, not database persistence or PDF parsing.
        """
        for recover in (True, False):
            with self.subTest(recover=recover):
                self.create.reset_mock()
                self.sdk.close.reset_mock()
                self.create.side_effect = (
                    [completion("{}"), completion(self.valid_json)]
                    if recover
                    else [completion("{}") for _ in range(3)]
                )
                with patch("interviews.resume_api.parse_pdf", AsyncMock(return_value=[self.page])):
                    events = [
                        json.loads(line) async for line in resume_events(b"pdf", mode="advanced")
                    ]
                terminal = events[-1]
                if recover:
                    self.assertEqual(terminal["type"], "result")
                    self.assertEqual(terminal["text"], "Corrected\r\nKeep")
                    self.assertNotIn("error", [event["type"] for event in events])
                else:
                    self.assertEqual(terminal["type"], "error")
                    self.assertEqual(terminal["code"], "invalid_json")
                    self.assertFalse(any(event["type"] in {"page", "result"} for event in events))
                self.sdk.close.assert_awaited_once()

    async def test_concurrent_feedback_is_page_local(self):
        """Functionality: Prevent feedback from one page affecting a concurrently reviewed page.
        Inputs: Two pages sharing a client, with one page initially failing schema validation.
        Outputs: Correct page results, isolated message lists and three total requests.
        Logic: Hold the first page until the second starts, then regenerate only the failed page.
        Constraints: Synthetic responses and event gating avoid external timing assumptions.
        """
        second_started = asyncio.Event()
        counts = {1: 0, 2: 0}

        async def respond(**kwargs):
            """Functionality: Model two overlapping pages with a single page-local schema failure.
            Inputs: SDK messages, enclosing per-page counters and synchronization event.
            Outputs: Invalid first-page JSON once; valid no-change reviews thereafter.
            Logic: Read the input page number, track its attempts and gate initial page one.
            Constraints: Wait is bounded and only page one's retry may contain feedback.
            """
            messages = kwargs["messages"]
            number = json.loads(messages[1]["content"][0]["text"])["number"]
            counts[number] += 1
            if number == 1 and counts[number] == 1:
                await asyncio.wait_for(second_started.wait(), 2)
                return completion("{}")
            if number == 2:
                second_started.set()
                self.assertEqual(len(messages), 2)
            else:
                self.assertEqual(len(messages), 3)
            return completion(
                json.dumps({"number": number, "corrections": [], "uncertainties": []})
            )

        self.create.side_effect = respond
        results = await asyncio.gather(
            self.provider.review(self.page, "system"),
            self.provider.review(ResumePage(2, "", "", image_png=b"page-2"), "system"),
        )
        self.assertEqual([result.number for result in results], [1, 2])
        self.assertEqual(counts, {1: 2, 2: 1})
