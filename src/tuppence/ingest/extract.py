"""Turn a stored upload into a Document (spec §6.2 step 1).

Text formats are read in-process. Spreadsheets, PDFs and images are opened in the
sandbox, with a time limit and a memory limit.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from tuppence.ingest.imaging import vision_image
from tuppence.ingest.importers.camt import camt_document
from tuppence.ingest.importers.ofx import ofx_document
from tuppence.ingest.importers.qif import qif_document
from tuppence.ingest.importers.xlsx import xlsx_records
from tuppence.ingest.models import Document, FileKind
from tuppence.ingest.ocr import image_rows
from tuppence.ingest.pdftext import pdf_pages, render_pages_png
from tuppence.ingest.results import (
    parse_image_rows,
    parse_pdf_pages,
    parse_png_list,
    parse_vision_image,
    parse_xlsx_records,
)
from tuppence.ingest.sandbox import SandboxTimeout, run_isolated
from tuppence.ingest.sniff import check_zip
from tuppence.ingest.textnum import decode_text
from tuppence.ingest.textprep import (
    csv_document,
    pages_document,
    table_document,
    text_document,
)

LOW_OCR_CONFIDENCE = 0.6


class ExtractLimits(BaseModel):
    max_pages: int = 50
    timeout_s: float = 180.0
    memory_mb: int = 2048


class VisionReader(Protocol):
    def transcribe(self, image: bytes, media_type: str) -> list[str]: ...


def extract_document(
    path: Path,
    kind: FileKind,
    *,
    sha256: str,
    limits: ExtractLimits,
    known_header: Callable[[Sequence[str]], bool] | None = None,
    vision: VisionReader | None = None,
) -> Document:
    """One overall deadline (`limits.timeout_s`) covers every step of one extraction,
    including each vision call, so a long scan can't run for hours."""
    path = path.resolve()  # the sandbox works in its own folder
    clock = _Deadline(limits)
    if kind == "csv":
        return csv_document(path.read_bytes(), sha256=sha256, known=known_header)
    if kind == "text":
        return text_document(decode_text(path.read_bytes()), sha256=sha256)
    if kind == "ofx":
        return ofx_document(decode_text(path.read_bytes()), sha256=sha256)
    if kind == "qif":
        return qif_document(decode_text(path.read_bytes()), sha256=sha256)
    if kind == "camt053":
        return camt_document(path.read_bytes(), sha256=sha256)
    if kind == "xlsx":
        check_zip(path)
        records = clock.run(parse_xlsx_records, xlsx_records, str(path))
        return table_document(records, sha256=sha256, kind="xlsx", known=known_header)
    if kind == "pdf":
        return _pdf(path, sha256, limits, clock, vision)
    return _image(path, sha256, limits, clock, vision)


class _Deadline:
    """Hands each sandbox call what is left of the extraction's time."""

    def __init__(self, limits: ExtractLimits) -> None:
        self.limits = limits
        self.end = time.monotonic() + limits.timeout_s

    def remaining(self) -> float:
        left = self.end - time.monotonic()
        if left <= 0:
            raise SandboxTimeout(
                f"Reading this file took longer than {int(self.limits.timeout_s)} seconds, "
                "so it was stopped."
            )
        return min(left, self.limits.timeout_s)

    def run[T](self, parse: Callable[[Any], T], fn: Callable[..., Any], *args: object) -> T:
        return run_isolated(
            fn, *args, timeout_s=self.remaining(), memory_mb=self.limits.memory_mb, parse=parse
        )


def _pdf(
    path: Path,
    sha256: str,
    limits: ExtractLimits,
    clock: _Deadline,
    vision: VisionReader | None,
) -> Document:
    result = clock.run(parse_pdf_pages, pdf_pages, str(path), limits.max_pages, vision is None)
    pages = result.pages
    ocr_pages = list(result.ocr_pages)
    warnings: list[str] = []
    if vision is not None:
        scanned = list(result.scanned_pages)
        pngs = clock.run(parse_png_list, render_pages_png, str(path), scanned) if scanned else []
        for number, png in zip(scanned, pngs, strict=True):
            clock.remaining()  # the model calls count against the same deadline
            pages[number - 1] = vision.transcribe(png, "image/png")
            ocr_pages.append(number)
        if result.scanned_pages:
            warnings.append("Scanned pages were read by your AI vision model.")
    doc = pages_document(pages, sha256=sha256, kind="pdf")
    doc.pages, doc.ocr_pages, doc.ocr_confidence = (
        result.page_count,
        sorted(ocr_pages),
        result.ocr_confidence,
    )
    if result.page_count > limits.max_pages:
        warnings.append(
            f"Only the first {limits.max_pages} of {result.page_count} pages were read."
        )
    if doc.ocr_confidence is not None and doc.ocr_confidence < LOW_OCR_CONFIDENCE:
        warnings.append("The scan is hard to read, so check these transactions carefully.")
    if not doc.lines:
        warnings.append("No text was found in this PDF.")
    doc.warnings = warnings
    return doc


def _image(
    path: Path,
    sha256: str,
    limits: ExtractLimits,
    clock: _Deadline,
    vision: VisionReader | None,
) -> Document:
    if vision is not None:
        # Decoded, size-checked and re-encoded in the sandbox: no metadata goes to the model.
        picture = clock.run(parse_vision_image, vision_image, str(path))
        rows, confidence = vision.transcribe(picture.data, picture.media_type), None
    else:
        result = clock.run(parse_image_rows, image_rows, str(path))
        rows, confidence = result.rows, result.ocr_confidence
    doc = pages_document([rows], sha256=sha256, kind="image", preamble=False)
    doc.pages, doc.ocr_pages, doc.ocr_confidence = 1, [1], confidence
    if confidence is not None and confidence < LOW_OCR_CONFIDENCE:
        doc.warnings.append(
            "The screenshot is hard to read, so check these transactions carefully."
        )
    return doc
