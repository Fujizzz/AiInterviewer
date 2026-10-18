"""Extract resume text from UTF-8 files or text-based PDFs."""

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError


def read_resume(path: Path) -> str:
    """Read UTF-8 text or extract PDF pages in order, rejecting empty or unsupported content."""
    if path.suffix.lower() == ".pdf":
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError(
                    "Encrypted PDF is not supported; provide an unlocked PDF or text resume."
                )
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except PyPdfError as exc:
            raise ValueError(
                "Cannot read this PDF; provide a valid PDF or UTF-8 text resume."
            ) from exc
        if not text.strip():
            raise ValueError(
                "PDF contains no extractable text. Provide a text-based PDF or UTF-8 "
                "text; OCR is not included."
            )
    else:
        text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError("Resume must contain text.")
    return text
