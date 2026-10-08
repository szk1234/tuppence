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
from tuppence.ingest.importers.camt import (
    CamtFacts,
    document_from_facts,
    read_camt_facts,
)
from tuppence.ingest.importers.ofx import ofx_document
from tuppence.ingest.importers.qif import qif_document
from tuppence.ingest.importers.xlsx import xlsx_records
from tuppence.ingest.models import Document, FileKind
from tuppence.ingest.ocr import image_rows
from tuppence.ingest.pdftext import pdf_pages, render_page_images
from tuppence.ingest.results import (
    parse_camt_facts,
    parse_image_rows,
    parse_pdf_pages,
    parse_vision_image,
    parse_vision_images,
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
VISION_BATCH = 4


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
    names: Sequence[str] = (),
) -> Document:
    """One overall deadline (`limits.timeout_s`) covers every step of one extraction,
    including each vision call, so a long scan can't run for hours. `names` are the
    household's own names: a line that is only one of them is withheld from the AI."""
    path = path.resolve()  # the sandbox works in its own folder
    clock = _Deadline(limits)
    # Text is prepared in this process; it stops at the same deadline as the sandbox calls.
    if kind == "csv":
        return csv_document(
            path.read_bytes(), sha256=sha256, known=known_header, deadline=clock.remaining
        )
    if kind == "text":
        return text_document(
            decode_text(path.read_bytes()), sha256=sha256, names=names, deadline=clock.remaining
        )
    if kind == "ofx":
        return ofx_document(decode_text(path.read_bytes()), sha256=sha256, deadline=clock.remaining)
    if kind == "qif":
        return qif_document(decode_text(path.read_bytes()), sha256=sha256, deadline=clock.remaining)
    if kind == "camt053":
        return document_from_facts(
            clock.run(parse_camt_facts, read_camt_facts, str(path)),
            sha256=sha256,
            deadline=clock.remaining,
        )
    if kind == "xlsx":
        check_zip(path)
        records = clock.run(parse_xlsx_records, xlsx_records, str(path))
        return table_document(
            records, sha256=sha256, kind="xlsx", known=known_header, deadline=clock.remaining
        )
    if kind == "pdf":
        return _pdf(path, sha256, limits, clock, vision, names)
    return _image(path, sha256, limits, clock, vision, names)


def read_camt(path: Path, limits: ExtractLimits) -> CamtFacts:
    """A CAMT.053 file's facts, with the XML parsed in the sandbox (never in the app).
    Build the statement with `camt.parsed_from_facts`."""
    return _Deadline(limits).run(parse_camt_facts, read_camt_facts, str(path.resolve()))


class _Deadline:
    """Hands each sandbox call what is left of the extraction's time."""

    def __init__(self, limits: ExtractLimits) -> None:
        self.limits = limits
        self.end = time.monotonic() + limits.timeout_s

    def left(self) -> float:
        """Seconds left (zero or less once the time is up)."""
        return self.end - time.monotonic()

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


def _prepare(vision: VisionReader, clock: _Deadline, limits: ExtractLimits, pages: int) -> None:
    """Before any vision call: refuse a scan with more pages than the run can read, and bound
    every call by what is left of the extraction's time (M9)."""
    if pages and (check := getattr(vision, "check_pages", None)) is not None:
        check(pages)
    if (bound := getattr(vision, "bound", None)) is not None:
        bound(clock.left, limits.timeout_s)


def _pdf(
    path: Path,
    sha256: str,
    limits: ExtractLimits,
    clock: _Deadline,
    vision: VisionReader | None,
    names: Sequence[str] = (),
) -> Document:
    result = clock.run(parse_pdf_pages, pdf_pages, str(path), limits.max_pages, vision is None)
    pages = result.pages
    ocr_pages = list(result.ocr_pages)
    warnings: list[str] = []
    if vision is not None:
        scanned = list(result.scanned_pages)
        _prepare(vision, clock, limits, len(scanned))
        for start in range(0, len(scanned), VISION_BATCH):
            batch = scanned[start : start + VISION_BATCH]
            # A few pages per sandbox call keeps each reply small; every call and every
            # model call counts against the same overall deadline.
            images = clock.run(parse_vision_images, render_page_images, str(path), batch)
            for number, picture in zip(batch, images, strict=True):
                clock.remaining()
                pages[number - 1] = vision.transcribe(picture.data, picture.media_type)
                ocr_pages.append(number)
        if result.scanned_pages:
            warnings.append("Scanned pages were read by your AI vision model.")
    doc = pages_document(pages, sha256=sha256, kind="pdf", names=names, deadline=clock.remaining)
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
    names: Sequence[str] = (),
) -> Document:
    if vision is not None:
        _prepare(vision, clock, limits, 1)
        # Decoded, size-checked and re-encoded in the sandbox: no metadata goes to the model.
        picture = clock.run(parse_vision_image, vision_image, str(path))
        rows, confidence = vision.transcribe(picture.data, picture.media_type), None
    else:
        result = clock.run(parse_image_rows, image_rows, str(path))
        rows, confidence = result.rows, result.ocr_confidence
    # Screenshot withholding (textprep.split_screenshot, with the shared classifier).
    doc = pages_document([rows], sha256=sha256, kind="image", names=names, deadline=clock.remaining)
    doc.pages, doc.ocr_pages, doc.ocr_confidence = 1, [1], confidence
    if confidence is not None and confidence < LOW_OCR_CONFIDENCE:
        doc.warnings.append(
            "The screenshot is hard to read, so check these transactions carefully."
        )
    return doc
