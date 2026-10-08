"""One check of a statement's rows (R-M3-23 (c)): the same when the statement is read and each
time the person saves the fix-up screen.

`verify` reads the balances this device can see (never the model's opening balance, and its
closing balance only when it is the running balance printed on the last row), proves what
directions it can from them (sign repair), checks every row against its line and the sums, and
adds the doubts about the account the statement was read for (a card's statement read for a
bank account, or the other way round) and about the layout it was read with. A save that
changes nothing finds exactly what the read found, so it can't clear a doubt.

What is about the read itself (a chunk the reader gave up on, no rows read at all, the rows a
layout couldn't read) is the parse step's to report; this checks what was read."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from tuppence.ingest.balances import SignRepair, local_balances, repair_signs
from tuppence.ingest.check import balance_printed, base_ref, check_rows, check_statement
from tuppence.ingest.clock import Deadline
from tuppence.ingest.identify import Evidence
from tuppence.ingest.models import AccountKind, CheckLevel, Document, ParsedStatement
from tuppence.ingest.reader import tidy
from tuppence.ingest.registry import CsvLayout, side_of
from tuppence.ingest.textprep import sent_lines


class Basis(BaseModel):
    """What a statement was read with, kept in its draft so that checking it again after the
    person's edits runs exactly as the first check did."""

    account_kind: AccountKind
    evidence: Evidence | None = None  # what identify found (for the account-kind doubt)
    layout: CsvLayout | None = None  # the learned or proposed CSV layout it was read with
    layout_new: bool = False  # proposed for this file, not remembered yet


# Wording of money paid to a card (or given back by it): a payment, a refund, cashback, a
# balance moved onto it. "CARD PAYMENT TO ..." and "CONTACTLESS PAYMENT" are purchases.
_CARD_CREDIT = re.compile(
    r"payment\s*(?:-\s*)?(?:received|thank)|thank\s*you|\brefund|\bcash\s*back\b"
    r"|direct\s+debit\s+payment|\bdd\s+payment|payment\s+(?:by|via)\s+(?:direct\s+debit|dd"
    r"|faster\s+payment|bank\s+transfer|standing\s+order|debit\s+card)"
    r"|balance\s+transfer\s+in\b",
    re.IGNORECASE,
)


def card_payment(text: str) -> bool:
    """A card row's description says money was paid to the card (or given back)."""
    return _CARD_CREDIT.search(text) is not None


_TYPE_CREDIT = {"cr", "credit", "payment", "refund"}
_TYPE_DEBIT = {"dr", "debit", "purchase", "sale"}
_ACCOUNT_WORDS = {"current": "a current account", "savings": "a savings account"}


def _card_evidence(parsed: ParsedStatement) -> tuple[int, int]:
    """(rows that agree, rows that disagree) with the signs read: a payment to the card or a
    refund is money in, a row typed as a purchase or debit is money out."""
    agree = disagree = 0
    for row in parsed.rows:
        kind = (row.bank_type or "").strip().casefold()
        if kind in _TYPE_CREDIT or card_payment(row.raw_description):
            want = 1
        elif kind in _TYPE_DEBIT:
            want = -1
        else:
            continue
        if row.amount_pence * want > 0:
            agree += 1
        elif row.amount_pence:
            disagree += 1
    return agree, disagree


def sign_doubt(
    parsed: ParsedStatement, account_kind: AccountKind, layout: CsvLayout, *, new: bool
) -> str | None:
    """Why a learned layout's signs can't be trusted yet, or None.

    Two money columns are checked by Check against their headings. A single amount column
    adds up (with a balance column too) whichever way round it is read, so:
    - on a current or savings account, a layout that reads it the card's way is a
      contradiction;
    - on a card, the rows are the evidence: a payment or refund read as money out (or a
      purchase read as money in) means back to front, and a new layout with no such row
      waits for the person to confirm it."""
    if layout.amount is None:
        return None
    if account_kind in _ACCOUNT_WORDS:
        if layout.perspective != "card":
            return None
        return (
            "The remembered layout for this file reads purchases as positive, as a card "
            f"statement does, which doesn't fit {_ACCOUNT_WORDS[account_kind]}. The signs may "
            "be back to front, so please check them."
        )
    if layout.direction is not None:  # each amount's direction is printed in a DR/CR column
        return None
    agree, disagree = _card_evidence(parsed)
    if disagree:
        return (
            f"{disagree} of these rows (payments to the card, refunds or purchases) would be "
            "stored the wrong way round, so the signs may be back to front. Please check them."
        )
    if not agree and new:
        return (
            "Nothing in this file shows which way round the card's amounts are (such as a "
            "payment to the card or a refund), so please check the signs before this layout "
            "is remembered."
        )
    return None


def held_back_message(doc: Document, count: int | None = None) -> str | None:
    """Withheld lines with an amount that may be transactions (text prep lists them): say so,
    without quoting them, instead of losing them silently. `count` is how many are still
    undecided (all of them by default). Lines left out for being too long have their own
    message (`too_long_message`)."""
    if count is None:
        long = set(doc.too_long_refs)
        count = sum(1 for ref in doc.held_amount_refs if ref not in long)
    what = "screenshot" if doc.kind == "image" else "statement"
    if count == 0:
        return None
    if count == 1:
        return (
            f"A line of this {what} with an amount on it was held back from the AI because it "
            "may show account details or a balance. It may be a transaction, so please check it."
        )
    return (
        f"{count} lines of this {what} with an amount on them were held back from the AI because "
        "they may show account details or a balance. They may be transactions, so please check "
        "them."
    )


def too_long_message(doc: Document, count: int | None = None) -> str | None:
    """Lines too long to read safely were left out: say so (`count` still undecided)."""
    count = len(doc.too_long_refs) if count is None else count
    if count == 0:
        return None
    these = "A line" if count == 1 else f"{count} lines"
    them = "it" if count == 1 else "them"
    return (
        f"{these} of this file {'was' if count == 1 else 'were'} too long to read, so "
        f"{'it was' if count == 1 else 'they were'} left out. Please check {them}."
    )


_KIND_NAMES = {
    "current": "a current account",
    "savings": "a savings account",
    "credit_card": "a credit card",
}


def kind_doubt(evidence: Evidence, account_kind: AccountKind) -> str | None:
    """M7: the file (or its wording) says one side of account, a card's or a bank account's,
    and the person chose the other. The amounts are read the chosen account's way, which would
    be back to front, so the person checks it."""
    said = evidence.kind or evidence.kind_hint
    if said is None or side_of(said) == side_of(account_kind):
        return None
    return (
        f"This statement looks like it's from {_KIND_NAMES[said]}, but it's being read for "
        f"{_KIND_NAMES[account_kind]}, so its amounts may be the wrong way round. If that's the "
        "wrong account, use Wrong account?; otherwise check the signs."
    )


def evidenced_closing(doc: Document, parsed: ParsedStatement) -> int | None:
    """The reader's closing balance, when it is the running balance printed on the last row
    (the balance after the last transaction, on the line the reader was sent); else None.

    No line the reader is sent prints an opening balance (balance lines are withheld and read
    on this device), so the model's opening balance is never used: it could only be worked out,
    and a worked-out balance can make anything add up (re-review N5)."""
    claim = parsed.closing_balance_pence
    if claim is None or not parsed.rows:
        return None
    order = {line.ref: i for i, line in enumerate(doc.lines)}
    last = max(parsed.rows, key=lambda row: order.get(base_ref(row.ref), -1))
    if last.balance_after_pence != claim:
        return None
    line = sent_lines(doc).get(base_ref(last.ref))
    if line is None or not balance_printed(last, line.text, parsed.perspective):
        return None
    return claim


def decided(doc: Document, parsed: ParsedStatement) -> list[str]:
    """Held-back lines the person made a row (typed in) or marked as not a transaction."""
    used = {r.ref for r in parsed.rows if r.edited} | {s.ref for s in parsed.skipped}
    return [ref for ref in doc.held_amount_refs if ref in used]


@dataclass(frozen=True)
class Verified:
    errors: list[str]  # plain messages; empty: nothing to look at
    repaired: list[str]  # rows whose direction the printed balances proved and set


def verify(
    doc: Document,
    parsed: ParsedStatement,
    basis: Basis,
    *,
    level: CheckLevel,
    deadline: Deadline | None = None,
) -> Verified:
    """Every check of `parsed` against `doc`.

    For rows the AI reader read, the opening and closing balances are set from what this
    device reads and sign repair runs first, changing in place only directions the printed
    balances prove (never a row the person edited)."""
    errors: list[str] = []
    repair = SignRepair([], [])
    if parsed.importer == "ai-read":
        local = local_balances(doc, perspective=parsed.perspective, deadline=deadline)
        parsed.opening_balance_pence = local.opening
        parsed.closing_balance_pence = (
            local.closing if local.closing is not None else evidenced_closing(doc, parsed)
        )
        repair = repair_signs(
            doc,
            parsed,
            opening=local.opening,
            closing=local.closing,
            level=level,
            deadline=deadline,
        )
    errors += check_rows(
        doc.lines,
        all_lines=doc.lines,
        context_refs=doc.header_refs,
        data_refs=[*doc.data_refs, *decided(doc, parsed)],
        parsed=parsed,
        level=level,
        deadline=deadline,
    )
    errors += check_statement(parsed, level=level, dates=True, deadline=deadline)
    errors += repair.errors
    if basis.layout is not None and (
        doubt := sign_doubt(parsed, basis.account_kind, basis.layout, new=basis.layout_new)
    ):
        errors.append(doubt)
    if basis.evidence is not None and (doubt := kind_doubt(basis.evidence, basis.account_kind)):
        errors.append(doubt)
    used = {r.ref for r in parsed.rows} | {s.ref for s in parsed.skipped}
    long = set(doc.too_long_refs)
    held_left = [r for r in doc.held_amount_refs if r not in used and r not in long]
    if held := held_back_message(doc, len(held_left)):
        errors.append(held)
    if too_long := too_long_message(doc, sum(1 for r in long if r not in used)):
        errors.append(too_long)
    # Checks quote the model's text: each message is cleaned and capped.
    return Verified(list(dict.fromkeys(tidy(e) for e in errors)), repair.repaired)
