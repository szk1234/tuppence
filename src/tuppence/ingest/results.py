"""Validators for what the sandbox child sends back.

The child handles hostile files, so its reply is never trusted: it arrives as JSON and each
sandboxed function has a validator here that checks types, sizes and keys, rejects anything
unexpected, and builds the result. Nothing from the child is ever unpickled.
"""

from __future__ import annotations

import base64
import binascii
import math
from dataclasses import dataclass
from typing import Any, Literal

MAX_TEXT = 1_000_000  # one string
MAX_ITEMS = 1_000_000  # one list
MAX_BYTES = 50 * 1024 * 1024
MAX_PAGE_NUMBER = 1_000_000


class BadReply(ValueError):
    """The child's reply isn't the shape the function should produce."""


def _dict(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise BadReply("unexpected keys")
    return value


def _list(value: Any, limit: int = MAX_ITEMS) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise BadReply("expected a list")
    return value


def _int(value: Any, low: int = 0, high: int = MAX_PAGE_NUMBER) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BadReply("expected an integer")
    return value


def _float01(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BadReply("expected a number")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise BadReply("expected a number from 0 to 1")
    return number


def _str(value: Any, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise BadReply("expected text")
    return value


def _strs(value: Any, limit: int = MAX_ITEMS) -> list[str]:
    return [_str(v) for v in _list(value, limit)]


def _bytes(value: Any) -> bytes:
    """Bytes travel as {"$b64": "<base64>"}."""
    text = _str(_dict(value, {"$b64"})["$b64"], MAX_BYTES * 2)
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise BadReply("expected bytes") from None
    if len(data) > MAX_BYTES:
        raise BadReply("too large")
    return data


def parse_scalar(value: Any) -> Any:
    """For simple results (None, bool, number, text)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise BadReply("expected a simple value")


@dataclass(frozen=True)
class PdfPages:
    pages: list[list[str]]
    page_count: int
    ocr_pages: list[int]
    scanned_pages: list[int]
    ocr_confidence: float | None


def parse_pdf_pages(value: Any) -> PdfPages:
    raw = _dict(value, {"pages", "page_count", "ocr_pages", "scanned_pages", "ocr_confidence"})
    confidence = raw["ocr_confidence"]
    return PdfPages(
        pages=[_strs(page) for page in _list(raw["pages"], MAX_PAGE_NUMBER)],
        page_count=_int(raw["page_count"]),
        ocr_pages=[_int(n, 1) for n in _list(raw["ocr_pages"], MAX_PAGE_NUMBER)],
        scanned_pages=[_int(n, 1) for n in _list(raw["scanned_pages"], MAX_PAGE_NUMBER)],
        ocr_confidence=None if confidence is None else _float01(confidence),
    )


@dataclass(frozen=True)
class ImageRows:
    rows: list[str]
    ocr_confidence: float


def parse_image_rows(value: Any) -> ImageRows:
    raw = _dict(value, {"rows", "ocr_confidence"})
    return ImageRows(rows=_strs(raw["rows"]), ocr_confidence=_float01(raw["ocr_confidence"]))


@dataclass(frozen=True)
class VisionImage:
    data: bytes
    media_type: Literal["image/png", "image/jpeg"]


def parse_vision_image(value: Any) -> VisionImage:
    items = _list(value, 2)
    if len(items) != 2:
        raise BadReply("expected two items")
    media_type = _str(items[1], 20)
    if media_type == "image/png":
        return VisionImage(_bytes(items[0]), "image/png")
    if media_type == "image/jpeg":
        return VisionImage(_bytes(items[0]), "image/jpeg")
    raise BadReply("unexpected image type")


def parse_png_list(value: Any) -> list[bytes]:
    return [_bytes(item) for item in _list(value, MAX_PAGE_NUMBER)]


def parse_xlsx_records(value: Any) -> list[tuple[int, str, list[str]]]:
    out: list[tuple[int, str, list[str]]] = []
    for item in _list(value):
        row = _list(item, 3)
        if len(row) != 3:
            raise BadReply("expected three items")
        out.append((_int(row[0], 1, MAX_ITEMS), _str(row[1]), _strs(row[2], 10_000)))
    return out
