"""Adversarial synthetic statements for the end-to-end privacy tests: every identity detail in
many spellings, dated balance and payment-due lines above the address, balance lines, repeated
page headers, and transaction lines that carry account details. Everything is invented and
every page carries the synthetic-statement watermark."""

from __future__ import annotations

import io

import openpyxl
import pypdfium2 as pdfium
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

WATERMARK = "SYNTHETIC TEST STATEMENT - NOT A REAL DOCUMENT"
NAMES = ["Alex Example", "Pat Example"]
# Never in any request, in any mode: identity details, address lines, balances and limits (the
# table's own "Balance brought forward" row included).
SECRETS = [
    "Alex", "ALEX", "Pat Example", "PAT EXAMPLE", "A EXAMPLE", "Example Road", "Example House",
    "Flat 3", "Exampletown", "EX1 2MP", "EX1", "12345678", "1234 5678", "07-12-34", "07 12 34",
    "071234", "GB29", "NWBK", "60161331926819", "6016 1331 9268 19", "9876543", "1234/56789",
    "4242", "Available balance", "Opening balance", "Closing balance", "overdraft limit",
    "Credit limit", "7,777.77", "8,888.88", "6,666.66", "Roll number", "Customer",
    "Sort code", "IBAN", "Account number", "Exampleshire", "Payment due", "minimum payment",
    "25.00", "2,252.32", "20-11-33", "41234567", "5,555.55",
]  # fmt: skip


def _pages(pages: list[list[list[tuple[int, str, bool]]]]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for lines in pages:
        y = 810
        c.setFont("Helvetica", 9)
        for cells in lines:
            for x, text, right in cells:
                (c.drawRightString if right else c.drawString)(x, y, text)
            y -= 13
        c.drawString(56, 30, WATERMARK)
        c.showPage()
    c.save()
    return buf.getvalue()


def _line(*cells: str | tuple[int, str, bool]) -> list[tuple[int, str, bool]]:
    return [(56, cell, False) if isinstance(cell, str) else cell for cell in cells]


HEAD = _line(
    (56, "Date", False),
    (140, "Description", False),
    (400, "Paid out", True),
    (470, "Paid in", True),
    (540, "Balance", True),
)


def _row(day: str, what: str, out: str = "", paid_in: str = "", balance: str = ""):
    cells = [(56, day, False), (140, what, False)]
    for x, value in ((400, out), (470, paid_in), (540, balance)):
        if value:
            cells.append((x, value, True))
    return cells


def current_pdf() -> bytes:
    page1 = [
        _line("Example Building Society"),
        _line("Balance on 31/10/2026 £2,252.32"),
        _line("MR ALEX EXAMPLE & MRS PAT EXAMPLE"),
        _line("Flat 3, Example House"),
        _line("10 Example Road"),
        _line("Exampletown"),
        _line("Exampleshire EX1 2MP"),
        _line("Account number: 12345678     Sort code: 07 12 34"),
        _line("IBAN: GB29 NWBK 6016 1331 9268 19   BIC: NWBKGB2L"),
        _line("Roll number 1234/56789   Customer number 9876543"),
        _line("Visa debit card ending 4242"),
        _line("Statement period 01/10/2026 to 31/10/2026"),
        _line("Opening balance on 01/10/2026 £5,555.55"),
        _line("Money in £1,650.00    Money out £438.78"),
        _line("Closing balance £6,766.77"),
        _line("Available balance £7,777.77"),
        _line("Arranged overdraft limit £500.00"),
        HEAD,
        _row("01 Oct 2026", "Balance brought forward", balance="5,555.55"),
        _row("01 Oct 2026", "Greenbasket Stores", out="42.18", balance="5,513.37"),
        _row("03 Oct 2026", "Home Cover Ltd", out="48.20", balance="5,465.17"),
        _row("05 Oct 2026", "Little Cafe", out="3.40", balance="5,461.77"),
        _row("07 Oct 2026", "Cash machine", out="50.00", balance="5,411.77"),
        _line("Alex Example"),
        _line("Page 1 of 2"),
    ]
    page2 = [
        _line("Example Building Society"),
        _line("ALEX EXAMPLE"),
        _line("Account 12345678  Sort code 07-12-34"),
        _line("Statement period 01/10/2026 to 31/10/2026"),
        _line("Your balance £8,888.88"),
        HEAD,
        _row("12 Oct 2026", "City Water", out="31.15", balance="5,380.62"),
        _row("17 Oct 2026", "Acme Payroll Ltd", paid_in="1,650.00", balance="7,030.62"),
        _row("20 Oct 2026", "Northline Rail", out="28.90", balance="7,001.72"),
        _row("25 Oct 2026", "Harbour Pharmacy refund", paid_in="6.15", balance="7,007.87"),
        # rows that carry a payee's sort code and account number: sent with them masked
        _row("26 Oct 2026", "FPO J SMITH 20-11-33 41234567", out="100.00", balance="6,907.87"),
        _row(
            "27 Oct 2026", "FPI J SMITH 20-11-33 41234567 RTN", paid_in="100.00", balance="7,007.87"
        ),  # fmt: skip
        _row("28 Oct 2026", "Greenbasket Stores", out="241.10", balance="6,766.77"),
        _line("Closing balance £6,766.77"),
        _line("Alex Example"),
        _line("Page 2 of 2"),
    ]
    return _pages([page1, page2])


CARD_COLUMNS = _line((56, "Date", False), (150, "Description", False), (540, "Amount", True))


def card_pdf() -> bytes:
    page = [
        _line("Card statement"),
        _line("Payment due by 20/11/2026: minimum payment £25.00"),
        _line("MR ALEX EXAMPLE"),
        _line("Flat 3"),
        _line("Example House"),
        _line("Exampletown"),
        _line("EX1 2MP"),
        _line("Card number **** **** **** 4242"),
        _line("Statement for 29 Sep 2026 to 28 Oct 2026"),
        _line("Previous balance £6,666.66"),
        _line("New balance £6,700.84"),
        _line("Credit limit £3,000.00"),
        CARD_COLUMNS,
        _line((56, "29 Sep 2026", False), (150, "Greenbasket Stores", False), (540, "42.18", True)),
        _line(
            (56, "02 Oct 2026", False),
            (150, "Payment received - thank you", False),
            (540, "40.00 CR", True),
        ),
        _line((56, "05 Oct 2026", False), (150, "Northline Rail", False), (540, "32.00", True)),
    ]
    return _pages([page])


def scanned(pdf: bytes, dpi: int = 150) -> bytes:
    doc = pdfium.PdfDocument(pdf)
    pages = [doc[i].render(scale=dpi / 72).to_pil().convert("L") for i in range(len(doc))]
    out = io.BytesIO()
    pages[0].save(out, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])
    return out.getvalue()


def screenshot_png() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(390, 620), invariant=1)
    c.setFont("Helvetica-Bold", 16)
    y = 590
    for text in (
        "Alex Example",
        "Current Account",
        "07-12-34  12345678",
        "£7,777.77",
        "Available balance",
        "Transactions",
    ):
        c.drawString(20, y, text)
        y -= 30
    c.setFont("Helvetica", 13)
    for day, what, amount in (
        ("Mon 5 Oct", "Little Cafe", "-£3.40"),
        ("Tue 6 Oct", "Northline Rail", "-£12.80"),
        ("Wed 7 Oct", "Acme Payroll Ltd", "+£250.00"),
    ):
        c.drawString(20, y, day)
        c.drawString(110, y, what)
        c.drawRightString(370, y, amount)
        y -= 34
    c.setFont("Helvetica", 9)
    c.drawString(20, 20, WATERMARK)
    c.save()
    image = pdfium.PdfDocument(buf.getvalue())[0].render(scale=2).to_pil()
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


CSV = (
    "Account Name:,Alex Example\n"
    "Account Number:,12345678\n"
    "Sort Code:,07-12-34\n"
    "IBAN:,GB29NWBK60161331926819\n"
    "Available Balance:,£7,777.77\n"
    "\n"
    "Posted,Narrative,Debit,Credit,Running total,Payee account\n"
    "02/10/2026,TRANSFER TO PAT EXAMPLE,42.18,,457.82,12345678\n"
    "06/10/2026,LITTLE CAFE,3.40,,454.42,07-12-34 87654321\n"
    "15/10/2026,ACME PAYROLL LTD,,900.00,1354.42,\n"
    "21/10/2026,CITY WATER,31.15,,1323.27,\n"
)


def statement_xlsx() -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for row in (
        ["Account holder", "Alex Example"],
        ["Account number", "12345678"],
        ["Sort code", "07-12-34"],
        [],
        ["Posted", "Narrative", "Debit", "Credit", "Running total"],
        ["02/10/2026", "TRANSFER TO PAT EXAMPLE 12345678", 42.18, None, 457.82],
        ["06/10/2026", "LITTLE CAFE", 3.40, None, 454.42],
        ["15/10/2026", "ACME PAYROLL LTD", None, 900.00, 1354.42],
    ):
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


# What a vision model reads off the screenshot and the scanned pages: the whole page, header
# included. Text prep must hold the header back exactly as it does for a text layer.
VISION_SCREENSHOT = [
    "Alex Example", "Current Account", "07-12-34  12345678", "£7,777.77", "Available balance",
    "Transactions", "Mon 5 Oct   Little Cafe   -£3.40", "Tue 6 Oct   Northline Rail   -£12.80",
    "Wed 7 Oct   Acme Payroll Ltd   +£250.00",
    # a row whose details a PDF or OCR printed with invisible and non-breaking characters
    "Thu 8 Oct   FPO J SMITH 20\u200b-11-33\u00a04123\u200b4567   -£5.00",
]  # fmt: skip
VISION_PAGES = [
    [
        "Example Building Society", "Balance on 31/10/2026 £2,252.32",
        "MR ALEX EXAMPLE & MRS PAT EXAMPLE", "Flat 3, Example House", "10 Example Road",
        "Exampletown", "Exampleshire EX1 2MP", "Account number: 12345678 Sort code: 07 12 34",
        "Statement period 01/10/2026 to 31/10/2026", "Opening balance £5,555.55",
        "Closing balance £6,766.77", "Available balance £7,777.77",
        "Date   Description   Paid out   Paid in   Balance",
        "01 Oct 2026   Greenbasket Stores   42.18   5,513.37",
        "03 Oct 2026   Home Cover Ltd   48.20   5,465.17",
        "05 Oct 2026   Little Cafe   3.40   5,461.77",
        "07 Oct 2026   Cash machine   50.00   5,411.77", "Alex Example", "Page 1 of 2",
    ],
    [
        "Example Building Society", "ALEX EXAMPLE", "Account 12345678  Sort code 07-12-34",
        "Your balance £8,888.88", "Date   Description   Paid out   Paid in   Balance",
        "12 Oct 2026   City Water   31.15   5,380.62",
        "17 Oct 2026   Acme Payroll Ltd   1,650.00   7,030.62",
        "20 Oct 2026   Northline Rail   28.90   7,001.72",
        "25 Oct 2026   Harbour Pharmacy refund   6.15   7,007.87",
        "26 Oct 2026   FPO J SMITH 20-11-33 41234567   100.00   6,907.87",
        "27 Oct 2026   FPI J SMITH 20-11-33 41234567 RTN   100.00   7,007.87",
        "28 Oct 2026   Greenbasket Stores   241.10   6,766.77", "Closing balance £6,766.77",
        "Alex Example", "Page 2 of 2",
    ],
]  # fmt: skip
