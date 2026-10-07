"""ISO 20022 CAMT.053 bank-to-customer statements, read with defusedxml."""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass
from datetime import date
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException, ElementTree

from tuppence.core.errors import UserFacing
from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement, SkippedLine
from tuppence.ingest.refusals import Refusal
from tuppence.ingest.textnum import parse_date, parse_money, to_pence

MAX_BYTES = 50 * 1024 * 1024
MAX_DEPTH = 100
MAX_ELEMENTS = 1_000_000


class CamtError(UserFacing, ValueError):
    pass


class CamtForbiddenXml(Refusal, CamtError):
    message = "This XML file uses features a bank statement never needs, so it wasn't read."


class CamtNotWellFormed(Refusal, CamtError):
    message = "This CAMT.053 file isn't well-formed XML, so it can't be read."


class CamtNoStatement(Refusal, CamtError):
    message = "This XML file has no CAMT.053 statement in it."


class CamtManyAccounts(Refusal, CamtError):
    message = "This file has statements for more than one account. Export one account at a time."


class CamtTooDeep(Refusal, CamtError):
    message = "This XML file is nested too deeply for a bank statement, so it wasn't read."


class CamtTooLarge(Refusal, CamtError):
    message = "This CAMT.053 file is too large to read safely."


def _parse(data: bytes) -> Element:
    """The XML tree, built incrementally so a hostile file is refused as soon as it is too
    deep or too big, before it can use much memory."""
    if len(data) > MAX_BYTES:
        raise CamtTooLarge
    depth = elements = 0
    root: Element | None = None
    try:
        for event, el in ElementTree.iterparse(io.BytesIO(data), events=("start", "end")):
            if event == "end":
                depth -= 1
                continue
            depth += 1
            elements += 1
            if depth > MAX_DEPTH:
                raise CamtTooDeep
            if elements > MAX_ELEMENTS:
                raise CamtTooLarge
            if root is None:
                root = el
    except DefusedXmlException:
        raise CamtForbiddenXml from None
    except (ElementTree.ParseError, LookupError):
        raise CamtNotWellFormed from None
    if root is None:
        raise CamtNotWellFormed
    return root


def _statements(data: bytes) -> list[Element]:
    """Every <Stmt>, oldest first. One account only: the document model holds one."""
    root = _parse(data)
    stmts = root.findall(".//{*}Stmt")
    if not stmts:
        raise CamtNoStatement
    if len({_account(st) for st in stmts}) > 1:
        raise CamtManyAccounts
    return sorted(stmts, key=lambda st: _day(st, "{*}FrToDt/{*}FrDtTm") or date.min)


def _account(stmt: Element) -> tuple[str, str]:
    iban = _text(stmt, "{*}Acct/{*}Id/{*}IBAN") or _text(stmt, "{*}Acct/{*}Id/{*}Othr/{*}Id")
    bic = _text(stmt, "{*}Acct/{*}Svcr/{*}FinInstnId/{*}BICFI") or _text(
        stmt, "{*}Acct/{*}Svcr/{*}FinInstnId/{*}BIC"
    )
    return iban, bic


def _entries(stmts: list[Element]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for stmt in stmts:
        out.extend(_stmt_entries(stmt, len(out)))
    return out


def _text(el: Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return (found.text or "").strip() if found is not None else ""


def _day(el: Element, *paths: str) -> date | None:
    for path in paths:
        value = _text(el, path)
        if value:
            return parse_date(value[:10], ("%Y-%m-%d",))
    return None


def _stmt_entries(stmt: Element, offset: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for n, entry in enumerate(stmt.findall("{*}Ntry"), start=offset + 1):
        debit = _text(entry, "{*}CdtDbtInd") == "DBIT"
        reversal = _text(entry, "{*}RvslInd").lower() == "true"
        party = "{*}Cdtr" if debit else "{*}Dbtr"
        name = _text(entry, f".//{{*}}RltdPties/{party}/{{*}}Nm") or _text(
            entry, f".//{{*}}RltdPties/{party}/{{*}}Pty/{{*}}Nm"
        )
        ustrd = " ".join((u.text or "").strip() for u in entry.findall(".//{*}RmtInf/{*}Ustrd"))
        amount = _text(entry, "{*}Amt")
        day = _day(entry, "{*}BookgDt/{*}Dt", "{*}BookgDt/{*}DtTm", "{*}ValDt/{*}Dt")
        out.append(
            {
                "ref": f"L{n}",
                "date": day.isoformat() if day else "",
                "amount_text": f"-{amount}" if debit != reversal else amount,
                "status": _text(entry, "{*}Sts/{*}Cd") or _text(entry, "{*}Sts"),
                "name": name,
                "details": " ".join(p for p in (ustrd, _text(entry, "{*}AddtlNtryInf")) if p),
            }
        )
    return out


@dataclass(frozen=True)
class CamtEntry:
    ref: str
    date: str  # ISO, or ""
    amount_text: str
    status: str
    name: str
    details: str


@dataclass(frozen=True)
class CamtFacts:
    """Everything either consumer needs from the XML, as plain data. The XML itself is only
    ever read inside the sandbox; this is what comes back."""

    iban: str
    bic: str
    currency: str
    period_start: str  # ISO, or ""
    period_end: str  # ISO, or ""
    opening_pence: int | None
    closing_pence: int | None
    entries: list[CamtEntry]


def _signed(bal: Element) -> int | None:
    value = parse_money(_text(bal, "{*}Amt"))
    if value is None:
        return None
    return to_pence(value) * (-1 if _text(bal, "{*}CdtDbtInd") == "DBIT" else 1)


def camt_facts(data: bytes) -> CamtFacts:
    stmts = _statements(data)
    first, last = stmts[0], stmts[-1]
    iban, bic = _account(first)
    opening = closing = None
    for bal in first.findall("{*}Bal"):
        code = _text(bal, "{*}Tp/{*}CdOrPrtry/{*}Cd")
        pence = _signed(bal)
        if pence is None:
            continue
        if code in {"OPBD", "PRCD"} and opening is None:
            opening = pence
        elif code == "CLBD" and first is last:
            closing = pence
    if first is not last:
        for bal in last.findall("{*}Bal"):
            pence = _signed(bal)
            if pence is not None and _text(bal, "{*}Tp/{*}CdOrPrtry/{*}Cd") == "CLBD":
                closing = pence
    start = _day(first, "{*}FrToDt/{*}FrDtTm")
    end = _day(last, "{*}FrToDt/{*}ToDtTm")
    return CamtFacts(
        iban=iban,
        bic=bic,
        currency=_text(first, "{*}Acct/{*}Ccy") or "GBP",
        period_start=start.isoformat() if start else "",
        period_end=end.isoformat() if end else "",
        opening_pence=opening,
        closing_pence=closing,
        entries=[CamtEntry(**e) for e in _entries(stmts)],
    )


def read_camt_facts(path: str) -> dict[str, object]:
    """Sandbox entry point: the file's facts as JSON-ready data."""
    with open(path, "rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    return asdict(camt_facts(data))


def document_from_facts(facts: CamtFacts, *, sha256: str) -> Document:
    lines = [Line(ref="H1", text=f"Account {facts.iban} {facts.bic}".strip())]
    for e in facts.entries:
        lines.append(
            Line(
                ref=e.ref,
                text=" | ".join(
                    p for p in (e.date, e.amount_text, e.name, e.details, e.status) if p
                ),
            )
        )
    return Document(
        kind="camt053",
        sha256=sha256,
        lines=lines,
        preamble_refs=["H1"],
        data_refs=[e.ref for e in facts.entries],
        meta={"iban": facts.iban, "bic": facts.bic},
    )


def parsed_from_facts(facts: CamtFacts) -> ParsedStatement:
    parsed = ParsedStatement(
        importer="camt053",
        perspective="household",
        currency=facts.currency,
        period_start=date.fromisoformat(facts.period_start) if facts.period_start else None,
        period_end=date.fromisoformat(facts.period_end) if facts.period_end else None,
        opening_balance_pence=facts.opening_pence,
        closing_balance_pence=facts.closing_pence,
    )
    for e in facts.entries:
        if e.status and e.status != "BOOK":
            parsed.skipped.append(SkippedLine(ref=e.ref, reason="not booked yet (pending)"))
            continue
        amount = parse_money(e.amount_text)
        if not e.date or amount is None:
            continue
        parsed.rows.append(
            ParsedRow(
                ref=e.ref,
                date=date.fromisoformat(e.date),
                amount_pence=to_pence(amount),
                amount_text=e.amount_text,
                raw_description=" ".join(p for p in (e.name, e.details) if p) or "(no description)",
                merchant=e.name or None,
            )
        )
    if parsed.period_start is None and parsed.rows:
        parsed.period_start = min(r.date for r in parsed.rows)
    if parsed.period_end is None and parsed.rows:
        parsed.period_end = max(r.date for r in parsed.rows)
    return parsed


def camt_document(data: bytes, *, sha256: str) -> Document:
    """In-process (for trusted bytes and tests). Files from people go through
    `extract.read_camt` instead, which parses the XML in the sandbox."""
    return document_from_facts(camt_facts(data), sha256=sha256)


def parse_camt(data: bytes) -> ParsedStatement:
    """In-process, as `camt_document`."""
    return parsed_from_facts(camt_facts(data))
