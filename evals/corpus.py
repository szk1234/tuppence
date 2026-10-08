"""The synthetic statement corpus and what each file should give (spec §6.3).

Every file lives in tests/fixtures/statements/ and is invented. Expected rows are
written out here independently of the fixtures, as (date, pence, description).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from tuppence.ingest.models import AccountKind, FileKind

Row = tuple[date, int, str]


def d(day: int, month: int = 10) -> date:
    return date(2026, month, day)


CURRENT: list[Row] = [
    (d(1), -4218, "Greenbasket Stores"),
    (d(3), -4820, "Home Cover Ltd"),
    (d(5), -340, "Little Cafe"),
    (d(7), -5000, "Cash machine"),
    (d(12), -3115, "City Water"),
    (d(17), 165000, "Acme Payroll Ltd"),
    (d(20), -2890, "Northline Rail"),
    (d(25), 615, "Harbour Pharmacy refund"),
    (d(28), -20000, "Pat Example"),
]
CARD: list[Row] = [
    (d(2), -6420, "Greenbasket Stores"),
    (d(6), -415, "Little Cafe"),
    (d(9), -2890, "Northline Rail"),
    (d(12), 615, "Harbour Pharmacy refund"),
    (d(16), -240, "Interest charge"),
    (d(28), 15000, "Payment received"),
]
CARD_PDF: list[Row] = [
    (d(29, 9), -4218, "Greenbasket Stores"),
    (d(30, 9), -1840, "Northwind Books"),
    (d(1), -675, "Little Lantern Cafe"),
    (d(2), 15000, "Payment received"),
    (d(5), -2890, "Northline Rail"),
    (d(8), -6130, "Greenbasket Stores"),
    (d(12), 615, "Harbour Pharmacy refund"),
    (d(14), -1299, "Page and Spine Books"),
    (d(18), -5512, "Greenbasket Stores"),
    (d(21), -2400, "City Cinema"),
    (d(24), 4000, "Payment received"),
    (d(26), -940, "Little Lantern Cafe"),
    (d(28), -480, "Interest"),
]
SCREENSHOT: list[Row] = [
    (d(5), -340, "Little Cafe"),
    (d(5), -2460, "Greenbasket Stores"),
    (d(6), -1280, "Northline Rail"),
    (d(7), 25000, "Acme Payroll Ltd"),
    (d(8), -799, "Harbour Pharmacy"),
]
FIVE: list[Row] = [CURRENT[0], CURRENT[1], CURRENT[2], CURRENT[5], CURRENT[6]]


@dataclass(frozen=True)
class Case:
    id: str
    path: str  # relative to tests/fixtures/statements
    kind: FileKind
    account_kind: AccountKind
    expected: list[Row]
    balance_verified: bool
    needs_ai: bool = False
    importer: str = ""
    notes: str = field(default="", compare=False)


CASES: list[Case] = [
    Case("csv-monzo", "csv/monzo.csv", "csv", "current", CURRENT, False, importer="csv:monzo"),
    Case(
        "csv-starling", "csv/starling.csv", "csv", "current", CURRENT, True, importer="csv:starling"
    ),
    Case("csv-hsbc", "csv/hsbc.csv", "csv", "current", CURRENT, False, importer="csv:hsbc"),
    Case(
        "csv-barclays",
        "csv/barclays.csv",
        "csv",
        "current",
        CURRENT,
        False,
        importer="csv:barclays",
    ),
    Case(
        "csv-lloyds-halifax",
        "csv/lloyds-halifax.csv",
        "csv",
        "current",
        [*CURRENT, (d(17), -295, "Corner Cafe")],
        True,
        importer="csv:lloyds-halifax",
    ),
    Case("csv-natwest", "csv/natwest.csv", "csv", "current", CURRENT, True, importer="csv:natwest"),
    Case(
        "csv-santander",
        "csv/santander.csv",
        "csv",
        "current",
        CURRENT,
        True,
        importer="csv:santander",
    ),
    Case(
        "csv-nationwide",
        "csv/nationwide.csv",
        "csv",
        "current",
        CURRENT,
        True,
        importer="csv:nationwide",
    ),
    Case("csv-chase", "csv/chase.csv", "csv", "current", CURRENT, True, importer="csv:chase"),
    Case(
        "csv-revolut",
        "csv/revolut.csv",
        "csv",
        "current",
        [*CURRENT, (d(20), -50, "Northline Rail fee")],
        True,
        importer="csv:revolut",
    ),
    Case(
        "csv-amex",
        "csv/amex.csv",
        "csv",
        "credit_card",
        [r for r in CARD if r[2] != "Interest charge"],
        False,
        importer="csv:amex",
    ),
    Case(
        "csv-barclaycard",
        "csv/barclaycard.csv",
        "csv",
        "credit_card",
        CARD,
        False,
        importer="csv:barclaycard",
    ),
    Case("ofx-current", "ofx/current.ofx", "ofx", "current", FIVE, False, importer="ofx"),
    Case(
        "qfx-card",
        "ofx/card.qfx",
        "ofx",
        "credit_card",
        [
            (d(2), -6420, "Greenbasket Stores"),
            (d(6), -415, "Little Cafe"),
            (d(12), 615, "Harbour Pharmacy refund"),
            (d(28), 15000, "Payment received"),
        ],
        False,
        importer="ofx",
    ),
    Case(
        "qif-bank",
        "qif/bank.qif",
        "qif",
        "current",
        [CURRENT[0], CURRENT[1], CURRENT[5], CURRENT[7], CURRENT[8]],
        False,
        importer="qif",
    ),
    Case("camt-053", "camt/statement.xml", "camt053", "current", FIVE, True, importer="camt053"),
    Case(
        "xlsx",
        "xlsx/statement.xlsx",
        "xlsx",
        "current",
        [CURRENT[0], (d(17), 165000, "Acme Payroll Ltd"), (d(20), -2890, "Northline Rail")],
        True,
        importer="csv:santander",
    ),
    Case(
        "csv-unknown-layout",
        "csv-unknown/credit-union.csv",
        "csv",
        "current",
        [
            (d(2), -4218, "Greenbasket Stores"),
            (d(6), -340, "Little Cafe"),
            (d(15), 90000, "Acme Payroll Ltd"),
            (d(21), -3115, "City Water"),
        ],
        True,
        needs_ai=True,
    ),
    Case(
        "pdf-card-text",
        "pdf/card-text.pdf",
        "pdf",
        "credit_card",
        CARD_PDF,
        True,
        needs_ai=True,
        importer="ai-read",
    ),
    Case(
        "pdf-card-scanned",
        "pdf/card-scanned.pdf",
        "pdf",
        "credit_card",
        CARD_PDF,
        True,
        needs_ai=True,
        importer="ai-read",
    ),
    Case(
        "pdf-current-text",
        "pdf/current-text.pdf",
        "pdf",
        "current",
        CURRENT,
        True,
        needs_ai=True,
        importer="ai-read",
    ),
    Case(
        "image-screenshot",
        "image/app-screenshot.png",
        "image",
        "current",
        SCREENSHOT,
        False,
        needs_ai=True,
        importer="ai-read",
    ),
]
