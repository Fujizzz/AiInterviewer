"""职责：验证真实 PDF 提取/渲染及模拟视觉端口，不向外部模型发送简历。
实现：内存构造双栏、扫描、加密与多页 PDF；检查阶段流、失败和取消的语义。
关联：resume_pdf、resume_api、resume_vision 及 agents.resume_cleanup。

目录：
- make_pdf：生成带 Helvetica 双栏文本的确定性 PDF。
- collect：消费异步 NDJSON 生成器为事件列表。
- ResumePdfTests：规则与原生渲染的本地测试。
- ResumePdfTests.test_rules_and_render：保留原文、分栏布局并渲染有界 PNG。
- ResumePdfTests.test_scanned_page：扫描图像无规则文本但可渲染视觉证据。
- ResumePdfTests.test_invalid_inputs：损坏、加密、过多页和超限全部明确拒绝。
- ResumePdfTests.test_conservative_normalization：不误合并列、不猜测断词或日期。
- ResumeFlowTests：异步 HTTP、视觉契约和生命周期测试。
- ResumeFlowTests.setUp：显式模拟沙箱边界，原解析算法在独立单元测试与沙箱联调中验证。
- ResumeFlowTests.setUp.local_parse：仅供流程测试返回既有算法的页面，不属于生产备用路径。
- ResumeFlowTests.test_no_changes_preserves_baseline：无修改建议时逐字保留提取基线。
- ResumeFlowTests.test_precise_corrections：仅替换编号行范围，越界、逆序、重叠或无依据拒绝。
- ResumeFlowTests.test_unchanged_suggestions_preserve_baseline：同文建议保留原文，不冒充实际修改。
- ResumeFlowTests.test_unchanged_suggestions_with_real_changes：
  忽略同文覆盖区间，仍验证行号与理由。
- ResumeFlowTests.test_line_ranges_preserve_unedited_repeated_text：
  行号区分重复文字，保留未选中行、CRLF 与末端无换行。
- ResumeFlowTests.test_vision_order_and_close：规则先返回，视觉按页序合并并关闭。
- ResumeFlowTests.test_failure_no_fallback：视觉失败无成功终态，错误不泄露原文。
- ResumeFlowTests.test_failure_codes_are_specific_and_redacted：
  定位失败与超时返回明确有限码，未知异常不泄露原文。
- ResumeFlowTests.test_cancellation_closes_client：取消发生后释放视觉连接。
- ResumeFlowTests.test_agent_rejects_mismatch_and_omission：拒绝缺图、页码错配与非空页丢失。
- ResumeFlowTests.test_http_contract：真实 Django 路由接受 multipart 并拒绝错误方法和参数。
- ResumeFlowTests.test_default_traditional_has_no_model_call：
  默认传统提取返回原基线，不构造或调用视觉客户端。
- ResumeProviderTests：模拟 SDK，验证真实适配器配置与图文请求。
- ResumeProviderTests.test_explicit_configuration：缺专用模型时不使用面试文本模型。
- ResumeProviderTests.test_multimodal_payload：图片和编号规则行实际进入请求，禁止 SDK 重试。
- ResumeProviderTests.test_invalid_output_is_not_retried：截断及非法 JSON 不能标记成功。

- ControlledAgent：用事件控制每页完成或失败，不依赖时间估计并发。
- ControlledAgent.__init__：建立页面闸门、启动队列和在途计数。
- ControlledAgent.clean_page：记录在途状态并等待测试释放，传播失败及取消。
- ResumeConcurrencyTests：验证有界并发、乱序完成与清理。
- ResumeConcurrencyTests.test_window_and_completion_order：证明三页重叠执行且窗口随完成补充。
- ResumeConcurrencyTests.test_failure_cancels_siblings：失败停止补充并取消其他在途页。
- ResumeConcurrencyTests.test_consumer_close_cancels_pending：消费者在一次产出后关闭时释放兄弟任务。

关键变量：
（无模块级变量。）
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
    """输入页数与加密标志，输出内存 PDF；两列文字固定，不含真实候选人数据。"""
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
    """输入异步字节事件流，输出解码事件；只在测试中全量收集，不访问外部网络。"""
    return [json.loads(line) async for line in stream]


class ResumePdfTests(SimpleTestCase):
    """功能：覆盖真实 pypdf/PDFium 边界；约束：全内存合成数据，无外部服务。"""

    def test_rules_and_render(self):
        """双栏前提下保留两个文本列及原文，PNG 最长边受限且具真实内容。"""
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
        """图像 PDF 不含文本层，规则结果保留空页并提示视觉，渲染仍成功。"""
        with Image.new("RGB", (600, 800), "white") as image:
            ImageDraw.Draw(image).text((50, 50), "Alex / Python / Project", fill="black")
            output = BytesIO()
            image.save(output, format="PDF")
        pages = extract_pdf(output.getvalue())
        self.assertFalse(pages[0].text.strip())
        self.assertIn("扫描页", "".join(pages[0].warnings))
        self.assertTrue(render_pages(output.getvalue(), pages)[0].image_png)

    def test_invalid_inputs(self):
        """边界输入不截断、不尝试解密、不转为成功的空文本。"""
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
        """仅修复明确排版连字，保留缩进、多空格、断词和数值。"""
        self.assertEqual(
            normalize_text("  \ufb01le\u00a0   2024-2025  \r\ndata-\r\nbase"),
            "  file    2024-2025\ndata-\nbase",
        )


class ResumeFlowTests(SimpleTestCase):
    """功能：检查异步业务流与取消；逻辑：视觉端口替身，不验证真实模型准确率。"""

    def setUp(self):
        """显式模拟沙箱边界，原解析算法在独立单元测试与沙箱联调中验证。"""

        async def local_parse(data):
            """仅供流程测试返回既有算法的页面，不属于生产备用路径；不调用外部服务。"""
            return render_pages(data, extract_pdf(data))

        stub = patch("interviews.resume_api.parse_pdf", side_effect=local_parse)
        stub.start()
        self.addCleanup(stub.stop)

    async def test_no_changes_preserves_baseline(self):
        """含缩进、换行和疑点的基线在模型未提出修改时逐字不变。"""
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
        """有效编号范围只替换选中行；越界、逆序、重叠和无依据补丁仍明确拒绝。"""
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
        # 两条实际补录占据同一个虚拟空行，不能因字符范围长度为零而绕过重叠检查。
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
        """模拟视觉同文建议；真实 Agent 保留字符/空白/疑点，changed 为 false 且补丁为空。"""
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
        """同文区间不阻止真实替换；无效行号和空理由仍拒绝，模型仅调用一次。"""
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
        """重复基线不需模型复制定位文本；修改第二行，保留第一行与最后一行全部字符。"""
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
        # 模型提供 LF 时仍恢复范围末端原有 CRLF，保留后续未选中的字符。
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
        """原算法文本与编号行补丁按页合并；第一条进度先于模型构造，SDK 为显式替身。"""
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
        """模拟供应商异常包含敏感文本；响应只显示固定错误且不发送成功终态。"""
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
        """真实流程配合显式视觉替身；验证定位失败/超时提示和有限错误码，原文与任意异常消息不公开。"""
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
        """视觉调用被取消时，不返回错误替代取消，取消已启动的同窗口任务并释放客户端。"""
        provider = SimpleNamespace(
            review=AsyncMock(side_effect=asyncio.CancelledError), close=AsyncMock()
        )
        with patch("interviews.resume_api.ResumeVision", return_value=provider):
            with self.assertRaises(asyncio.CancelledError):
                await collect(resume_events(make_pdf(2), mode="advanced"))
        self.assertEqual(provider.review.await_count, 2)
        provider.close.assert_awaited_once()

    async def test_agent_rejects_mismatch_and_omission(self):
        """输入和模型输出的页身份不可错配，非空规则页不可无说明地遗漏。"""
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
        """真实 Django 路由配合视觉替身；验证非法模式拒绝、显式 advanced 调用和流返回。"""
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
        """真实路由与原规则算法、沙箱替身；缺省模式不创建视觉客户端，返回规则文本和风险提示。"""
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
    """功能：验证 SDK 边界；约束：所有模型请求被 AsyncMock 接管，不能证明供应商可用。"""

    def test_explicit_configuration(self):
        """面试模型即使存在也不能隐式承担图片输入。"""
        with patch.dict("os.environ", {"DASHSCOPE_MODEL": "qwen-plus"}, clear=True):
            with self.assertRaises(ValueError):
                ResumeVision()

    async def test_multimodal_payload(self):
        """实际适配器将 PNG 编码为图像类型而非普通文字；只发一次并关闭连接。"""
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

    async def test_invalid_output_is_not_retried(self):
        """模型截断和非法 JSON 均明确失败，SDK 调用次数恒为一。"""
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
            sdk.chat.completions.create.assert_awaited_once()


class ControlledAgent:
    """功能：模拟受控的页校对；逻辑：事件决定完成时间；约束：无真实模型或睡眠。"""

    def __init__(self, failure=None):
        """输入可选失败页号；建立四页闸门、started 队列、active/peak 及 cancelled 记录。"""
        self.failure = failure
        self.gates = {number: asyncio.Event() for number in range(1, 5)}
        self.started = asyncio.Queue()
        self.active = 0
        self.peak = 0
        self.cancelled = set()

    async def clean_page(self, page):
        """输入页对象，待测试开闸后返回页号；记录峰值，失败/取消时都减少在途计数。"""
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
    """功能：验证调度边界；逻辑：真实 asyncio 任务与事件替身；约束：不证明模型速度。"""

    async def test_window_and_completion_order(self):
        """四页输入先启动三页，第三页先完成后才补第四页；返回实际完成顺序。"""
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
        """第一项失败后第二三项必须被取消，尚未进入窗口的第四项不能启动。"""
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
        """模拟外层已收到一页后断开流，aclose 必须取消其他在途页并阻止第四页启动。"""
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
