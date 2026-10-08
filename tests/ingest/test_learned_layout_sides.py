"""I4 (final review) and M11, M7: a learned CSV layout is remembered for the headings and the
side of the account it was confirmed with (a bank account's or a card's), saved only inside
the import, and never reused for the other side; a statement whose wording says one kind of
account, answered as the other, is checked by the person. Every way rows are read (a bank's
layout, a learned layout, a fresh mapping, OFX/QIF, the AI reader) goes through Check, and
each failed check lands in needs_review. All files are synthetic."""

import json

import pytest

from ingest.helpers import add_account, drain, use_local_model

CURRENT = """Date,Description,Amount,Reference
01/10/2026,GREENBASKET STORES,-42.18,R1
03/10/2026,ACME PAYROLL LTD,900.00,R2
05/10/2026,LITTLE CAFE,-3.40,R3
"""
CARD = """Date,Description,Amount,Reference
02/11/2026,NORTHWIND BOOKS,18.40,C1
04/11/2026,CITY CINEMA,24.00,C2
09/11/2026,PAYMENT BY DIRECT DEBIT,-150.00,C3
"""


def _settle(services, outcome, account):
    record = services.statements.get(outcome.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(outcome.record.id)
    return record


def _upload(services, name, text, account):
    outcome = services.ingest.upload(name, text.encode())
    drain(services)
    return _settle(services, outcome, account)


def test_a_layout_learned_on_a_current_account_is_not_reused_for_a_card(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    current = add_account(services, "other", "current", "Credit union current")
    card = add_account(services, "other", "credit_card", "Store card")
    first = _upload(services, "current.csv", CURRENT, current)
    assert first.status == "imported" and first.importer.startswith("csv:learned-")
    calls = len(scripted.requests)
    second = _upload(services, "card.csv", CARD, card)
    assert len(scripted.requests) > calls  # learned afresh for the card's side
    assert second.status == "needs_review", second.importer
    assert services.statements.transactions(second.id) == []
    # Try again, and moving it to another card, can't get round it either.
    again = services.ingest.retry(second.id, expected_version=second.version)
    drain(services)
    assert services.statements.get(again.id).status == "needs_review"
    other_card = add_account(services, "other", "credit_card", "Another card")
    record = services.statements.get(second.id)
    services.ingest.change_account(record.id, account_id=other_card.id,
                                   expected_version=record.version)  # fmt: skip
    drain(services)
    assert services.statements.get(second.id).status == "needs_review"


def test_the_same_headings_are_remembered_once_for_each_side(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    current = add_account(services, "other", "current", "Current")
    card = add_account(services, "other", "credit_card", "Card")
    _upload(services, "current.csv", CURRENT, current)
    record = _upload(services, "card.csv", CARD, card)
    assert record.status == "needs_review"  # nothing on it shows which way round it is
    services.ingest.accept(record.id, expected_version=record.version)
    learned = services.layouts.learned.all()
    assert len(learned) == 2
    assert {layout.kind for _, layout in learned} == {"current", "credit_card"}
    assert len({layout.id for _, layout in learned}) == 2
    calls = len(scripted.requests)
    later = CARD.replace("02/11/2026", "05/12/2026").replace("C1", "C9")
    again = _upload(services, "card-december.csv", later, card)
    assert len(scripted.requests) == calls  # the card's own layout: no AI call
    assert again.importer == record.importer or again.importer.startswith("csv:learned-")


def test_a_learned_layout_is_saved_only_inside_the_import(ingest_env, monkeypatch):
    """M11: if the import fails, the layout isn't remembered."""
    from tuppence.ingest.store import StatementStore

    services, _ = ingest_env
    use_local_model(services)
    current = add_account(services, "other", "current", "Current")

    def broken(self, *args, **kwargs):
        within = kwargs.get("within")
        with self.db.transaction() as conn:
            if within is not None:
                within(conn)
            raise RuntimeError("disk full")

    monkeypatch.setattr(StatementStore, "persist", broken)
    record = _upload(services, "current.csv", CURRENT, current)
    assert record.status == "failed"
    assert services.layouts.learned.all() == []


def test_widened_card_payment_wording():
    from tuppence.ingest.parse import card_payment

    for text in ("PAYMENT BY DIRECT DEBIT", "DIRECT DEBIT PAYMENT", "PAYMENT RECEIVED",
                 "PAYMENT - THANK YOU", "DD PAYMENT", "PAYMENT BY FASTER PAYMENT",
                 "CARD PAYMENT RECEIVED", "BALANCE TRANSFER IN"):  # fmt: skip
        assert card_payment(text), text
    for text in ("GREENBASKET STORES", "CARD PAYMENT TO CITY CINEMA", "CONTACTLESS PAYMENT"):
        assert not card_payment(text), text


CARD_TEXT = """Example Card Services
Card statement
Minimum payment £25.00
Statement period 01/10/2026 to 31/10/2026
Date Description Amount
02/10/2026 Greenbasket Stores 42.18
05/10/2026 Payment received - thank you 100.00 CR
"""


def test_a_card_statement_answered_as_a_current_account_is_checked(ingest_env):
    """M7: the wording says credit card, the person chose a current account: the signs could be
    back to front, so the person checks it."""
    services, _ = ingest_env
    use_local_model(services)
    current = add_account(services, "other", "current", "Current")
    record = _upload(services, "card.txt", CARD_TEXT, current)
    assert record.status == "needs_review"
    assert any("credit card" in e and "current account" in e for e in record.check_errors)


# --- every path goes through Check (coordinator audit) ----------------------------------------


def test_a_banks_own_layout_goes_through_check(ingest_env, fixtures):
    services, _ = ingest_env
    account = add_account(services, "nationwide", "current", "Nationwide")
    text = (fixtures / "csv" / "nationwide.csv").read_text().replace("£48.20", "£84.20", 1)
    record = _upload(services, "nationwide-wrong.csv", text, account)
    assert record.status == "needs_review", record.check_errors
    assert services.statements.transactions(record.id) == []


def test_a_learned_layout_goes_through_check(ingest_env, fixtures):
    services, _ = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Credit union")
    first = _upload(services, "cu.csv", (fixtures / "csv-unknown" / "credit-union.csv")
                    .read_text(), account)  # fmt: skip
    assert first.status == "imported"
    text = (fixtures / "csv-unknown" / "credit-union-nov.csv").read_text()
    lines = text.splitlines()
    cells = lines[1].split(",")
    cells[-1] = "1.23"
    lines[1] = ",".join(cells)
    record = _upload(services, "cu-nov-wrong.csv", "\n".join(lines) + "\n", account)
    assert record.status == "needs_review", record.check_errors


def test_a_fresh_mapping_goes_through_check(ingest_env, fixtures):
    services, _ = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Credit union")
    text = (fixtures / "csv-unknown" / "credit-union.csv").read_text()
    lines = text.splitlines()
    cells = lines[1].split(",")
    cells[-1] = "1.23"
    lines[1] = ",".join(cells)
    record = _upload(services, "cu-wrong.csv", "\n".join(lines) + "\n", account)
    assert record.status == "needs_review", record.check_errors
    assert services.layouts.learned.all() == []


@pytest.mark.parametrize(
    ("relative", "kind", "broken"),
    [
        ("ofx/current.ofx", "current", ("<TRNAMT>", "<TRNAMT>x")),
        ("qif/bank.qif", "current", ("\nT", "\nTx")),
    ],
)
def test_structured_files_go_through_check(ingest_env, fixtures, relative, kind, broken):
    services, _ = ingest_env
    account = add_account(services, "other", kind, "Probe")
    text = (fixtures / relative).read_text().replace(*broken, 1)
    record = _upload(services, relative.replace("/", "-"), text, account)
    assert record.status == "needs_review", record.check_errors


def test_the_ai_read_with_sign_repair_goes_through_check(ingest_env):
    """A wrong running balance on a row: sign repair can't make it add up, Check says so."""
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")
    text = (
        "Example Bank plc\nStatement period 01/10/2026 to 31/10/2026\n"
        "Opening balance 1,000.00\nClosing balance 957.82\n"
        "Date Description Paid out Paid in Balance\n"
        "01/10/2026 Greenbasket Stores 42.18 957.82\n"
        "02/10/2026 Little Cafe 3.40 950.00\n"
    )
    record = _upload(services, "repair.txt", text, account)
    assert record.status == "needs_review", (record.status, record.check_errors)
    assert not record.balance_verified


def test_a_model_cannot_mark_a_statement_verified(ingest_env):
    """Fields the reader schema doesn't have ("verified", "confidence", "source") are ignored,
    and with no balances printed nothing is verified."""
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")

    def answer(body):
        from evals import oracle

        reply = oracle.read(body["messages"][-1]["content"])
        reply["verified"] = True
        reply["statement"]["balance_verified"] = True
        reply["statement"]["confidence"] = 1.0
        for row in reply["transactions"]:
            row["source"] = "balance"
        return json.dumps(reply)

    scripted.replies = [answer] * 3
    text = "Example Bank plc\nDate Description Amount\n02/10/2026 Shop -5.00\n"
    record = _upload(services, "verified.txt", text, account)
    assert record.status == "imported" and record.balance_verified is False
