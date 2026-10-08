"""Responsibilities: Verify real PDF extraction/rendering and simulated visual port, without sending
resumes to external models.
Implementation: Memory-construct dual-column, scanned, encrypted, and multi-page PDFs; check stream
phase, failure, and cancellation semantics.
Related Modules: resume_pdf, resume_api, resume_vision, and agents.resume_cleanup.

Declaration Index:
- make_pdf: Generate deterministic PDF with Helvetica dual-column text.
- collect: Consume asynchronous NDJSON generator into event list.
- ResumePdfTests: Local tests for rules and native rendering.
- ResumePdfTests.test_rules_and_render: Preserve original text, column layout, render bounded PNG.
- ResumePdfTests.test_scanned_page: Scanned images have no rule-based text but render visual
  evidence.
- ResumePdfTests.test_invalid_inputs: Damaged, encrypted, excessive pages, and over-limit all
  explicitly rejected.
- ResumePdfTests.test_conservative_normalization: Do not merge columns, guess word breaks or dates.
- ResumeFlowTests: Asynchronous HTTP, visual contract, and lifecycle testing.
- ResumeFlowTests.setUp: Explicitly simulate sandbox boundaries; original parsing algorithm verified
  in independent unit tests and sandbox integration.
- ResumeFlowTests.setUp.local_parse: For flow testing only, return existing algorithm’s pages; not
  part of production fallback path.
- ResumeFlowTests.test_no_changes_preserves_baseline: No modification suggestions preserve baseline
  extraction verbatim.
- ResumeFlowTests.test_precise_corrections: Replace only numbered line ranges; reject out-of-bound,
  reverse, overlapping, or unsupported changes.
- ResumeFlowTests.test_unchanged_suggestions_preserve_baseline: Same-text suggestions preserve
  original, do not impersonate actual
  edits.
- ResumeFlowTests.test_unchanged_suggestions_with_real_changes:
  Ignore same-text override range, still validate line numbers and reasons.
- ResumeFlowTests.test_line_ranges_preserve_unedited_repeated_text:
  Line numbers distinguish repeated text, preserve unselected lines, CRLF, and trailing no-newline.
- ResumeFlowTests.test_vision_order_and_close: Rules returned first, vision merged by page order and
  closed.
- ResumeFlowTests.test_failure_no_fallback: Vision failure has no successful terminal state, no
  original text leaked.
- ResumeFlowTests.test_failure_codes_are_specific_and_redacted:
  Failure and timeout return specific, limited codes; unknown exceptions do not leak original text.
- ResumeFlowTests.test_cancellation_closes_client: Cancellation releases visual connection.
- ResumeFlowTests.test_agent_rejects_mismatch_and_omission: Reject missing images, wrong page
  numbers, and non-empty page loss.
- ResumeFlowTests.test_http_contract: Real Django routing accepts multipart, rejects wrong methods
  and parameters.
- ResumeFlowTests.test_default_traditional_has_no_model_call:
  Default traditional extraction returns original baseline, no visual client constructed or called.
- ResumeProviderTests: Simulate SDK, verify real adapter configuration and multimodal requests.
- ResumeProviderTests.test_explicit_configuration: Do not use interview text model if dedicated
  model missing.
- ResumeProviderTests.test_multimodal_payload: Images and numbered rule lines actually enter
  request, SDK retry prohibited.
- ResumeProviderTests.test_invalid_output_obeys_retry_policy: Truncation fails immediately; invalid
  JSON exhausts bounded retries without a successful result.

- ControlledAgent: Control page completion or failure via events, not relying on time-based
  concurrency estimates.
- ControlledAgent.__init__: Establish page gate, start queue, and track in-flight count.
- ControlledAgent.clean_page: Record in-flight status, wait for test release, propagate failure and
  cancellation.
- ResumeConcurrencyTests: Verify bounded concurrency, out-of-order completion, and cleanup.
- ResumeConcurrencyTests.test_window_and_completion_order: Prove three pages overlap execution with
  window replenished upon completion.
- ResumeConcurrencyTests.test_failure_cancels_siblings: Failure stops replenishment and cancels
  other in-flight pages.
- ResumeConcurrencyTests.test_consumer_close_cancels_pending: Consumer closes after one output,
  releasing sibling tasks.

Variable Index:
None
"""

import asyncio
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import AsyncClient, SimpleTestCase
from httpx import ReadTimeout, Request
from openai import APITimeoutError
from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from agents.resume_cleanup import LineCorrection, PageReview, ResumeCleanupAgent, ResumePage
from interviews.resume_api import VISION_CONCURRENCY, resume_events, review_pages
from interviews.resume_pdf import (
    MAX_BYTES,
    PdfInputError,
    extract_pdf,
    normalize_text,
    render_pages,
)
from interviews.resume_vision import ResumeVision


def make_pdf(pages=1, encrypted=False):
    """Input page count and encryption flag, output in-memory PDF; two columns fixed, no real
    candidate data included.
    """
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(
            b"BT /F1 12 Tf 40 720 Td (Alex - Python) Tj 300 0 Td (Projects) Tj "
            b"-300 -20 Td (2024-2025) Tj 300 0 Td (Data pipeline) Tj ET"
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    output = BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


async def collect(stream):
    """Input asynchronous byte event stream, output decoded events; collect fully only in test, no
    external network access.
    """
    return [json.loads(line) async for line in stream]


class ResumePdfTests(SimpleTestCase):
    """Function: Cover real pypdf/PDFium boundaries; constraint: fully in-memory synthesis, no
    external services.
    """

    def test_rules_and_render(self):
        """Under dual-column premise, preserve two text columns and original text; PNG longest edge
        bounded and contains realistic content.
        """
        data = make_pdf(2)
        pages = extract_pdf(data)
        self.assertEqual([page.number for page in pages], [1, 2])
        self.assertIn("Alex - Python", pages[0].raw_text)
        self.assertIn("Projects", pages[0].text)
        self.assertIn("   ", pages[0].text)
        rendered = render_pages(data, pages)
        self.assertFalse(pages[0].image_png)
        with Image.open(BytesIO(rendered[0].image_png)) as image:
            self.assertLessEqual(max(image.size), 1800)
            self.assertNotEqual(image.convert("L").getextrema(), (255, 255))

    def test_scanned_page(self):
        """The image PDF contains no text layer; rule results retain empty pages and prompt
        visually, yet rendering still succeeds.
        """
        with Image.new("RGB", (600, 800), "white") as image:
            ImageDraw.Draw(image).text((50, 50), "Alex / Python / Project", fill="black")
            output = BytesIO()
            image.save(output, format="PDF")
        pages = extract_pdf(output.getvalue())
        self.assertFalse(pages[0].text.strip())
        self.assertIn("scanned", "".join(pages[0].warnings))
        self.assertTrue(render_pages(output.getvalue(), pages)[0].image_png)

    def test_invalid_inputs(self):
        """Boundary inputs are neither truncated nor attempted to be decrypted, nor converted into
        successful empty text.
        """
        for data in (
            b"",
            b"not pdf",
            b"%PDF-broken",
            make_pdf(11),
            make_pdf(encrypted=True),
            b"%PDF-" + b"0" * MAX_BYTES,
        ):
            with self.subTest(size=len(data)), self.assertRaises(PdfInputError):
                extract_pdf(data)

    def test_conservative_normalization(self):
        """Only explicitly formatted ligatures are repaired; indentation, multiple spaces, line
        breaks, and numerical values are preserved.
        """
        self.assertEqual(
            normalize_text("  \ufb01le\u00a0   2024-2025  \r\ndata-\r\nbase"),
            "  file    2024-2025\ndata-\nbase",
        )


class ResumeFlowTests(SimpleTestCase):
    """Function: Check asynchronous business flow and cancellation; Logic: Visual port proxy, does
    not validate actual model accuracy.
    """

    def setUp(self):
        """Explicitly simulate sandbox boundary; original parsing algorithm is verified in
        independent unit tests and sandbox integration.
        """

        async def local_parse(data):
            """For process testing only, return existing algorithm's pages; this is not a production
            fallback path; external services are not invoked.
            """
            return render_pages(data, extract_pdf(data))

        stub = patch("interviews.resume_api.parse_pdf", side_effect=local_parse)
        stub.start()
        self.addCleanup(stub.stop)

    async def test_no_changes_preserves_baseline(self):
        """Baseline with indentation, line breaks, and ambiguities remains unchanged
        character-by-character when the model does not propose modifications.
        """
        text = "  Alex    Python\n2024-2025  "
        port = SimpleNamespace(
            review=AsyncMock(
                return_value=PageReview(
                    number=1, corrections=[], uncertainties=["日期不清晰，保留原文"]
                )
            )
        )
        result = await ResumeCleanupAgent(port).clean_page(
            ResumePage(1, text, text, image_png=b"png")
        )
        self.assertEqual(result.text, text)
        self.assertFalse(result.changed)
        self.assertEqual(result.corrections, [])
        self.assertEqual(result.uncertainties, ["日期不清晰，保留原文"])

    async def test_precise_corrections(self):
        """Valid number range replaces only selected lines; out-of-range, reverse-order,
        overlapping, and unsupported patches are explicitly rejected.
        """
        port = SimpleNamespace(review=AsyncMock())
        page = ResumePage(1, "", "  Pyth0n / SQL / 30%\n  日期 2026\n", image_png=b"png")
        correction = LineCorrection(
            start_line=1, end_line=1, text="  Python / SQL / 30%", reason="图中为字母 o"
        )
        port.review.return_value = PageReview(number=1, corrections=[correction], uncertainties=[])
        result = await ResumeCleanupAgent(port).clean_page(page)
        self.assertEqual(result.text, "  Python / SQL / 30%\n  日期 2026\n")
        self.assertTrue(result.changed)
        self.assertEqual(result.corrections, [correction])
        for corrections in (
            [LineCorrection(start_line=3, end_line=3, text="x", reason="test")],
            [LineCorrection(start_line=2, end_line=1, text="x", reason="test")],
            [LineCorrection(start_line=1, end_line=1, text="x", reason=" ")],
            [correction, LineCorrection(start_line=1, end_line=2, text="x", reason="test")],
        ):
            port.review.return_value = PageReview(
                number=1, corrections=corrections, uncertainties=[]
            )
            with self.assertRaises(ValueError):
                await ResumeCleanupAgent(port).clean_page(page)
        port.review.return_value = PageReview(
            number=1,
            corrections=[
                LineCorrection(start_line=1, end_line=1, text="扫描文字", reason="图片可见")
            ],
            uncertainties=[],
        )
        result = await ResumeCleanupAgent(port).clean_page(ResumePage(1, "", "", image_png=b"png"))
        self.assertEqual(result.text, "扫描文字")
        # Two actual insertions occupy the same virtual blank line; they cannot bypass overlap
        # checks due to zero-length character ranges.
        port.review.return_value = PageReview(
            number=1,
            corrections=[
                LineCorrection(start_line=1, end_line=1, text="first", reason="test"),
                LineCorrection(start_line=1, end_line=1, text="second", reason="test"),
            ],
            uncertainties=[],
        )
        with self.assertRaisesRegex(ValueError, "overlapping"):
            await ResumeCleanupAgent(port).clean_page(ResumePage(1, "", "", image_png=b"png"))

    async def test_unchanged_suggestions_preserve_baseline(self):
        """Simulate visual same-text suggestion; real Agent retains
        characters/whitespace/ambiguities, changed is false, and patch is empty.
        """
        page = ResumePage(1, "", "  Python / SQL\n", image_png=b"png")
        port = SimpleNamespace(
            review=AsyncMock(
                return_value=PageReview(
                    number=1,
                    corrections=[
                        LineCorrection(start_line=1, end_line=1, text=page.text, reason="图片一致")
                    ],
                    uncertainties=["日期需核对"],
                )
            )
        )
        result = await ResumeCleanupAgent(port).clean_page(page)
        self.assertEqual(result.text, page.text)
        self.assertFalse(result.changed)
        self.assertEqual(result.corrections, [])
        self.assertEqual(result.uncertainties, ["日期需核对"])
        port.review.assert_awaited_once()

    async def test_unchanged_suggestions_with_real_changes(self):
        """Same-text intervals do not block real replacement; invalid line numbers and empty reasons
        are still rejected, model called only once.
        """
        page = ResumePage(1, "", " Pyth0n / SQL\n", image_png=b"png")
        correction = LineCorrection(
            start_line=1, end_line=1, text=" Python / SQL", reason="图中为字母 o"
        )
        unchanged = LineCorrection(start_line=1, end_line=1, text=page.text, reason="已核对")
        port = SimpleNamespace(
            review=AsyncMock(
                return_value=PageReview(
                    number=1,
                    corrections=[unchanged, correction],
                    uncertainties=[],
                )
            )
        )
        result = await ResumeCleanupAgent(port).clean_page(page)
        self.assertEqual(result.text, " Python / SQL\n")
        self.assertEqual(result.corrections, [correction])
        self.assertTrue(result.changed)
        port.review.assert_awaited_once()
        for invalid in (
            LineCorrection(start_line=2, end_line=2, text="missing", reason="test"),
            LineCorrection(start_line=1, end_line=1, text=page.text, reason=" "),
        ):
            port.review.return_value = PageReview(number=1, corrections=[invalid], uncertainties=[])
            with self.assertRaises(ValueError):
                await ResumeCleanupAgent(port).clean_page(page)

    async def test_line_ranges_preserve_unedited_repeated_text(self):
        """Repeated baselines do not require model repositioning of text; modify second line,
        preserve all characters in first and last lines.
        """
        page = ResumePage(1, "", "  SQL\r\n  SQL\r\nTAIL", image_png=b"png")
        port = SimpleNamespace(
            review=AsyncMock(
                return_value=PageReview(
                    number=1,
                    corrections=[
                        LineCorrection(
                            start_line=2, end_line=2, text="  Python", reason="图片依据"
                        ),
                    ],
                    uncertainties=[],
                )
            )
        )
        result = await ResumeCleanupAgent(port).clean_page(page)
        self.assertEqual(result.text, "  SQL\r\n  Python\r\nTAIL")
        # Model provides LF but still restores original CRLF at range end, preserving unselected
        # characters afterward.
        port.review.return_value = PageReview(
            number=1,
            corrections=[
                LineCorrection(start_line=1, end_line=2, text="SQL\nPython\n", reason="图片依据"),
            ],
            uncertainties=[],
        )
        result = await ResumeCleanupAgent(port).clean_page(page)
        self.assertEqual(result.text, "SQL\nPython\r\nTAIL")

    async def test_vision_order_and_close(self):
        """Original algorithm text and numbered line patches are merged by page; first progress
        precedes model construction, SDK is explicit proxy.
        """
        baseline = extract_pdf(make_pdf())[0].text
        lines = baseline.splitlines(keepends=True)
        target_line = next(i for i, line in enumerate(lines, 1) if "Data pipeline" in line)
        replacement = lines[target_line - 1].replace("Data pipeline", "Data pipeline test")
        provider = SimpleNamespace(
            review=AsyncMock(
                side_effect=[
                    PageReview(number=1, corrections=[], uncertainties=["check title"]),
                    PageReview(
                        number=2,
                        corrections=[
                            LineCorrection(
                                start_line=target_line,
                                end_line=target_line,
                                text=replacement,
                                reason="test image",
                            )
                        ],
                        uncertainties=[],
                    ),
                ]
            ),
            close=AsyncMock(),
        )
        with patch("interviews.resume_api.ResumeVision", return_value=provider) as constructor:
            stream = resume_events(make_pdf(2), mode="advanced")
            self.assertEqual(json.loads(await anext(stream))["type"], "progress")
            constructor.assert_not_called()
            events = await collect(stream)
        self.assertEqual(
            events[-1]["text"],
            baseline + "\n\n" + baseline.replace("Data pipeline", "Data pipeline test"),
        )
        self.assertEqual(events[-1]["changed_pages"], 1)
        self.assertNotIn("corrections", json.dumps(events))
        self.assertEqual(events[-1]["pages"][0]["uncertainties"], ["check title"])
        self.assertEqual(provider.review.await_count, 2)
        provider.close.assert_awaited_once()

    async def test_failure_no_fallback(self):
        """Simulate vendor exception containing sensitive text; response displays fixed error only
        and does not send success final state.
        """
        provider = SimpleNamespace(
            review=AsyncMock(side_effect=RuntimeError("private-resume")), close=AsyncMock()
        )
        with patch("interviews.resume_api.ResumeVision", return_value=provider):
            events = await collect(resume_events(make_pdf(), mode="advanced"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["stage"], "vision")
        self.assertNotIn("result", [event["type"] for event in events])
        self.assertNotIn("private-resume", json.dumps(events))

    async def test_failure_codes_are_specific_and_redacted(self):
        """Real process pairs with explicit visual proxy; verify positioning failure/timeout prompts
        and limited error codes; original text and any exception messages are not disclosed.
        """
        timeout = APITimeoutError(request=Request("POST", "https://example.test"))
        timeout.__cause__ = ReadTimeout("private-network-cause")
        for error, expected in [
            (ValueError("resume_correction_range_invalid"), "resume_correction_range_invalid"),
            (TimeoutError("private-timeout"), "timeout"),
            (timeout, "timeout"),
            (ValueError("private-resume-secret"), "model_error"),
        ]:
            provider = SimpleNamespace(review=AsyncMock(side_effect=error), close=AsyncMock())
            with patch("interviews.resume_api.ResumeVision", return_value=provider):
                events = await collect(resume_events(make_pdf(), mode="advanced"))
            self.assertEqual(events[-1]["type"], "error")
            self.assertEqual(events[-1]["code"], expected)
            self.assertNotIn("private-", json.dumps(events))
            self.assertFalse(any(event["type"] == "result" for event in events))
            provider.review.assert_awaited_once()
            provider.close.assert_awaited_once()
        provider.close.assert_awaited_once()

    async def test_cancellation_closes_client(self):
        """When visual call is cancelled, do not return error as substitute for cancellation; cancel
        ongoing tasks in the same window and release client.
        """
        provider = SimpleNamespace(
            review=AsyncMock(side_effect=asyncio.CancelledError), close=AsyncMock()
        )
        with patch("interviews.resume_api.ResumeVision", return_value=provider):
            with self.assertRaises(asyncio.CancelledError):
                await collect(resume_events(make_pdf(2), mode="advanced"))
        self.assertEqual(provider.review.await_count, 2)
        provider.close.assert_awaited_once()

    async def test_agent_rejects_mismatch_and_omission(self):
        """Input and model output page identities must not mismatch; non-empty rule pages must not
        be omitted without explanation.
        """
        port = SimpleNamespace(review=AsyncMock())
        agent = ResumeCleanupAgent(port)
        with self.assertRaisesRegex(ValueError, "image_required"):
            await agent.clean_page(ResumePage(1, "x", "x"))
        port.review.assert_not_awaited()
        for output in (
            PageReview(number=2, corrections=[], uncertainties=[]),
            PageReview(
                number=1,
                corrections=[LineCorrection(start_line=1, end_line=1, text="", reason="test")],
                uncertainties=[],
            ),
        ):
            port.review.return_value = output
            with self.assertRaises(ValueError):
                await agent.clean_page(ResumePage(1, "x", "x", image_png=b"png"))

    async def test_http_contract(self):
        """Real Django routing pairs with visual proxy; verify rejection of illegal patterns,
        explicit advanced calls, and stream returns.
        """
        client = AsyncClient(headers={"host": "127.0.0.1"})
        response = await client.get("/api/resume/parse/")
        self.assertEqual(response.status_code, 405)
        response = await client.post("/api/resume/parse/", {"mode": "oops"})
        self.assertEqual(response.status_code, 400)
        response = await client.post(
            "/api/resume/parse/",
            {
                "file": SimpleUploadedFile(
                    "resume.pdf", make_pdf(), content_type="application/pdf"
                ),
                "mode": "advanced",
            },
        )
        self.assertEqual(response.status_code, 200)
        provider = SimpleNamespace(
            review=AsyncMock(return_value=PageReview(number=1, corrections=[], uncertainties=[])),
            close=AsyncMock(),
        )
        with patch("interviews.resume_api.ResumeVision", return_value=provider):
            events = await collect(response.streaming_content)
        provider.review.assert_awaited_once()
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(response["Cache-Control"], "no-store")

    async def test_default_traditional_has_no_model_call(self):
        """Real routing with original rule algorithm and sandbox proxy; default mode does not create
        visual client, returns rule text and risk warning.
        """
        client = AsyncClient(headers={"host": "127.0.0.1"})
        with patch("interviews.resume_api.ResumeVision") as constructor:
            response = await client.post(
                "/api/resume/parse/",
                {
                    "file": SimpleUploadedFile(
                        "resume.pdf", make_pdf(), content_type="application/pdf"
                    )
                },
            )
            events = await collect(response.streaming_content)
        constructor.assert_not_called()
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(events[-1]["mode"], "traditional")
        self.assertEqual(events[-1]["text"], extract_pdf(make_pdf())[0].text)
        self.assertTrue(events[-1]["pages"][0]["uncertainties"])
        self.assertFalse(any(event.get("stage") == "vision" for event in events))


class ResumeProviderTests(SimpleTestCase):
    """Function: Verify SDK boundary; Constraint: All model requests are handled by AsyncMock,
    cannot prove vendor availability.
    """

    def test_explicit_configuration(self):
        """Interview model, even if present, cannot implicitly assume responsibility for image
        input.
        """
        with patch.dict("os.environ", {"DASHSCOPE_MODEL": "qwen-plus"}, clear=True):
            with self.assertRaises(ValueError):
                ResumeVision()

    async def test_multimodal_payload(self):
        """Actual adapter encodes PNG as image type rather than plain text; send only once and close
        connection.
        """
        sdk = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(
                        return_value=SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    finish_reason="stop",
                                    message=SimpleNamespace(
                                        refusal=None,
                                        content='{"number":1,"corrections":[],"uncertainties":[]}',
                                    ),
                                )
                            ]
                        )
                    )
                )
            ),
            close=AsyncMock(),
        )
        with (
            patch.dict(
                "os.environ",
                {
                    "RESUME_VISION_PROVIDER": "dashscope",
                    "RESUME_VISION_MODEL": "test-vision",
                    "DASHSCOPE_API_KEY": "test-key",
                    "RESUME_VISION_ENABLE_THINKING": "false",
                },
                clear=True,
            ),
            patch("interviews.resume_vision.AsyncOpenAI", return_value=sdk) as constructor,
        ):
            provider = ResumeVision()
            result = await provider.review(
                ResumePage(1, "raw", "  rule\r\nnext", image_png=b"png"), "system"
            )
            await provider.close()
        self.assertEqual(result.corrections, [])
        self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)
        payload = sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(payload["messages"][1]["content"][1]["type"], "image_url")
        self.assertTrue(
            payload["messages"][1]["content"][1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )
        numbered = json.loads(payload["messages"][1]["content"][0]["text"])
        self.assertEqual(
            numbered["rule_lines"], [{"line": 1, "text": "  rule"}, {"line": 2, "text": "next"}]
        )
        self.assertNotIn("rule_text", numbered)
        self.assertEqual(payload["extra_body"], {"enable_thinking": False})
        sdk.close.assert_awaited_once()

    async def test_invalid_output_obeys_retry_policy(self):
        """Functionality: Verify failed SDK outputs obey the distinct retry boundaries.
        Inputs: Mocked truncation or malformed JSON with default JSON retry settings.
        Outputs: None; asserts explicit failure and exact request count.
        Logic: Truncation fails once; completed invalid JSON is regenerated twice before failing.
        Constraints: No external model calls and no partial output may become successful.
        """
        for finish_reason, content in (("length", "{}"), ("stop", "not json")):
            sdk = SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=AsyncMock(
                            return_value=SimpleNamespace(
                                choices=[
                                    SimpleNamespace(
                                        finish_reason=finish_reason,
                                        message=SimpleNamespace(refusal=None, content=content),
                                    )
                                ]
                            )
                        )
                    )
                ),
                close=AsyncMock(),
            )
            with (
                patch.dict(
                    "os.environ",
                    {
                        "RESUME_VISION_PROVIDER": "openai",
                        "RESUME_VISION_MODEL": "test-vision",
                        "OPENAI_API_KEY": "test-key",
                    },
                    clear=True,
                ),
                patch("interviews.resume_vision.AsyncOpenAI", return_value=sdk),
            ):
                provider = ResumeVision()
                with self.assertRaises(ValueError):
                    await provider.review(
                        ResumePage(1, "raw", "  rule\r\nnext", image_png=b"png"), "system"
                    )
                await provider.close()
            self.assertEqual(
                sdk.chat.completions.create.await_count, 1 if finish_reason == "length" else 3
            )


class ControlledAgent:
    """Function: Simulate controlled page proofreading; Logic: Events determine completion time;
    Constraint: No real model or sleep involved.
    """

    def __init__(self, failure=None):
        """Input optionally includes failed page number; establish four-page gate, started queue,
        active/peak, and cancelled records.
        """
        self.failure = failure
        self.gates = {number: asyncio.Event() for number in range(1, 5)}
        self.started = asyncio.Queue()
        self.active = 0
        self.peak = 0
        self.cancelled = set()

    async def clean_page(self, page):
        """Input page object, return page number after test gate opens; record peak, reduce
        in-flight count on failure/cancellation.
        """
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.put_nowait(page.number)
        try:
            await self.gates[page.number].wait()
            if page.number == self.failure:
                raise ValueError("test page failed")
            return SimpleNamespace(number=page.number)
        except asyncio.CancelledError:
            self.cancelled.add(page.number)
            raise
        finally:
            self.active -= 1


class ResumeConcurrencyTests(SimpleTestCase):
    """Function: Verify scheduling boundary; Logic: Real asyncio task with event proxy; Constraint:
    Does not prove model speed.
    """

    async def test_window_and_completion_order(self):
        """Four-page input starts three pages first; fourth page added only after third completes;
        return actual completion order.
        """
        agent = ControlledAgent()
        pages = [SimpleNamespace(number=number) for number in range(1, 5)]
        stream = review_pages(agent, pages)
        try:
            first = asyncio.create_task(anext(stream))
            starts = [await asyncio.wait_for(agent.started.get(), 2) for _ in range(3)]
            self.assertEqual(starts, [1, 2, 3])
            self.assertEqual(agent.active, VISION_CONCURRENCY)
            agent.gates[3].set()
            self.assertEqual((await asyncio.wait_for(first, 2)).number, 3)
            second = asyncio.create_task(anext(stream))
            self.assertEqual(await asyncio.wait_for(agent.started.get(), 2), 4)
            agent.gates[4].set()
            self.assertEqual((await asyncio.wait_for(second, 2)).number, 4)
            for number in (2, 1):
                agent.gates[number].set()
                self.assertEqual((await asyncio.wait_for(anext(stream), 2)).number, number)
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
            self.assertEqual(agent.peak, 3)
            self.assertEqual(agent.active, 0)
        finally:
            await stream.aclose()

    async def test_failure_cancels_siblings(self):
        """After first item fails, second and third must be cancelled; fourth item, not yet in
        window, must not start.
        """
        agent = ControlledAgent(failure=1)
        stream = review_pages(agent, [SimpleNamespace(number=n) for n in range(1, 5)])
        first = asyncio.create_task(anext(stream))
        for _ in range(3):
            await asyncio.wait_for(agent.started.get(), 2)
        agent.gates[1].set()
        with self.assertRaisesRegex(ValueError, "test page failed"):
            await asyncio.wait_for(first, 2)
        self.assertEqual(agent.cancelled, {2, 3})
        self.assertTrue(agent.started.empty())
        self.assertEqual(agent.active, 0)

    async def test_consumer_close_cancels_pending(self):
        """Simulate outer layer receiving one page then disconnecting stream; aclose must cancel
        other in-flight pages and prevent fourth page from starting.
        """
        agent = ControlledAgent()
        stream = review_pages(agent, [SimpleNamespace(number=n) for n in range(1, 5)])
        first = asyncio.create_task(anext(stream))
        for _ in range(3):
            await asyncio.wait_for(agent.started.get(), 2)
        agent.gates[1].set()
        await asyncio.wait_for(first, 2)
        await stream.aclose()
        self.assertEqual(agent.cancelled, {2, 3})
        self.assertEqual(agent.active, 0)
        self.assertTrue(agent.started.empty())
