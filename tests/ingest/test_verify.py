"""R-M3-23 (c), re-review N5 and N7: one `verify(doc, rows, account)` checks a statement when it
is read and each time the person saves the fix-up screen: the sums, sign repair, the balances
read on this device, the account-kind and layout doubts. A save that changes nothing can't
clear a doubt, and a balance the model made up is never stored or verified."""

from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from ingest.helpers import add_account, drain, use_local_model
from tuppence.ingest import parse, service, verify
from tuppence.ingest.models import SkippedLine
from tuppence.ingest.service import RowEdit

# --- N5: a model's opening and closing balance --------------------------------------------------

NO_BALANCES = """Example Bank plc
Statement period 01/10/2026 to 31/10/2026
Date Description Amount
02/10/2026 Greenbasket Stores -20.00
03/10/2026 Acme Payroll Ltd +30.00
04/10/2026 Refund Northwind +10.00
"""


def _reply(body, opening, closing, balances=None):
    user = body["messages"][-1]["content"].split("Your previous answer")[0]
    rows, skipped = [], []
    for ref, text in re.findall(r"^(D\d+): (.*)$", user, re.MULTILINE):
        figures = re.findall(r"[-+]?\d[\d,]*\.\d{2}", text)
        if figures and re.match(r"\d\d/10/2026", text):
            amount = float(figures[0].replace(",", ""))
            balance = balances.get(text[:10]) if balances else None
            rows.append(dict(ref=ref, date=f"2026-10-{text[:2]}", amount=amount,
                             amount_text=figures[0], sign_from=None, raw_desc=text[11:30],
                             merchant=None, bank_category=None, bank_type=None,
                             running_balance=balance))  # fmt: skip
        else:
            skipped.append({"ref": ref, "reason": "heading"})
    statement = {
        "period_start": "2026-10-01",
        "period_end": "2026-10-31",
        "opening_balance": opening,
        "closing_balance": closing,
        "currency": "GBP",
    }
    return json.dumps({"statement": statement, "transactions": rows, "skipped": skipped})


def _read(services, scripted, name: str, text: str, reply, kind="current"):
    use_local_model(services)
    account = add_account(services, "other", kind, name)
    scripted.replies = [reply] * 6
    out = services.ingest.upload(name, text.encode())
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(out.record.id)
    return record


@pytest.mark.parametrize(("opening", "closing"), [(10.0, 30.0), (None, None), (-20.0, 10.0)])
def test_a_models_balances_that_are_row_amounts_are_not_used(ingest_env, opening, closing):
    """The reviewer's probe (test_rr_model_balances): opening and closing equal to two rows'
    amounts imported as "Balances add up" with a made-up balance stored."""
    services, scripted = ingest_env
    record = _read(services, scripted, f"mb{opening}.txt", NO_BALANCES,
                   lambda body: _reply(body, opening, closing))  # fmt: skip
    assert not record.balance_verified
    assert record.opening_balance_pence is None and record.closing_balance_pence is None
    with services.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM account_balance").fetchone()[0] == 0


WITH_BALANCES = """Example Bank plc
Statement period 01/10/2026 to 31/10/2026
Date Description Amount Balance
02/10/2026 Greenbasket Stores -20.00 980.00
03/10/2026 Acme Payroll Ltd +30.00 1,010.00
04/10/2026 Refund Northwind +10.00 1,020.00
"""


def test_a_closing_balance_printed_as_the_last_rows_balance_is_kept(ingest_env):
    services, scripted = ingest_env
    balances = {"02/10/2026": 980.0, "03/10/2026": 1010.0, "04/10/2026": 1020.0}
    record = _read(services, scripted, "wb.txt", WITH_BALANCES,
                   lambda body: _reply(body, 999.0, 1020.0, balances))  # fmt: skip
    assert record.closing_balance_pence == 102_000
    assert record.opening_balance_pence is None  # nothing printed it: never the model's
    assert not record.balance_verified


def test_a_closing_balance_that_is_another_rows_balance_is_not_kept(ingest_env):
    services, scripted = ingest_env
    balances = {"02/10/2026": 980.0, "03/10/2026": 1010.0, "04/10/2026": 1020.0}
    record = _read(services, scripted, "wb2.txt", WITH_BALANCES,
                   lambda body: _reply(body, None, 1010.0, balances))  # fmt: skip
    assert record.closing_balance_pence is None


# --- N7: a save that changes nothing keeps every doubt ----------------------------------------


def _save_unchanged(services, record):
    rows = [
        RowEdit(ref=r["ref"], date=dt.date.fromisoformat(r["date"]),
                amount_pence=r["amount_pence"], description=r["raw_description"])
        for r in record.draft["parsed"]["rows"]
    ]  # fmt: skip
    skipped = [SkippedLine(**s) for s in record.draft["parsed"]["skipped"]]
    return services.ingest.save_draft(
        record.id, rows=rows, skipped=skipped, expected_version=record.version
    )


def test_an_unchanged_save_keeps_the_sign_doubt_and_never_verifies(ingest_env):
    """The reviewer's probe (test_rr_save_clears_doubt, sign doubt): after an unchanged save
    the errors were [], and the import said "Balances add up" with both signs swapped."""
    from ingest.test_running_balance_evidence import _swapped, _two_column_pdf

    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")
    scripted.replies = [_swapped(False)] * 6
    out = services.ingest.upload("p2.pdf", _two_column_pdf())
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(out.record.id)
    assert record.status == "needs_review"
    doubts = [e for e in record.check_errors if "more than one way" in e]
    assert doubts
    saved = _save_unchanged(services, record)
    assert [e for e in saved.check_errors if "more than one way" in e] == doubts
    services.ingest.accept(saved.id, expected_version=saved.version)
    assert not services.statements.get(saved.id).balance_verified


def test_an_unchanged_save_keeps_the_account_kind_doubt(ingest_env, fixtures):
    """The reviewer's probe (kind doubt): a card PDF read for a current account."""
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Wrong: a current account")
    out = services.ingest.upload("card.pdf", (fixtures / "pdf" / "card-text.pdf").read_bytes())
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(out.record.id)
    assert record.status == "needs_review"
    doubt = [e for e in record.check_errors if "looks like it's from a credit card" in e]
    assert doubt
    saved = _save_unchanged(services, record)
    assert [e for e in saved.check_errors if "looks like it's from a credit card" in e] == doubt


CARD_NO_EVIDENCE = """Date,Description,Amount,Reference
02/11/2026,NORTHWIND BOOKS,18.40,C1
04/11/2026,CITY CINEMA,24.00,C2
"""


def test_an_unchanged_save_keeps_the_layout_doubt(ingest_env):
    """A card layout proposed for this file, with nothing in it showing which way round its
    amounts are, waits for the person; saving without a change doesn't settle it."""
    services, scripted = ingest_env
    use_local_model(services)
    card = add_account(services, "other", "credit_card", "Store card")
    out = services.ingest.upload("card.csv", CARD_NO_EVIDENCE.encode())
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(record.id, account_id=card.id,
                                       expected_version=record.version)  # fmt: skip
        drain(services)
        record = services.statements.get(out.record.id)
    assert record.status == "needs_review"
    doubt = [e for e in record.check_errors if "which way round" in e]
    assert doubt
    saved = _save_unchanged(services, record)
    assert [e for e in saved.check_errors if "which way round" in e] == doubt


# --- one verify for parse and recheck ---------------------------------------------------------


def test_parse_and_recheck_both_use_verify(ingest_env, monkeypatch):
    calls: list[str] = []
    real = verify.verify

    def spy(doc, parsed, basis, **kwargs):
        calls.append(parsed.importer)
        return real(doc, parsed, basis, **kwargs)

    monkeypatch.setattr(verify, "verify", spy)
    services, scripted = ingest_env
    record = _read(services, scripted, "spy.txt", NO_BALANCES, lambda body: _reply(body, 1.0, 2.0))
    assert calls == ["ai-read"]
    if record.status == "needs_review":
        _save_unchanged(services, record)
        assert calls == ["ai-read", "ai-read"]
    assert parse.verify is verify and service.verify is verify


def test_an_unchanged_save_gives_the_same_checks_as_the_read(ingest_env, fixtures):
    """Whatever the read found (apart from how the read itself went), saving without a change
    finds again: the rule is one function, run on the same rows."""
    from ingest.test_running_balance_evidence import _swapped, _two_column_pdf

    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")
    scripted.replies = [_swapped(True)] * 6
    out = services.ingest.upload("p2.pdf", _two_column_pdf() + b"%same")
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(out.record.id)
    assert record.status == "needs_review"
    saved = _save_unchanged(services, record)
    assert saved.check_errors == record.check_errors
