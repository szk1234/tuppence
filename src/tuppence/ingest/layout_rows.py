"""Rebuild text rows from positioned words or OCR boxes. Pure functions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple


class Box(NamedTuple):
    text: str
    left: float
    top: float
    right: float
    bottom: float


def rows_from_boxes(boxes: Sequence[Box], *, gap_factor: float = 1.8) -> list[str]:
    """Group boxes whose vertical centres sit within half a line height into one row,
    left to right. A wide horizontal gap (a column break) becomes three spaces."""
    if not boxes:
        return []

    def centre(b: Box) -> float:
        return (b.top + b.bottom) / 2

    rows: list[list[Box]] = []
    for box in sorted(boxes, key=centre):
        if rows:
            row = rows[-1]
            row_centre = sum(centre(b) for b in row) / len(row)
            height = max(b.bottom - b.top for b in row)
            if abs(centre(box) - row_centre) <= height / 2:
                row.append(box)
                continue
        rows.append([box])
    out: list[str] = []
    for row in rows:
        ordered = sorted(row, key=lambda b: b.left)
        widths = [(b.right - b.left) / max(1, len(b.text)) for b in ordered]
        char_width = sorted(widths)[len(widths) // 2]
        parts = [ordered[0].text]
        for prev, box in zip(ordered, ordered[1:], strict=False):
            gap = box.left - prev.right
            parts.append("   " if gap > gap_factor * char_width * 1.5 else " ")
            parts.append(box.text)
        out.append("".join(parts).strip())
    return out
