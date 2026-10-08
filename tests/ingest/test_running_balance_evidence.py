"""I3 (final review): a running balance the AI reader reports is used only when that figure is
printed on the row's own line, with its sign. Otherwise it is treated as absent, so a model
that works balances out for itself (or is told to by text in a payee reference) can't make
swapped signs pass as "Balances add up". All statements are synthetic."""

import datetime as dt
import io
import json
import re

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ingest.helpers import add_account, drain, use_local_model
from tuppence.ingest.check import balance_printed
from tuppence.ingest.models import ParsedRow


def _row(amount: int, balance: int | None) -> ParsedRow:
    return ParsedRow(
        ref="L1",
        date=dt.date(2026, 10, 2),
        amount_pence=amount,
        amount_text="x",
        raw_description="x",
        balance_after_pence=balance,
    )


@pytest.mark.parametrize(
    ("line", "amount", "balance", "printed"),
    [
        ("02 Oct 2026 Shop 42.18 957.82", -4218, 95782, True),
        ("02 Oct 2026 Shop 42.18 £957.82", -4218, 95782, True),
        ("02 Oct 2026 Shop 42.18 1,957.82", -4218, 195782, True),
        ("02 Oct 2026 Shop 42.18 57.82 OD", -4218, -5782, True),
        ("02 Oct 2026 Shop 42.18 57.82 DR", -4218, -5782, True),
        ("02 Oct 2026 Shop 42.18 -57.82", -4218, -5782, True),
        ("02 Oct 2026 Shop 42.18 (57.82)", -4218, -5782, True),
        ("02 Oct 2026 Shop 50.00 50.00", -5000, 5000, True),  # the same size, printed twice
        ("02 Oct 2026 Acme Payroll Ltd 50.00", 5000, 95000, False),  # not printed at all
        ("02 Oct 2026 Acme Payroll Ltd 50.00", 5000, 5000, False),  # only the amount itself
        ("02 Oct 2026 Shop 42.18 57.82", -4218, -5782, False),  # printed in credit
        ("02 Oct 2026 Shop 42.18 57.82 OD", -4218, 5782, False),  # printed overdrawn
        ("02 Oct 2026 Shop 42.18 1957.82", -4218, 95782, False),  # part of another figure
        ("02 Oct 2026 Shop 42.18", -4218, None, True),  # nothing claimed
    ],
)
def test_a_running_balance_counts_only_when_printed_on_its_line(line, amount, balance, printed):
    assert balance_printed(_row(amount, balance), line, "household") is printed


def test_a_card_balance_in_credit_is_printed_with_cr():
    assert balance_printed(_row(-1000, -500), "02 Oct 2026 Shop 10.00 5.00 CR", "card")
    assert balance_printed(_row(-1000, 500), "02 Oct 2026 Shop 10.00 5.00", "card")
    assert not balance_printed(_row(-1000, -500), "02 Oct 2026 Shop 10.00 5.00", "card")


def _two_column_pdf() -> bytes:
    """Paid out / Paid in, no balance column; opening and closing both £1,000.00."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 10)
    rows = [
        [(56, "Example Bank plc", False)],
        [(56, "Statement period 01/10/2026 to 31/10/2026", False)],
        [(56, "Opening balance £1,000.00", False)],
        [(56, "Closing balance £1,000.00", False)],
        [(56, "Date", False), (140, "Description", False), (400, "Paid out", True),
         (470, "Paid in", True)],
        [(56, "02 Oct 2026", False), (140, "Acme Payroll Ltd", False), (470, "50.00", True)],
        [(56, "03 Oct 2026", False), (140, "Greenbasket Stores", False), (400, "50.00", True)],
    ]  # fmt: skip
    y = 800
    for cells in rows:
        for x, text, right in cells:
            (c.drawRightString if right else c.drawString)(x, y, text)
        y -= 15
    c.setFont("Helvetica", 8)
    c.drawString(56, 48, "SYNTHETIC TEST STATEMENT - NOT A REAL DOCUMENT")
    c.save()
    return buf.getvalue()


def _swapped(fabricate: bool):
    """A reply with both signs swapped, labelled from the wrong columns; with `fabricate`, it
    also works out running balances that are printed nowhere and make the swap add up."""

    def answer(body):
        user = body["messages"][-1]["content"].split("Your previous answer failed")[0]
        rows, skipped = [], []
        for ref, text in re.findall(r"^(D\d+): (.*)$", user, re.MULTILINE):
            if "Payroll" in text:
                rows.append(dict(ref=ref, date="2026-10-02", amount=-50.0, amount_text="50.00",
                                 sign_from="Paid out", raw_desc="Acme Payroll Ltd", merchant=None,
                                 bank_category=None, bank_type=None,
                                 running_balance=950.0 if fabricate else None))  # fmt: skip
            elif "Greenbasket" in text:
                rows.append(dict(ref=ref, date="2026-10-03", amount=50.0, amount_text="50.00",
                                 sign_from="Paid in", raw_desc="Greenbasket Stores", merchant=None,
                                 bank_category=None, bank_type=None,
                                 running_balance=1000.0 if fabricate else None))  # fmt: skip
            else:
                skipped.append({"ref": ref, "reason": "heading"})
        statement = {
            "period_start": "2026-10-01",
            "period_end": "2026-10-31",
            "opening_balance": None,
            "closing_balance": None,
            "currency": "GBP",
        }
        return json.dumps({"statement": statement, "transactions": rows, "skipped": skipped})

    return answer


@pytest.mark.parametrize("fabricate", [True, False])
def test_running_balances_printed_nowhere_cant_make_swapped_signs_add_up(ingest_env, fabricate):
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")
    scripted.replies = [_swapped(fabricate)] * 6
    outcome = services.ingest.upload("two-columns.pdf", _two_column_pdf())
    drain(services)
    record = services.statements.get(outcome.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(outcome.record.id)
    assert record.status == "needs_review" and not record.balance_verified
    assert any("more than one way" in e for e in record.check_errors), record.check_errors
    rows = record.draft["parsed"]["rows"]
    assert [r["balance_after_pence"] for r in rows] == [None, None]
    assert services.statements.transactions(record.id) == []
