"""ISO 20022 CAMT.053 bank-to-customer statements, read with defusedxml."""

from __future__ import annotations

from datetime import date
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException, ElementTree

from tuppence.core.errors import UserFacing
from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement, SkippedLine
from tuppence.ingest.textnum import parse_date, parse_money, to_pence


class CamtError(UserFacing, ValueError):
    pass


def _statements(data: bytes) -> list[Element]:
    """Every <Stmt>, oldest first. One account only: the document model holds one."""
    try:
        root = ElementTree.fromstring(data)
    except DefusedXmlException:
        raise CamtError(
            "This XML file uses features a bank statement never needs, so it wasn't read."
        ) from None
    except (ElementTree.ParseError, LookupError):
        raise CamtError("This CAMT.053 file isn't well-formed XML, so it can't be read.") from None
    stmts = root.findall(".//{*}Stmt")
    if not stmts:
        raise CamtError("This XML file has no CAMT.053 statement in it.")
    if len({_account(st) for st in stmts}) > 1:
        raise CamtError(
            "This file has statements for more than one account. Export one account at a time."
        )
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


def camt_document(data: bytes, *, sha256: str) -> Document:
    stmts = _statements(data)
    iban, bic = _account(stmts[0])
    lines = [Line(ref="H1", text=f"Account {iban} {bic}".strip())]
    entries = _entries(stmts)
    for e in entries:
        lines.append(
            Line(
                ref=e["ref"],
                text=" | ".join(
                    p
                    for p in (e["date"], e["amount_text"], e["name"], e["details"], e["status"])
                    if p
                ),
            )
        )
    return Document(
        kind="camt053",
        sha256=sha256,
        lines=lines,
        preamble_refs=["H1"],
        data_refs=[e["ref"] for e in entries],
        meta={"iban": iban, "bic": bic},
    )


def parse_camt(data: bytes) -> ParsedStatement:
    stmts = _statements(data)
    first, last = stmts[0], stmts[-1]
    parsed = ParsedStatement(
        importer="camt053",
        perspective="household",
        currency=_text(first, "{*}Acct/{*}Ccy") or "GBP",
        period_start=_day(first, "{*}FrToDt/{*}FrDtTm"),
        period_end=_day(last, "{*}FrToDt/{*}ToDtTm"),
    )
    for bal in first.findall("{*}Bal"):
        code = _text(bal, "{*}Tp/{*}CdOrPrtry/{*}Cd")
        value = parse_money(_text(bal, "{*}Amt"))
        if value is None:
            continue
        pence = to_pence(value) * (-1 if _text(bal, "{*}CdtDbtInd") == "DBIT" else 1)
        if code in {"OPBD", "PRCD"} and parsed.opening_balance_pence is None:
            parsed.opening_balance_pence = pence
        elif code == "CLBD" and first is last:
            parsed.closing_balance_pence = pence
    if first is not last:
        for bal in last.findall("{*}Bal"):
            value = parse_money(_text(bal, "{*}Amt"))
            if value is not None and _text(bal, "{*}Tp/{*}CdOrPrtry/{*}Cd") == "CLBD":
                parsed.closing_balance_pence = to_pence(value) * (
                    -1 if _text(bal, "{*}CdtDbtInd") == "DBIT" else 1
                )
    for e in _entries(stmts):
        if e["status"] and e["status"] != "BOOK":
            parsed.skipped.append(SkippedLine(ref=e["ref"], reason="not booked yet (pending)"))
            continue
        amount = parse_money(e["amount_text"])
        if not e["date"] or amount is None:
            continue
        parsed.rows.append(
            ParsedRow(
                ref=e["ref"],
                date=date.fromisoformat(e["date"]),
                amount_pence=to_pence(amount),
                amount_text=e["amount_text"],
                raw_description=" ".join(p for p in (e["name"], e["details"]) if p)
                or "(no description)",
                merchant=e["name"] or None,
            )
        )
    if parsed.period_start is None and parsed.rows:
        parsed.period_start = min(r.date for r in parsed.rows)
    if parsed.period_end is None and parsed.rows:
        parsed.period_end = max(r.date for r in parsed.rows)
    return parsed
