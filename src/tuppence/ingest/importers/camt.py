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


def _statement(data: bytes) -> Element:
    try:
        root = ElementTree.fromstring(data)
    except DefusedXmlException:
        raise CamtError(
            "This XML file uses features a bank statement never needs, so it wasn't read."
        ) from None
    except ElementTree.ParseError:
        raise CamtError("This CAMT.053 file isn't well-formed XML, so it can't be read.") from None
    stmt = root.find(".//{*}Stmt")
    if stmt is None:
        raise CamtError("This XML file has no CAMT.053 statement in it.")
    return stmt


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


def _entries(stmt: Element) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for n, entry in enumerate(stmt.findall("{*}Ntry"), start=1):
        debit = _text(entry, "{*}CdtDbtInd") == "DBIT"
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
                "amount_text": f"-{amount}" if debit else amount,
                "status": _text(entry, "{*}Sts/{*}Cd") or _text(entry, "{*}Sts"),
                "name": name,
                "details": " ".join(p for p in (ustrd, _text(entry, "{*}AddtlNtryInf")) if p),
            }
        )
    return out


def camt_document(data: bytes, *, sha256: str) -> Document:
    stmt = _statement(data)
    iban = _text(stmt, "{*}Acct/{*}Id/{*}IBAN") or _text(stmt, "{*}Acct/{*}Id/{*}Othr/{*}Id")
    bic = _text(stmt, "{*}Acct/{*}Svcr/{*}FinInstnId/{*}BICFI") or _text(
        stmt, "{*}Acct/{*}Svcr/{*}FinInstnId/{*}BIC"
    )
    lines = [Line(ref="H1", text=f"Account {iban} {bic}".strip())]
    entries = _entries(stmt)
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
    stmt = _statement(data)
    parsed = ParsedStatement(
        importer="camt053",
        perspective="household",
        currency=_text(stmt, "{*}Acct/{*}Ccy") or "GBP",
        period_start=_day(stmt, "{*}FrToDt/{*}FrDtTm"),
        period_end=_day(stmt, "{*}FrToDt/{*}ToDtTm"),
    )
    for bal in stmt.findall("{*}Bal"):
        code = _text(bal, "{*}Tp/{*}CdOrPrtry/{*}Cd")
        value = parse_money(_text(bal, "{*}Amt"))
        if value is None:
            continue
        pence = to_pence(value) * (-1 if _text(bal, "{*}CdtDbtInd") == "DBIT" else 1)
        if code in {"OPBD", "PRCD"} and parsed.opening_balance_pence is None:
            parsed.opening_balance_pence = pence
        elif code == "CLBD":
            parsed.closing_balance_pence = pence
    for e in _entries(stmt):
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
