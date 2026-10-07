"""PDF pages to text rows. Runs inside the sandbox (spec §14.2)."""

from __future__ import annotations

from typing import Any

from tuppence.core.errors import UserFacing
from tuppence.ingest.imaging import encode_for_vision, render_scale
from tuppence.ingest.layout_rows import Box, rows_from_boxes

MIN_WORDS_FOR_TEXT_LAYER = 5
MAX_WORDS_PER_PAGE = 20_000
MAX_CHARS_PER_PAGE = 250_000
PAGE_COUNT_FACTOR = 10  # a PDF with more than this many times max_pages is refused outright


class PdfRefused(UserFacing, ValueError):
    """A PDF Tuppence won't or can't read. The message is safe to show."""


def _open(path: str) -> Any:
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw

    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        if getattr(exc, "err_code", None) == pdfium_raw.FPDF_ERR_PASSWORD:
            raise PdfRefused(
                "This PDF is password-protected. Remove the password and upload it again."
            ) from None
        raise PdfRefused("This PDF couldn't be read. The file may be damaged.") from None


def _render(page: Any, dpi: int) -> Any:
    """The page as a PIL image, no bigger than the pixel cap whatever its page size."""
    width, height = page.get_size()
    return page.render(scale=render_scale(width, height, dpi)).to_pil()


def pdf_pages(path: str, max_pages: int, ocr: bool, render_dpi: int = 200) -> dict[str, Any]:
    """{"pages": [[row, …], …], "page_count", "ocr_pages", "ocr_confidence", "scanned_pages"}.

    Pages with a text layer are rebuilt from pdfplumber's word positions. Pages
    without one go through RapidOCR when `ocr` is true; otherwise they're listed in
    `scanned_pages` so the caller can use the vision model instead.

    The page count comes from PDFium, which doesn't build every page, so a file with a
    huge page tree is refused before pdfplumber sees it. A page with too much text, or a
    page too large to render within the pixel cap, is handled without exhausting memory.
    """
    import pdfplumber

    doc = _open(path)
    try:
        count = len(doc)
        if count > max_pages * PAGE_COUNT_FACTOR:
            raise PdfRefused(
                f"This PDF has {count:,} pages, which is more than Tuppence reads "
                f"(it reads up to {max_pages}). Split it and upload the part you need."
            )
        pages: list[list[str]] = []
        ocr_pages: list[int] = []
        scanned: list[int] = []
        confidences: list[float] = []
        with pdfplumber.open(path) as pdf:
            for number in range(1, min(count, max_pages) + 1):
                fast = doc[number - 1]
                if fast.get_textpage().count_chars() > MAX_CHARS_PER_PAGE:
                    raise PdfRefused(f"Page {number} has too much text to read safely.")
                words = pdf.pages[number - 1].extract_words(
                    x_tolerance=1.5, y_tolerance=3, keep_blank_chars=False
                )
                if len(words) > MAX_WORDS_PER_PAGE:
                    raise PdfRefused(f"Page {number} has too many words to read safely.")
                if len(words) >= MIN_WORDS_FOR_TEXT_LAYER:
                    pages.append(
                        rows_from_boxes(
                            [Box(w["text"], w["x0"], w["top"], w["x1"], w["bottom"]) for w in words]
                        )
                    )
                    continue
                if not ocr:
                    scanned.append(number)
                    pages.append([])
                    continue
                from tuppence.ingest.ocr import ocr_image

                rows, confidence = ocr_image(_render(fast, render_dpi))
                pages.append(rows)
                ocr_pages.append(number)
                if rows:  # a blank page (the back of a duplex scan) says nothing about quality
                    confidences.append(confidence)
    finally:
        doc.close()
    return {
        "pages": pages,
        "page_count": count,
        "ocr_pages": ocr_pages,
        "scanned_pages": scanned,
        "ocr_confidence": (sum(confidences) / len(confidences)) if confidences else None,
    }


def render_pages_png(path: str, numbers: list[int], dpi: int = 150) -> list[bytes]:
    """The given pages (1-based) as clean PNG bytes, for the vision model."""
    doc = _open(path)
    try:
        return [encode_for_vision(_render(doc[n - 1], dpi))[0] for n in numbers]
    finally:
        doc.close()
