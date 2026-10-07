"""Which bank and which account is this statement from? Local only, never an AI (spec §6.2).

`identify()` reads the evidence a file carries (CSV headings, PDF markers, OFX
BANKID/ACCTID, CAMT BIC/IBAN, and header facts such as the last 4 digits) and
`match_account()` compares it with the household's accounts and with what was
answered before for the same layout.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, Field

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.ingest.models import AccountKind, Document
from tuppence.ingest.registry import (
    BankPack,
    CsvLayout,
    LayoutRegistry,
    column_getter,
    data_records,
    header_cells,
    header_key,
)
from tuppence.ingest.textnum import has_credit_marker, parse_date, parse_money, to_pence

_DATE = (
    r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?\s+\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})"
)
_PERIOD = re.compile(_DATE + r"\s*(?:to|until|-|–)\s*" + _DATE, re.IGNORECASE)
_AMOUNT = r"(-?\s*£?\s*\d{1,3}(?:,\d{3})*\.\d{2}(?:\s*(?:CR|DR)\b)?)"
_OPENING = re.compile(
    r"\b(?:(?:opening|previous|starting|start)\s+balance|balance\s+brought\s+forward)\b[^\d£\n-]{0,20}"
    + _AMOUNT,
    re.IGNORECASE,
)
_CLOSING = re.compile(
    r"\b(?:(?:closing|new|ending|end)\s+balance|balance\s+carried\s+forward)\b[^\d£\n-]{0,20}"
    + _AMOUNT,
    re.IGNORECASE,
)
_MASK = r"[*xX•]"
# The number's own token only: a full 16-digit card (4x4, masks allowed), an Amex 4-6-5, a
# masked number, a sort code plus account number, or a plain digit run. Never the date or
# amount that happens to follow it on the line.
_NUMBER_TOKEN = (
    rf"(?:[*xX•\d]{{4}}(?:[ \-][*xX•\d]{{4}}){{3}}(?!\d)"
    r"|\d{4} \d{6} \d{5}(?!\d)"
    rf"|{_MASK}{{2,}}(?:[ \-]{_MASK}{{2,}})*[ \-]?\d{{4,}}(?!\d)"
    r"|\d{2}-\d{2}-\d{2}[ \-]\d{6,8}(?!\d)"
    r"|\d{4,}(?!\d))"
)
_LABELLED_NUMBER = re.compile(
    r"(?:account\s*(?:number|no\.?)|card\s*(?:number|no\.?)|ending(?:\s+in)?)"
    rf"\s*[:#]?\s*({_NUMBER_TOKEN})",
    re.IGNORECASE,
)
_MASKED = re.compile(rf"{_MASK}{{2,}}[ \-]?(\d{{4,}})(?!\d)")
_CARD_WORDS = re.compile(r"minimum payment|credit limit|card ending|card statement", re.IGNORECASE)


class HeaderFacts(BaseModel):
    last4: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    opening_pence: int | None = None  # as printed
    closing_pence: int | None = None
    looks_like_card: bool = False


class Evidence(BaseModel):
    layout_fingerprint: str
    layout_id: str | None = None
    providers: list[str] = Field(default_factory=list)  # firm evidence: one of these banks
    provider_hint: str | None = None  # a guess, for preselecting and prefilling only
    kind: AccountKind | None = None
    last4: str | None = None
    facts: HeaderFacts = Field(default_factory=HeaderFacts)
    label: str


class AccountRef(BaseModel):
    id: str
    provider: str
    provider_name: str
    kind: AccountKind
    nickname: str
    last4: str | None
    status: str = "active"


class AccountMatch(BaseModel):
    account_id: str | None  # set only for a strong match
    best_guess: str | None
    candidates: list[str]
    reason: str
    prefill: dict[str, str | None]


def _money(text: str) -> int | None:
    value = parse_money(text)
    if value is None:
        return None
    pence = to_pence(value)
    return -abs(pence) if has_credit_marker(text) else pence  # "£12.00 CR" on a card: in credit


def header_facts(text: str) -> HeaderFacts:
    facts = HeaderFacts(looks_like_card=bool(_CARD_WORDS.search(text)))
    if period := _PERIOD.search(text):
        facts.period_start, facts.period_end = (
            parse_date(period.group(1)),
            parse_date(period.group(2)),
        )
    if opening := _OPENING.search(text):
        facts.opening_pence = _money(opening.group(1))
    if closing := _CLOSING.search(text):
        facts.closing_pence = _money(closing.group(1))
    found: set[str] = set()
    for match in [*_LABELLED_NUMBER.finditer(text), *_MASKED.finditer(text)]:
        digits = re.sub(r"\D", "", match.group(1))
        if len(digits) >= 4:
            found.add(digits[-4:])
    if len(found) == 1:
        facts.last4 = found.pop()
    return facts


_KEY_SETTING = "ingest.fingerprint_key"


def fingerprint_key(db: Database) -> bytes:
    """This install's random key for account fingerprints. It is created once, stays in the
    local database, and is never exported or logged, so a stored fingerprint can't be turned
    back into an account number by trying them all."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value, version, updated_at) "
            "VALUES (?, ?, 1, ?)",
            [_KEY_SETTING, secrets.token_hex(32), to_iso(utcnow())],
        )
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", [_KEY_SETTING]
        ).fetchone()
    return bytes.fromhex(row["value"])


def _short_hash(value: str, key: bytes) -> str:
    normalised = re.sub(r"\s+", "", value).upper()
    return hmac.new(key, normalised.encode(), hashlib.sha256).hexdigest()[:12]


def _sort_code_provider(pack: BankPack, bankid: str) -> str | None:
    digits = re.sub(r"\D", "", bankid)
    return (
        pack.sort_codes.get(digits[:6]) or pack.sort_codes.get(digits[:2])
        if len(digits) >= 6
        else None
    )


def identify(doc: Document, *, pack: BankPack, registry: LayoutRegistry, key: bytes) -> Evidence:
    by_ref = doc.by_ref()
    preamble_text = "\n".join(by_ref[r].text for r in doc.preamble_refs if r in by_ref)
    if doc.kind in ("csv", "xlsx"):
        return _identify_table(doc, registry, header_facts(preamble_text))
    if doc.kind == "ofx":
        acctid = doc.meta.get("acctid", "")
        digits = re.sub(r"\D", "", acctid)
        card = doc.meta.get("card") == "1"
        accttype = doc.meta.get("accttype", "").upper()
        provider = _sort_code_provider(pack, doc.meta.get("bankid", ""))
        return Evidence(
            layout_fingerprint=f"ofx:{doc.meta.get('bankid') or '-'}:{_short_hash(acctid, key)}",
            providers=[provider] if provider else [],
            provider_hint=provider,
            kind="credit_card"
            if card
            else ("savings" if accttype in {"SAVINGS", "MONEYMRKT"} else "current"),
            last4=digits[-4:] if len(digits) >= 4 else None,
            label="OFX download",
        )
    if doc.kind == "camt053":
        iban, bic = doc.meta.get("iban", ""), doc.meta.get("bic", "")
        digits = re.sub(r"\D", "", iban[4:]) if iban else ""
        provider = pack.bics.get(bic[:8].upper()) if bic else None
        return Evidence(
            layout_fingerprint=f"camt:{bic or '-'}:{_short_hash(iban, key)}",
            providers=[provider] if provider else [],
            provider_hint=provider,
            last4=digits[-4:] if len(digits) >= 4 else None,
            label="CAMT.053 statement",
        )
    if doc.kind == "qif":
        card = doc.meta.get("card") == "1"
        return Evidence(
            layout_fingerprint=f"qif:{doc.meta.get('qif_type', '').lower()}",
            kind="credit_card" if card else None,
            label="QIF download",
        )
    if doc.kind == "image":
        return Evidence(layout_fingerprint="image", label="screenshot")
    # PDF or plain text: a bank's legal name is printed somewhere on the statement.
    first_lines = "\n".join(line.text for line in doc.lines[:60])
    # The bank's name and the account facts are in the header, not in payee names further down.
    top = f"{preamble_text}\n{first_lines}".casefold()
    facts = header_facts(preamble_text or first_lines)
    for marker in pack.pdf_markers:
        if any(needle.casefold() in top for needle in marker.any):
            kind = marker.kind or ("credit_card" if facts.looks_like_card else None)
            return Evidence(
                layout_fingerprint=f"{doc.kind}:{marker.provider}:{kind or '-'}",
                providers=[marker.provider],
                provider_hint=marker.provider,
                kind=kind,
                last4=facts.last4,
                facts=facts,
                label=f"{'PDF' if doc.kind == 'pdf' else 'text'} statement",
            )
    kind = "credit_card" if facts.looks_like_card else None
    return Evidence(
        layout_fingerprint=f"{doc.kind}:unknown:{kind or '-'}",
        kind=kind,
        last4=facts.last4,
        facts=facts,
        label=f"{'PDF' if doc.kind == 'pdf' else 'text'} statement",
    )


def _identify_table(doc: Document, registry: LayoutRegistry, facts: HeaderFacts) -> Evidence:
    layout = registry.match(doc)
    header = header_cells(doc)
    if header is not None:
        fingerprint = f"{doc.kind}:{header_key(header)}"
    else:
        width = len(data_records(doc)[0][1]) if data_records(doc) else 0
        fingerprint = f"{doc.kind}:noheader:{width}"
    if layout is None:
        return Evidence(
            layout_fingerprint=fingerprint,
            last4=facts.last4,
            facts=facts,
            kind="credit_card" if facts.looks_like_card else None,
            label="CSV file in a new layout",
        )
    last4 = facts.last4 or _column_last4(doc, layout)
    firm = bool(layout.signature)  # files without headings only hint at a bank
    return Evidence(
        layout_fingerprint=fingerprint,
        layout_id=layout.id,
        providers=list(layout.providers) if firm else [],
        provider_hint=layout.providers[0] if layout.providers else None,
        kind=layout.kind,
        last4=last4,
        facts=facts,
        label=layout.name,
    )


def _column_last4(doc: Document, layout: CsvLayout) -> str | None:
    if not layout.account_number:
        return None
    get = column_getter(layout, header_cells(doc))
    for _, cells in data_records(doc):
        digits = re.sub(r"\D", "", get(cells, layout.account_number))
        if len(digits) >= 4:
            return digits[-4:]
    return None


def match_account(
    evidence: Evidence, accounts: Sequence[AccountRef], remembered: Sequence[str]
) -> AccountMatch:
    """A strong match is assigned without asking; anything else becomes a question.

    `remembered` lists the accounts earlier answers tied to this layout fingerprint,
    oldest first. Memory only decides on its own when every earlier answer was the
    same account and no other account at that bank has the same type, so two
    accounts with identical exports (a personal and a joint Monzo) are always asked.
    """
    active = [a for a in accounts if a.status == "active"]
    prefill: dict[str, str | None] = {
        "provider": evidence.providers[0] if evidence.providers else evidence.provider_hint,
        "kind": evidence.kind,
        "last4": evidence.last4,
    }

    def strong(account: AccountRef, reason: str) -> AccountMatch:
        return AccountMatch(
            account_id=account.id,
            best_guess=account.id,
            candidates=[account.id],
            reason=reason,
            prefill=prefill,
        )

    def has_sibling(account: AccountRef) -> bool:
        return any(
            a.id != account.id and a.provider == account.provider and a.kind == account.kind
            for a in active
        )

    pool = [
        a
        for a in active
        if (not evidence.providers or a.provider in evidence.providers)
        and (not evidence.kind or a.kind == evidence.kind)
    ]
    # Bank evidence, or an OFX/CAMT fingerprint (tied to one account), is what lets a match
    # decide. Without it a last 4 or an earlier answer only suggests an account.
    specific = evidence.layout_fingerprint.startswith(("ofx:", "camt:"))
    trusted = bool(evidence.providers) or specific
    last4_unmatched = False
    if evidence.last4:
        exact = [a for a in pool if a.last4 == evidence.last4]
        if len(exact) == 1 and trusted:
            return strong(exact[0], "The bank, account type and last 4 digits match.")
        if not exact:
            last4_unmatched = True
            pool = [a for a in pool if a.last4 is None]
        else:
            pool = exact
    if evidence.providers and len(pool) == 1 and not last4_unmatched:
        return strong(pool[0], "It's your only account at this bank of this type.")
    distinct = list(dict.fromkeys(remembered))
    if len(distinct) == 1 and not last4_unmatched:
        chosen = next((a for a in pool if a.id == distinct[0]), None)
        if (
            chosen is not None
            and not has_sibling(chosen)
            and (
                specific
                or (evidence.providers and evidence.last4 and evidence.last4 == chosen.last4)
            )
        ):
            return strong(chosen, "You chose this account for this kind of file before.")
    candidates = [a.id for a in (pool or active)]
    in_memory = [r for r in reversed(distinct) if r in candidates]
    hinted = [
        a.id for a in active if evidence.provider_hint and a.provider == evidence.provider_hint
    ]
    best = (
        (in_memory[0] if in_memory else None)
        or (candidates[0] if len(candidates) == 1 else None)
        or (hinted[0] if hinted else None)
    )
    if last4_unmatched and not pool:
        best = None  # the statement names an account we don't know: offer to add it
    if not active:
        reason = "You haven't added any accounts yet."
    elif not pool:
        reason = "None of your accounts match this statement."
    elif len(candidates) == 1:
        reason = "This looks like one of your accounts. Please confirm."
    else:
        reason = "More than one of your accounts could match."
    return AccountMatch(
        account_id=None, best_guess=best, candidates=candidates, reason=reason, prefill=prefill
    )
