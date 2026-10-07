"""PDF pages to text rows. Runs inside the sandbox (spec §14.2)."""

from __future__ import annotations

from typing import Any

from tuppence.ingest.layout_rows import Box, rows_from_boxes

MIN_WORDS_FOR_TEXT_LAYER = 5


def pdf_pages(path: str, max_pages: int, ocr: bool, render_dpi: int = 200) -> dict[str, Any]:
    """{"pages": [[row, …], …], "page_count", "ocr_pages", "ocr_confidence", "scanned_pages"}.

    Pages with a text layer are rebuilt from pdfplumber's word positions. Pages
    without one go through RapidOCR when `ocr` is true; otherwise they're listed in
    `scanned_pages` so the caller can use the vision model instead.
    """
    import pdfplumber

    pages: list[list[str]] = []
    ocr_pages: list[int] = []
    scanned: list[int] = []
    confidences: list[float] = []
    with pdfplumber.open(path) as pdf:
        count = len(pdf.pages)
        for number, page in enumerate(pdf.pages[:max_pages], start=1):
            words = page.extract_words(x_tolerance=1.5, y_tolerance=3, keep_blank_chars=False)
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

            image = page.to_image(resolution=render_dpi).original
            rows, confidence = ocr_image(image)
            pages.append(rows)
            ocr_pages.append(number)
            confidences.append(confidence)
    return {
        "pages": pages,
        "page_count": count,
        "ocr_pages": ocr_pages,
        "scanned_pages": scanned,
        "ocr_confidence": (sum(confidences) / len(confidences)) if confidences else None,
    }


def render_page_png(path: str, page_number: int, dpi: int = 150) -> bytes:
    """One page as PNG bytes, for the vision model."""
    import io

    import pdfplumber

    with pdfplumber.open(path) as pdf:
        image = pdf.pages[page_number - 1].to_image(resolution=dpi).original
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()
