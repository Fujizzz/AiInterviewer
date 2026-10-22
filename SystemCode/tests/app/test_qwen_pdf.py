"""Responsibilities: Verify Qwen JSON validation and text/PDF resume loading without API calls.
Implementation: Execute typed provider or application paths with explicit validation boundaries.
Related Modules: shared.resume_contract and app.parsing.resume define canonical resume data.
Declaration Index:
- response: Build a minimal fake Chat Completions response with content and a finish reason.
- QwenTests: Group isolated contract and lifecycle regression cases.
- QwenTests.test_resume_invalid_output_fails_once_with_scoped_timeout: Mock SDK responses to
  verify canonical extraction has one attempt and its own deadline.
- QwenTests.test_json_validation_and_retry: Check Qwen configuration and recovery from one
  schema-invalid JSON response.
- QwenTests.test_invalid_json_is_bounded: Ensure invalid Qwen JSON stops after two attempts.
- QwenTests.test_truncated_output_is_rejected: Verify incomplete Qwen output is rejected before
  schema parsing.
- ResumeTests: Group isolated contract and lifecycle regression cases.
- ResumeTests.test_utf8_text: Verify Chinese UTF-8 text with a byte-order mark is read
  correctly.
- ResumeTests.test_blank_pdf_rejected: Create a blank PDF and confirm the loader rejects missing
  text.
- ResumeTests.test_pdf_pages_extracted_in_order: Verify extracted text preserves PDF page order.
Variable Index:
None
"""

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfWriter

from app.adapters.llm import GeneratedText
from app.parsing.files import read_resume
from app.parsing.resume import parse_resume_profile
from app.providers.llm import LLMError, OpenAILLM


def response(content, reason="stop"):
    """Build a minimal fake Chat Completions response with content and a finish reason."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=reason, message=SimpleNamespace(content=content, refusal=None)
            )
        ]
    )


@patch("app.providers.llm.load_dotenv")
@patch.dict(
    "os.environ",
    {
        "LLM_PROVIDER": "dashscope",
        "DASHSCOPE_API_KEY": "test-key",
        "DASHSCOPE_MODEL": "qwen-plus",
        "OPENAI_TEMPERATURE": "0",
    },
    clear=True,
)
@patch("app.providers.llm.OpenAI")
class QwenTests(unittest.TestCase):
    """Functionality: Group isolated contract and lifecycle regression cases.
    Logic: Test setup supplies local state and explicit external-service substitutes.
    Constraints: Test assertions do not establish live provider availability.
    """

    def test_resume_invalid_output_fails_once_with_scoped_timeout(self, client, dotenv):
        """Mock SDK responses to verify canonical extraction has one attempt and its own deadline.
        Invalid JSON must surface as LLMError; ordinary generation retains its existing timeout.
        """
        create = client.return_value.chat.completions.create
        create.side_effect = [response('{"projects": "invalid"}'), response('{"text":"OK"}')]
        model = OpenAILLM()
        with self.assertRaisesRegex(LLMError, "invalid structured JSON"):
            asyncio.run(parse_resume_profile("Built an API using Python", llm=model))
        self.assertEqual(create.call_count, 1)
        self.assertGreater(create.call_args.kwargs["timeout"], 30)
        self.assertLessEqual(create.call_args.kwargs["timeout"], 90)
        model("Reply", {}, GeneratedText)
        self.assertEqual(create.call_args.kwargs["timeout"], 30)

    def test_json_validation_and_retry(self, client, dotenv):
        """Check Qwen configuration and recovery from one schema-invalid JSON response."""
        create = client.return_value.chat.completions.create
        create.side_effect = [
            response('{"wrong_key": 1}'),
            response(json.dumps({"text": "What did you build?"})),
        ]
        model = OpenAILLM()
        self.assertEqual(
            model("Generate a question", {}, GeneratedText).text, "What did you build?"
        )
        self.assertEqual(create.call_count, 2)
        self.assertEqual(create.call_args.kwargs["model"], "qwen-plus")
        self.assertEqual(create.call_args.kwargs["response_format"], {"type": "json_object"})
        self.assertFalse(create.call_args.kwargs["extra_body"]["enable_thinking"])
        self.assertIn("dashscope.aliyuncs.com", client.call_args.kwargs["base_url"])
        client.return_value.responses.parse.assert_not_called()

    def test_invalid_json_is_bounded(self, client, dotenv):
        """Ensure invalid Qwen JSON stops after two attempts."""
        client.return_value.chat.completions.create.return_value = response("not JSON")
        with self.assertRaisesRegex(LLMError, "twice"):
            OpenAILLM()("Question", {}, GeneratedText)
        self.assertEqual(client.return_value.chat.completions.create.call_count, 2)

    def test_truncated_output_is_rejected(self, client, dotenv):
        """Verify incomplete Qwen output is rejected before schema parsing."""
        client.return_value.chat.completions.create.return_value = response("{}", "length")
        with self.assertRaisesRegex(LLMError, "incomplete"):
            OpenAILLM()("Question", {}, GeneratedText)


class ResumeTests(unittest.TestCase):
    """Functionality: Group isolated contract and lifecycle regression cases.
    Logic: Test setup supplies local state and explicit external-service substitutes.
    Constraints: Test assertions do not establish live provider availability.
    """

    def test_utf8_text(self):
        """Verify Chinese UTF-8 text with a byte-order mark is read correctly."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "resume.txt"
            path.write_text("姓名：测试\n项目：图像识别", encoding="utf-8-sig")
            self.assertTrue(read_resume(path).startswith("姓名"))

    def test_blank_pdf_rejected(self):
        """Create a blank PDF and confirm the loader rejects missing text."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "empty.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            writer.write(path)
            with self.assertRaisesRegex(ValueError, "no extractable text"):
                read_resume(path)

    def test_pdf_pages_extracted_in_order(self):
        """Verify extracted text preserves PDF page order."""
        with patch("app.parsing.files.PdfReader") as reader:
            reader.return_value.is_encrypted = False
            reader.return_value.pages = [
                SimpleNamespace(extract_text=lambda: "Project A"),
                SimpleNamespace(extract_text=lambda: "Project B"),
            ]
            self.assertEqual(read_resume(Path("resume.pdf")), "Project A\n\nProject B")


if __name__ == "__main__":
    unittest.main()
