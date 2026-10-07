"""Build the synthetic PDF and image statement fixtures.

    uv run python scripts/make_statement_fixtures.py

Everything here is invented: names, addresses, numbers and amounts.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pypdfium2 as pdfium
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/statements")
WATERMARK = "SYNTHETIC TEST STATEMENT - NOT A REAL DOCUMENT"

CARD_PAGES = [
    [
        ("29 Sep 2026", "Greenbasket Stores", "42.18"),
        ("30 Sep 2026", "Northwind Books", "18.40"),
        ("01 Oct 2026", "Little Lantern Cafe", "6.75"),
        ("02 Oct 2026", "Payment received - thank you", "150.00 CR"),
        ("05 Oct 2026", "Northline Rail", "28.90"),
        ("08 Oct 2026", "Greenbasket Stores", "61.30"),
        ("12 Oct 2026", "Harbour Pharmacy refund", "6.15 CR"),
        ("14 Oct 2026", "Page and Spine Books", "12.99"),
    ],
    [
        ("18 Oct 2026", "Greenbasket Stores", "55.12"),
        ("21 Oct 2026", "City Cinema", "24.00"),
        ("24 Oct 2026", "Payment received - thank you", "40.00 CR"),
        ("26 Oct 2026", "Little Lantern Cafe", "9.40"),
        ("28 Oct 2026", "Interest", "4.80"),
    ],
]
CURRENT_ROWS = [
    ("01 Oct 2026", "Greenbasket Stores", "42.18", "", "957.82"),
    ("03 Oct 2026", "Home Cover Ltd", "48.20", "", "909.62"),
    ("05 Oct 2026", "Little Cafe", "3.40", "", "906.22"),
    ("07 Oct 2026", "Cash machine", "50.00", "", "856.22"),
    ("12 Oct 2026", "City Water", "31.15", "", "825.07"),
    ("17 Oct 2026", "Acme Payroll Ltd", "", "1,650.00", "2,475.07"),
    ("20 Oct 2026", "Northline Rail", "28.90", "", "2,446.17"),
    ("25 Oct 2026", "Harbour Pharmacy refund", "", "6.15", "2,452.32"),
    ("28 Oct 2026", "Pat Example", "200.00", "", "2,252.32"),
]
SCREENSHOT_ROWS = [
    ("Mon 5 Oct", "Little Cafe", "-£3.40"),
    ("Mon 5 Oct", "Greenbasket Stores", "-£24.60"),
    ("Tue 6 Oct", "Northline Rail", "-£12.80"),
    ("Wed 7 Oct", "Acme Payroll Ltd", "+£250.00"),
    ("Thu 8 Oct", "Harbour Pharmacy", "-£7.99"),
]


def _address(c: canvas.Canvas, y: float) -> float:
    for line in ("Alex Example", "1 Example Road", "Exampletown", "EX1 2MP"):
        c.drawString(56, y, line)
        y -= 14
    return y


def card_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for page_no, rows in enumerate(CARD_PAGES, start=1):
        c.setFont("Helvetica-Bold", 18)
        c.drawString(56, 790, "Card statement")
        c.setFont("Helvetica", 10)
        c.drawRightString(540, 790, f"Page {page_no} of {len(CARD_PAGES)}")
        c.drawRightString(540, 776, "Card ending 4242")
        y = 740
        if page_no == 1:
            y = _address(c, y) - 10
            c.drawString(56, y, "Statement for 29 Sep 2026 to 28 Oct 2026")
            y -= 20
            for label, value in (
                ("Previous balance", "£842.16"),
                ("Payments", "£190.00"),
                ("Refunds", "£6.15"),
                ("New purchases", "£259.04"),
                ("Interest", "£4.80"),
                ("New balance", "£909.85"),
                ("Minimum payment", "£25.00"),
                ("Payment due", "20 Nov 2026"),
                ("Credit limit", "£3,000.00"),
            ):
                c.drawString(56, y, label)
                c.drawRightString(300, y, value)
                y -= 14
            y -= 10
        c.setFont("Helvetica-Bold", 10)
        c.drawString(56, y, "Date")
        c.drawString(150, y, "Description")
        c.drawRightString(540, y, "Amount")
        c.setFont("Helvetica", 10)
        y -= 16
        for day, what, amount in rows:
            c.drawString(56, y, day)
            c.drawString(150, y, what)
            c.drawRightString(540, y, amount)
            y -= 15
        c.setFont("Helvetica", 8)
        c.drawString(56, 60, "Barclaycard is a trading name of Barclays Bank UK PLC.")
        c.drawString(56, 48, WATERMARK)
        c.showPage()
    c.save()
    return buf.getvalue()


def current_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(56, 790, "Current account statement")
    c.setFont("Helvetica", 10)
    y = _address(c, 750) - 10
    c.drawString(56, y, "Account number 12345678")
    c.drawString(56, y - 14, "Statement period 01/10/2026 to 31/10/2026")
    c.drawString(56, y - 28, "Opening balance £1,000.00")
    c.drawString(56, y - 42, "Closing balance £2,252.32")
    y -= 70
    c.setFont("Helvetica-Bold", 10)
    for x, label, right in (
        (56, "Date", False),
        (140, "Description", False),
        (400, "Paid out", True),
        (470, "Paid in", True),
        (540, "Balance", True),
    ):
        (c.drawRightString if right else c.drawString)(x, y, label)
    c.setFont("Helvetica", 10)
    y -= 16
    c.drawString(140, y, "Balance brought forward")
    c.drawRightString(540, y, "1,000.00")
    y -= 15
    for day, what, out, paid_in, balance in CURRENT_ROWS:
        c.drawString(56, y, day)
        c.drawString(140, y, what)
        if out:
            c.drawRightString(400, y, out)
        if paid_in:
            c.drawRightString(470, y, paid_in)
        c.drawRightString(540, y, balance)
        y -= 15
    c.setFont("Helvetica", 8)
    c.drawString(56, 60, "Nationwide Building Society. This is a synthetic example statement.")
    c.drawString(56, 48, WATERMARK)
    c.save()
    return buf.getvalue()


def scanned(pdf: bytes, dpi: int = 150) -> bytes:
    doc = pdfium.PdfDocument(pdf)
    pages = [doc[i].render(scale=dpi / 72).to_pil().convert("L") for i in range(len(doc))]
    out = io.BytesIO()
    pages[0].save(out, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])
    return out.getvalue()


def screenshot_png() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(390, 520), invariant=1)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(20, 480, "Transactions")
    y = 440
    c.setFont("Helvetica", 13)
    for day, what, amount in SCREENSHOT_ROWS:
        c.drawString(20, y, day)
        c.drawString(110, y, what)
        c.drawRightString(370, y, amount)
        y -= 34
    c.setFont("Helvetica", 9)
    c.drawString(20, 30, WATERMARK)
    c.save()
    page = pdfium.PdfDocument(buf.getvalue())[0]
    image = page.render(scale=2).to_pil()
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def statement_xlsx() -> bytes:
    import datetime

    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Statement"
    sheet.append(["Example Bank statement export"])
    sheet.append([])
    sheet.append(["Date", "Description", "Money in", "Money out", "Balance"])
    for row in (
        (datetime.datetime(2026, 10, 1), "GREENBASKET STORES", None, 42.18, 957.82),
        (datetime.datetime(2026, 10, 17), "ACME PAYROLL LTD", 1650, None, 2607.82),
        (datetime.datetime(2026, 10, 20), "NORTHLINE RAIL", None, 28.9, 2578.92),
    ):
        sheet.append(list(row))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def main() -> None:
    (OUT / "pdf").mkdir(parents=True, exist_ok=True)
    (OUT / "image").mkdir(parents=True, exist_ok=True)
    (OUT / "xlsx").mkdir(parents=True, exist_ok=True)
    (OUT / "xlsx" / "statement.xlsx").write_bytes(statement_xlsx())
    card = card_pdf()
    (OUT / "pdf" / "card-text.pdf").write_bytes(card)
    (OUT / "pdf" / "card-scanned.pdf").write_bytes(scanned(card))
    (OUT / "pdf" / "current-text.pdf").write_bytes(current_pdf())
    (OUT / "image" / "app-screenshot.png").write_bytes(screenshot_png())
    print(
        "wrote",
        sorted(
            str(p.relative_to(OUT)) for p in OUT.rglob("*") if p.suffix in {".pdf", ".png", ".xlsx"}
        ),
    )


if __name__ == "__main__":
    main()
