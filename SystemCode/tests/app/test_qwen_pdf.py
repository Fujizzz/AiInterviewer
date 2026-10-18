"""Verify Qwen JSON validation and text/PDF resume loading without API calls."""

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
    def test_resume_array_repair_and_scoped_timeout(self, client, dotenv):
        create = client.return_value.chat.completions.create
        invalid = json.dumps(
            {"projects": [{"name": "API", "technologies": "Python", "claims": None, "metrics": ""}]}
        )
        valid = json.dumps(
            {
                "projects": [
                    {
                        "name": "API",
                        "technologies": ["Python"],
                        "claims": ["Built an API"],
                        "metrics": [],
                    }
                ]
            }
        )
        create.side_effect = [response(invalid), response(valid), response('{"text":"OK"}')]
        model = OpenAILLM()
        profile, _ = asyncio.run(parse_resume_profile("Built an API using Python", llm=model))
        self.assertEqual(profile.projects[0].technologies, ["Python"])
        self.assertEqual(profile.projects[0].metrics, [])
        first, second = create.call_args_list
        self.assertGreater(first.kwargs["timeout"], 30)
        self.assertLessEqual(first.kwargs["timeout"], 90)
        self.assertLessEqual(second.kwargs["timeout"], first.kwargs["timeout"])
        messages = second.kwargs["messages"]
        self.assertIn("list_type means", messages[0]["content"])
        self.assertEqual(messages[-2], {"role": "assistant", "content": invalid})
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
