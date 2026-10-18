"""Responsibilities: Local PDF rule extraction and bounded page rendering, no model invocation, no
saving of uploaded files.
Implementation: pypdf preserves layout extraction with conservative character cleaning; PDFium
generates PNG within mutual exclusion lock.
Related Modules: Production only calls pdf_worker in isolated process; local unit tests may call
directly, Agent receives single-page evidence.
Declaration Index:
- PdfInputError: Input validation errors safe for display.
- normalize_text: Fixes known typographic characters while preserving row and column whitespace.
- extract_pdf: Validates PDF and extracts rule text per page.
- render_pages: Generates images with limited pixel dimensions and closes all PDFium native
  resources.
Variable Index:
- MAX_BYTES: Maximum PDF byte size accepted by this new upload endpoint.
- MAX_PAGES: Maximum number of pages accepted by this new endpoint, no truncation of oversized
  documents.
- MAX_TEXT: Maximum acceptable extracted characters per page, exceeding triggers explicit failure.
- IMAGE_EDGE: Maximum edge pixel length, limits image memory and visual input scale.
- PDFIUM_LOCK: Protects full lifecycle of non-thread-safe PDFium objects.
- CHAR_MAP: Only fixes known typographic ligatures and non-breaking spaces, does not guess OCR
  characters.
"""

from dataclasses import replace
from io import BytesIO
from math import isfinite, nextafter
from threading import Lock

import pypdfium2 as pdfium
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from agents.resume_cleanup import ResumePage

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_TEXT = 30000
IMAGE_EDGE = 1800
PDFIUM_LOCK = Lock()
CHAR_MAP = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u00a0": " ",
    }
)


class PdfInputError(ValueError):
    """Function: Identify expected input failure; logic: exception text is fixed diagnostic
    statement; constraint: contains no resume data.
    """


def normalize_text(text: str) -> str:
    """Input layout text, output unified line breaks and clearly fixed ligatures.

    Preserve leading indentation and internal multiple spaces to avoid further breaking columns; do
    not merge hyphenated words, do not guess corrupted text,
    Do not remove headers or duplicate experiences. Original text is retained independently by
    caller, no I/O side effects.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(CHAR_MAP)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def extract_pdf(data: bytes) -> list[ResumePage]:
    """Input complete PDF bytes, return rule text in original page order and risk warnings; no OCR
    or visual calls.

    Exceeding limits, encrypted, damaged, or zero-page PDFs raise PdfInputError, no truncation, no
    decryption attempt.
    Pages with no text are preserved for potential visual transcription. pypdf parsing errors
    converted to fixed diagnostics, no exposure of file content.
    """
    if not data or len(data) > MAX_BYTES:
        raise PdfInputError("The PDF must be non-empty and no larger than 10 MiB.")
    if not data.startswith(b"%PDF-"):
        raise PdfInputError("The file content is not a PDF.")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise PdfInputError(
                "Encrypted PDFs are not accepted. Decrypt the file before uploading it."
            )
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise PdfInputError("The PDF must contain between 1 and 10 pages.")
        pages = []
        for number, page in enumerate(reader.pages, 1):
            raw = page.extract_text(extraction_mode="layout", layout_mode_strip_rotated=False)
            if len(raw) > MAX_TEXT:
                raise PdfInputError(
                    "A page contains more than 30,000 characters. Split the document and try again."
                )
            text = normalize_text(raw)
            warnings = [
                (
                    "Rule-based extraction preserves layout spacing; "
                    "verify columns, charts, and reading order."
                )
            ]
            if not text.strip():
                warnings.append(
                    "No text was extracted. The page may be scanned "
                    "or blank; visual review is available."
                )
            if "\ufffd" in text or "\x00" in text:
                warnings.append(
                    "Unusual characters were detected. Compare them "
                    "with the original PDF or use visual review."
                )
            pages.append(ResumePage(number, raw, text, warnings))
        return pages
    except PdfInputError:
        raise
    except (PyPdfError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise PdfInputError(
            "The PDF structure or text encoding could not be "
            "parsed. Check the file or export it again."
        ) from exc


def render_pages(data: bytes, pages: list[ResumePage]) -> list[ResumePage]:
    """Input validated PDF and list of pages, output list with PNGs; no disk writes.

    Create, use, and close PDFium objects within the same mutual exclusion zone to prevent
    concurrent thread corruption of native state.
    Longest edge at most IMAGE_EDGE pixels; any page failure causes overall error, no skipping
    pages.
    Normal path finally releases bitmap, page, and document; production cancellation terminates
    entire isolated process via supervisor.
    """
    with PDFIUM_LOCK, pdfium.PdfDocument(data) as document:
        if len(document) != len(pages):
            raise PdfInputError(
                "The page counts from text parsing and page rendering do not match."
            )
        rendered = []
        for source in pages:
            page = document[source.number - 1]
            try:
                width, height = page.get_size()
                if not all(isfinite(value) and value > 0 for value in (width, height)):
                    raise PdfInputError("The PDF page dimensions are invalid.")
                # PDFium rounds up pixels; rounds toward zero for adjacent floating-point values to
                # avoid exceeding limit exactly.
                scale = min(2.5, nextafter(IMAGE_EDGE / max(width, height), 0))
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    try:
                        output = BytesIO()
                        image.save(output, format="PNG")
                        rendered.append(replace(source, image_png=output.getvalue()))
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        return rendered
