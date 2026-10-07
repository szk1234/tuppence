"""OFX and QFX (OFX 1.x SGML and 2.x XML) without an XML parser.

A small tolerant reader: aggregates such as <STMTTRN> are always closed, leaf
elements such as <TRNAMT>-42.18 may not be. No entities are expanded, so there is
nothing for a hostile file to exploit.
"""

from __future__ import annotations

import html
import re
from datetime import date

from tuppence.core.errors import UserFacing
from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement
from tuppence.ingest.textnum import parse_date, parse_money, to_pence

_LEAF = re.compile(r"<([A-Za-z0-9.]+)>([^<\r\n]*)")


class OfxError(UserFacing, ValueError):
    pass


MAX_CHARS = 10_000_000
MAX_TRANSACTIONS = 50_000


def _blocks(text: str, name: str) -> list[str]:
    """The text inside each <name>...</name>, in one pass over the file."""
    if len(text) > MAX_CHARS:
        raise OfxError("This OFX file is too large to read.")
    out: list[str] = []
    opened: int | None = None
    for m in re.finditer(rf"<(/?){re.escape(name)}>", text, re.IGNORECASE):
        if m.group(1):
            if opened is not None:
                out.append(text[opened : m.start()])
                opened = None
        elif opened is not None:
            raise OfxError(f"This OFX file has a <{name}> that is never closed.")
        else:
            opened = m.end()
    if opened is not None:
        raise OfxError(f"This OFX file has a <{name}> that is never closed.")
    if name.upper() == "STMTTRN" and len(out) > MAX_TRANSACTIONS:
        raise OfxError("This OFX file has too many transactions to read.")
    return out


def _leaves(block: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, value in _LEAF.findall(block):
        if value.strip():
            found.setdefault(name.upper(), html.unescape(value.strip()))
    return found


def _ofx_date(value: str | None) -> date | None:
    digits = re.match(r"\d{8}", value or "")
    return parse_date(digits.group(0), ("%Y%m%d",)) if digits else None


def ofx_document(text: str, *, sha256: str) -> Document:
    if "<OFX>" not in text.upper():
        raise OfxError("This doesn't look like an OFX file.")
    account = (_blocks(text, "BANKACCTFROM") or _blocks(text, "CCACCTFROM") or [""])[0]
    acct = _leaves(account)
    is_card = bool(_blocks(text, "CCACCTFROM"))
    bankid, acctid = acct.get("BANKID", ""), acct.get("ACCTID", "")
    accttype = acct.get("ACCTTYPE", "CREDITCARD" if is_card else "")
    lines: list[Line] = [
        Line(
            ref="H1",
            text=" ".join(["Account", *(v for v in (bankid, acctid, accttype) if v)]),
        ),
    ]
    data: list[str] = []
    for n, block in enumerate(_blocks(text, "STMTTRN"), start=1):
        leaf = _leaves(block)
        parts = [
            leaf.get("DTPOSTED", "")[:8],
            leaf.get("TRNAMT", ""),
            leaf.get("TRNTYPE", ""),
            leaf.get("NAME", ""),
            leaf.get("MEMO", ""),
        ]
        lines.append(Line(ref=f"L{n}", text=" | ".join(p for p in parts if p)))
        data.append(f"L{n}")
    meta = {
        "bankid": bankid,
        "acctid": acctid,
        "accttype": accttype,
        "card": "1" if is_card else "0",
    }
    return Document(
        kind="ofx", sha256=sha256, lines=lines, preamble_refs=["H1"], data_refs=data, meta=meta
    )


def parse_ofx(text: str) -> ParsedStatement:
    tranlist = _leaves((_blocks(text, "BANKTRANLIST") or [""])[0].split("<STMTTRN>")[0])
    ledger = _leaves((_blocks(text, "LEDGERBAL") or [""])[0])
    closing = parse_money(ledger.get("BALAMT"))
    parsed = ParsedStatement(
        importer="ofx",
        perspective="household",
        period_start=_ofx_date(tranlist.get("DTSTART")),
        period_end=_ofx_date(tranlist.get("DTEND")),
        closing_balance_pence=to_pence(closing) if closing is not None else None,
        currency=_leaves(text).get("CURDEF", "GBP"),
    )
    for n, block in enumerate(_blocks(text, "STMTTRN"), start=1):
        leaf = _leaves(block)
        day = _ofx_date(leaf.get("DTPOSTED"))
        amount = parse_money(leaf.get("TRNAMT"))
        if day is None or amount is None:
            continue  # the check reports the line as missing
        name, memo = leaf.get("NAME", ""), leaf.get("MEMO", "")
        description = name if not memo or memo == name else f"{name} {memo}".strip()
        parsed.rows.append(
            ParsedRow(
                ref=f"L{n}",
                date=day,
                amount_pence=to_pence(amount),
                amount_text=leaf["TRNAMT"],
                raw_description=description or "(no description)",
                merchant=name or None,
                bank_type=leaf.get("TRNTYPE"),
            )
        )
    if parsed.period_start is None and parsed.rows:
        parsed.period_start = min(r.date for r in parsed.rows)
    if parsed.period_end is None and parsed.rows:
        parsed.period_end = max(r.date for r in parsed.rows)
    return parsed
