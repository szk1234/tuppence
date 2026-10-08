"""Shapes shared by every ingestion step (spec §6.2). Plain data, no I/O."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FileKind = Literal["csv", "text", "ofx", "qif", "camt053", "xlsx", "pdf", "image"]
AccountKind = Literal["current", "savings", "credit_card"]
Perspective = Literal["household", "card"]
CheckLevel = Literal["full", "screenshot"]
StatementStatus = Literal[
    "received", "identifying", "needs_account", "parsing", "needs_review", "imported", "failed"
]


class Line(BaseModel):
    """One numbered line of a statement: `L12` (files) or `P2L4` (page 2, line 4)."""

    model_config = ConfigDict(frozen=True)
    ref: str
    text: str


class MaskedLine(BaseModel):
    """A line as the AI reader is sent it: each account detail replaced by a placeholder such as
    `[hidden-a]`, and what each placeholder stands for (kept on this device, to put the details
    back into the descriptions the reader copies)."""

    text: str
    hidden: dict[str, str] = Field(default_factory=dict)


class Document(BaseModel):
    """A statement turned into numbered lines, ready to identify and parse."""

    kind: FileKind
    sha256: str
    lines: list[Line]
    preamble_refs: list[str] = Field(
        default_factory=list
    )  # before the table: identity, never sent to AI
    header_refs: list[str] = Field(default_factory=list)  # column headings: not data
    data_refs: list[str] = Field(default_factory=list)  # every one must become a row or a skip
    # withheld lines with an amount that may be a transaction: reported, never lost silently
    held_amount_refs: list[str] = Field(default_factory=list)
    # lines too long to read safely (their text is cut short): left out, reported, and listed
    # in held_amount_refs too, so the person can say what each one is
    too_long_refs: list[str] = Field(default_factory=list)
    # data lines sent with their account details masked in place (ref -> what is sent)
    masked: dict[str, MaskedLine] = Field(default_factory=dict)
    table: list[list[str]] | None = None  # CSV/XLSX cells for each line in `lines`, same order
    meta: dict[str, str] = Field(default_factory=dict)  # e.g. OFX BANKID/ACCTID, CAMT IBAN/BIC
    pages: int = 0
    ocr_pages: list[int] = Field(default_factory=list)
    ocr_confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)

    def by_ref(self) -> dict[str, Line]:
        return {line.ref: line for line in self.lines}


class ParsedRow(BaseModel):
    ref: str
    date: dt.date
    amount_pence: int  # household perspective: money in positive, money out negative
    amount_text: str  # copied from the line, exactly as printed
    sign_from: str | None = None  # a column heading or label that gives the sign
    raw_description: str
    merchant: str | None = None
    bank_category: str | None = None
    bank_type: str | None = None
    balance_after_pence: int | None = None  # as printed on the line
    edited: bool = False  # changed by the person on the fix-up screen


class SkippedLine(BaseModel):
    ref: str
    reason: str


class ParsedStatement(BaseModel):
    importer: str
    perspective: Perspective = "household"  # "card": the file prints purchases positive
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    opening_balance_pence: int | None = None  # as printed (a card prints what is owed as positive)
    closing_balance_pence: int | None = None
    currency: str = "GBP"
    rows: list[ParsedRow] = Field(default_factory=list)
    skipped: list[SkippedLine] = Field(default_factory=list)
