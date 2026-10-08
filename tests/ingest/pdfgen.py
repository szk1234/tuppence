"""Small synthetic PDFs for reviewer probes: each line is a list of (x, text, right-aligned?)
cells at one height, so cells on one line can be merged by the text layer as a bank's
right-hand box is."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

WATERMARK = "SYNTHETIC TEST STATEMENT - NOT A REAL DOCUMENT"


def pdf(pages: list[list[list[tuple[int, str, bool]]]]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for lines in pages:
        y = 800
        c.setFont("Helvetica", 10)
        for cells in lines:
            for x, text, right in cells:
                (c.drawRightString if right else c.drawString)(x, y, text)
            y -= 15
        c.setFont("Helvetica", 8)
        c.drawString(56, 48, WATERMARK)
        c.showPage()
    c.save()
    return buf.getvalue()


def L(*cells: tuple[int, str, bool]) -> list[tuple[int, str, bool]]:  # noqa: N802 - the probes' name
    return list(cells)
