"""Turn a stored upload into a Document (spec §6.2 step 1).

Text formats are read in-process. Spreadsheets, PDFs and images are opened in the
sandbox, with a time limit and a memory limit.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from tuppence.ingest.importers.camt import camt_document
from tuppence.ingest.importers.ofx import ofx_document
from tuppence.ingest.importers.qif import qif_document
from tuppence.ingest.importers.xlsx import xlsx_records
from tuppence.ingest.models import Document, FileKind
from tuppence.ingest.ocr import image_rows
from tuppence.ingest.pdftext import pdf_pages, render_page_png
from tuppence.ingest.sandbox import run_isolated
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
        records = run_isolated(
            xlsx_records, str(path), timeout_s=limits.timeout_s, memory_mb=limits.memory_mb
        )
        return table_document(records, sha256=sha256, kind="xlsx", known=known_header)
    if kind == "pdf":
        return _pdf(path, sha256, limits, vision)
    return _image(path, sha256, limits, vision)


def _pdf(path: Path, sha256: str, limits: ExtractLimits, vision: VisionReader | None) -> Document:
    result = run_isolated(
        pdf_pages,
        str(path),
        limits.max_pages,
        vision is None,
        timeout_s=limits.timeout_s,
        memory_mb=limits.memory_mb,
    )
    pages: list[list[str]] = result["pages"]
    ocr_pages: list[int] = list(result["ocr_pages"])
    warnings: list[str] = []
    if vision is not None:
        for number in result["scanned_pages"]:
            png = run_isolated(
                render_page_png,
                str(path),
                number,
                timeout_s=limits.timeout_s,
                memory_mb=limits.memory_mb,
            )
            pages[number - 1] = vision.transcribe(png, "image/png")
            ocr_pages.append(number)
        if result["scanned_pages"]:
            warnings.append("Scanned pages were read by your AI vision model.")
    doc = pages_document(pages, sha256=sha256, kind="pdf")
    doc.pages, doc.ocr_pages, doc.ocr_confidence = (
        result["page_count"],
        sorted(ocr_pages),
        result["ocr_confidence"],
    )
    if result["page_count"] > limits.max_pages:
        warnings.append(
            f"Only the first {limits.max_pages} of {result['page_count']} pages were read."
        )
    if doc.ocr_confidence is not None and doc.ocr_confidence < LOW_OCR_CONFIDENCE:
        warnings.append("The scan is hard to read, so check these transactions carefully.")
    if not doc.lines:
        warnings.append("No text was found in this PDF.")
    doc.warnings = warnings
    return doc


def _image(path: Path, sha256: str, limits: ExtractLimits, vision: VisionReader | None) -> Document:
    if vision is not None:
        media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        rows, confidence = vision.transcribe(path.read_bytes(), media_type), None
    else:
        result = run_isolated(
            image_rows, str(path), timeout_s=limits.timeout_s, memory_mb=limits.memory_mb
        )
        rows, confidence = result["rows"], result["ocr_confidence"]
    doc = pages_document([rows], sha256=sha256, kind="image", preamble=False)
    doc.pages, doc.ocr_pages, doc.ocr_confidence = 1, [1], confidence
    if confidence is not None and confidence < LOW_OCR_CONFIDENCE:
        doc.warnings.append(
            "The screenshot is hard to read, so check these transactions carefully."
        )
    return doc
