"""R-M3-23 (e), re-review 2 R1: a transaction row is sent to the reader or reported for the person,
never withheld without a word, whatever its description.

The descriptions are realistic UK statement wording: interest, fees, charges, payments, refunds,
statement credits, cashback, transfers, pots, standing orders, direct debits, cash, cheques,
merchants, and descriptions made only of balance or summary words ("INTEREST ON CREDIT BALANCE",
"STATEMENT CREDIT", "MINIMUM PAYMENT"). Each is printed in every way a row is printed: with its
date (numeric or named), with a sign, with and without a balance column, in a text file, on a
PDF page and in a banking app's screenshot.

The expectation is the rule itself and nothing else: the line is in what is sent or in what is
reported. It doesn't ask the text prep how it classifies a line."""

from __future__ import annotations

import pytest

from tuppence.ingest.models import Document
from tuppence.ingest.textprep import pages_document, text_document

INTEREST = [
    "INTEREST", "INTEREST PAID", "INTEREST CHARGED", "INTEREST CHARGE", "GROSS INTEREST",
    "NET INTEREST", "CREDIT INTEREST", "DEBIT INTEREST", "MONTHLY INTEREST", "BONUS INTEREST",
    "INTEREST EARNED", "INTEREST ON CREDIT BALANCE", "INTEREST ON YOUR BALANCE",
    "INTEREST ON BALANCE", "BALANCE INTEREST", "CREDIT BALANCE INTEREST",
    "DEBIT INTEREST ON OVERDRAWN BALANCE", "OVERDRAFT INTEREST", "ARRANGED OVERDRAFT INTEREST",
    "UNARRANGED OVERDRAFT INTEREST", "INTEREST ON PURCHASES", "PURCHASE INTEREST",
    "CASH INTEREST", "CASH ADVANCE INTEREST", "BALANCE TRANSFER INTEREST", "CLOSING INTEREST",
    "INTEREST ON AVAILABLE FUNDS", "CLEARED FUNDS INTEREST", "PAYMENT DUE INTEREST",
    "ISA INTEREST", "SAVINGS INTEREST", "INTEREST ADJUSTMENT",
]  # fmt: skip
FEES = [
    "ACCOUNT FEE", "MONTHLY ACCOUNT FEE", "MONTHLY FEE", "PACKAGED ACCOUNT FEE", "OVERDRAFT FEE",
    "OVERDRAFT ARRANGEMENT FEE", "OVERDRAFT USAGE FEE", "OVERDRAWN BALANCE FEE",
    "UNPAID ITEM FEE", "RETURNED DIRECT DEBIT FEE", "LATE PAYMENT FEE", "OVER LIMIT FEE",
    "ANNUAL FEE", "CARD FEE", "REPLACEMENT CARD FEE", "CASH ADVANCE FEE", "CASH FEE",
    "BALANCE TRANSFER FEE", "MONEY TRANSFER FEE", "FOREIGN TRANSACTION FEE",
    "NON-STERLING TRANSACTION FEE", "NON-STERLING CASH FEE", "STATEMENT FEE",
    "PAPER STATEMENT FEE", "CHAPS FEE", "AMOUNT DUE FEE", "OVERDRAFT LIMIT FEE",
]  # fmt: skip
CHARGES = [
    "SERVICE CHARGE", "SERVICE CHARGES", "ACCOUNT CHARGES", "BANK CHARGES", "COMMISSION CHARGE",
    "DEBIT CARD CHARGE", "TFL TRAVEL CHARGE", "CONGESTION CHARGE",
]  # fmt: skip
PAYMENTS = [
    "PAYMENT", "PAYMENT RECEIVED", "PAYMENT RECEIVED - THANK YOU", "PAYMENT THANK YOU",
    "THANK YOU FOR YOUR PAYMENT", "DIRECT DEBIT PAYMENT", "PAYMENT BY DIRECT DEBIT",
    "DIRECT DEBIT PAYMENT TO CARD", "MINIMUM PAYMENT", "MINIMUM PAYMENT DIRECT DEBIT",
    "STATEMENT BALANCE PAYMENT", "PAYMENT OF MINIMUM AMOUNT DUE", "TOTAL AMOUNT DUE PAYMENT",
    "FULL BALANCE PAYMENT", "OUTSTANDING BALANCE PAYMENT", "BALANCE PAYMENT", "CARD PAYMENT",
    "CARD PAYMENT TO GREENBASKET STORES", "CONTACTLESS PAYMENT", "FASTER PAYMENT",
    "FASTER PAYMENT RECEIVED", "FASTER PAYMENT TO J SMITH", "BILL PAYMENT",
    "BILL PAYMENT TO CITY WATER", "ONLINE PAYMENT", "MOBILE PAYMENT", "PAYMENT TO CREDIT CARD",
    "CREDIT CARD PAYMENT", "BARCLAYCARD PAYMENT", "AMEX PAYMENT", "BANK GIRO CREDIT",
    "BGC ACME PAYROLL LTD", "BACS PAYMENT", "BACS CREDIT ACME PAYROLL", "CHAPS PAYMENT",
    "PAYMENT IN", "PAYMENT OUT", "PAYMENTS", "GOODWILL PAYMENT", "COMPENSATION PAYMENT",
]  # fmt: skip
REFUNDS = [
    "REFUND", "REFUND FROM HARBOUR PHARMACY", "HARBOUR PHARMACY REFUND", "PURCHASE REFUND",
    "CARD REFUND", "PARTIAL REFUND", "FEE REFUND", "INTEREST REFUND", "REFUND OF CHARGES",
    "CHARGEBACK", "CHARGEBACK CREDIT", "AMAZON REFUND", "HMRC TAX REFUND", "COUNCIL TAX REFUND",
    "CREDIT BALANCE REFUND",
]  # fmt: skip
CREDITS = [
    "STATEMENT CREDIT", "AMEX OFFER STATEMENT CREDIT", "OFFER STATEMENT CREDIT",
    "STATEMENT ADJUSTMENT", "PREVIOUS STATEMENT ADJUSTMENT", "CREDIT ADJUSTMENT",
    "DEBIT ADJUSTMENT", "BALANCE ADJUSTMENT", "GOODWILL CREDIT", "PROMOTIONAL CREDIT",
    "REWARD CREDIT", "ACCOUNT CREDIT", "CREDIT", "DEBIT", "CREDIT VOUCHER", "SWITCHING BONUS",
    "OPENING DEPOSIT",
]  # fmt: skip
CASHBACK = [
    "CASHBACK", "CASHBACK REWARD", "CASHBACK CREDIT", "ANNUAL CASHBACK", "MONTHLY CASHBACK",
    "CASHBACK ON PURCHASES", "AMEX CASHBACK", "REWARDS CASHBACK",
]  # fmt: skip
TRANSFERS = [
    "TRANSFER", "TRANSFER TO SAVINGS", "TRANSFER FROM SAVINGS", "TFR TO ISA",
    "TFR FROM EASY SAVER", "INTERNAL TRANSFER", "BALANCE TRANSFER", "MONEY TRANSFER",
    "BANK TRANSFER", "INTERNATIONAL TRANSFER", "MOBILE TRANSFER", "ONLINE TRANSFER",
    "TRANSFER IN", "TRANSFER OUT", "TRANSFER FROM J SMITH", "TRANSFER TO JOINT ACCOUNT",
    "TRANSFER TO CREDIT CARD", "AVAILABLE FUNDS TRANSFER", "TFR J SMITH RENT",
]  # fmt: skip
POTS = [
    "SAVINGS POT", "TRANSFER TO POT", "TRANSFER FROM POT", "HOLIDAY POT", "BILLS POT",
    "TO SAVINGS POT", "FROM SAVINGS POT", "ROUND UP", "ROUND UPS", "SAVINGS SPACE",
    "SPACE TRANSFER", "VAULT TRANSFER", "GOAL CONTRIBUTION", "SAVINGS GOAL", "ISA TRANSFER",
    "EASY SAVER", "RAINY DAY POT",
]  # fmt: skip
STANDING_ORDERS = [
    "STANDING ORDER", "STANDING ORDER TO LANDLORD", "STANDING ORDER RENT", "S/O RENT",
    "SO RENT", "STO J SMITH", "STANDING ORDER - SAVINGS", "STANDING ORDER TO SAVINGS",
    "STANDING ORDER FROM J SMITH", "REGULAR PAYMENT TO J SMITH", "SO CHURCH DONATION",
]  # fmt: skip
DIRECT_DEBITS = [
    "DIRECT DEBIT", "DD CITY WATER", "DIRECT DEBIT TO HOME COVER LTD", "D/D TV LICENCE",
    "DD COUNCIL TAX", "DIRECT DEBIT COUNCIL TAX", "DD GYM MEMBERSHIP", "DDR CITY WATER",
    "DIRECT DEBIT RETURNED", "RETURNED DIRECT DEBIT", "UNPAID DIRECT DEBIT",
    "DIRECT DEBIT INDEMNITY CLAIM", "DD MOBILE PHONE", "DD ENERGY SUPPLIER", "DD CAR INSURANCE",
    "DD MORTGAGE",
]  # fmt: skip
CASH = [
    "CASH", "CASH WITHDRAWAL", "CASH MACHINE", "CASH MACHINE WITHDRAWAL", "ATM WITHDRAWAL",
    "CASH WITHDRAWAL AT BRANCH", "LINK ATM", "CASHPOINT", "CASH DEPOSIT", "CASH PAID IN",
    "CASH PAID IN AT BRANCH", "CASH IN AT POST OFFICE", "POST OFFICE CASH DEPOSIT",
    "POST OFFICE WITHDRAWAL", "CASH ADVANCE", "NON-STERLING CASH WITHDRAWAL",
    "CASH BACK AT TILL",
]  # fmt: skip
CHEQUES = [
    "CHEQUE", "CHEQUE 000123", "CHQ 100234", "CHEQUE DEPOSIT", "CHEQUE PAID IN", "CHEQUE IN",
    "CHEQUE RETURNED", "RETURNED CHEQUE", "COUNTER CREDIT", "CHQ PAID IN",
    "CHEQUE IMAGING DEPOSIT", "BANKERS DRAFT",
]  # fmt: skip
# Descriptions made only of the words a balance or a summary uses.
SUMMARY_WORDS = [
    "MONEY IN", "MONEY OUT", "PAID IN", "PAID OUT", "CREDIT ON ACCOUNT", "STATEMENT CREDIT",
    "MINIMUM PAYMENT", "INTEREST ON BALANCE", "STATEMENT BALANCE PAYMENT",
]  # fmt: skip
MERCHANTS = [
    "GREENBASKET STORES", "LITTLE CAFE", "NORTHLINE RAIL", "CITY WATER", "HOME COVER LTD",
    "ACME PAYROLL LTD", "TESCO STORES 2345", "7-ELEVEN", "3 MOBILE", "B&Q 0123",
    "PAYPAL *EBAY 12345", "PRET A MANGER 0016", "COSTA 43021", "O2 UK", "99P STORES",
    "NETFLIX.COM", "AMAZON MARKETPLACE", "UBER *TRIP", "DVLA VEHICLE TAX",
    "HMRC SELF ASSESSMENT", "COUNCIL TAX", "TV LICENCE", "SALARY", "WAGES", "PENSION",
    "CHILD BENEFIT", "UNIVERSAL CREDIT", "WORKING TAX CREDIT", "STUDENT LOAN", "MORTGAGE",
    "RENT", "LOAN REPAYMENT", "CAR FINANCE",
]  # fmt: skip

DESCRIPTIONS = list(
    dict.fromkeys(
        [*INTEREST, *FEES, *CHARGES, *PAYMENTS, *REFUNDS, *CREDITS, *CASHBACK, *TRANSFERS,
         *POTS, *STANDING_ORDERS, *DIRECT_DEBITS, *CASH, *CHEQUES, *SUMMARY_WORDS, *MERCHANTS]
    )
)  # fmt: skip

# Each way a row is printed. `{d}` is the description.
TABLE_FORMS = {
    "dated": "02/10/2026 {d} 12.34",
    "dated, balance": "02/10/2026 {d} 12.34 1,012.34",
    "dated, signed": "02/10/2026 {d} -12.34",
    "dated, signed credit": "02/10/2026 {d} +12.34",
    "dated, signed, balance": "02/10/2026 {d} -12.34 987.66",
    "dated, £": "02/10/2026 {d} £12.34",
    "dated, CR": "02/10/2026 {d} 12.34 CR",
    "named date": "02 Oct 2026 {d} 12.34",
    "named date, balance": "02 Oct {d} 12.34 1,012.34",
    "short date, signed": "02/10/26 {d} -12.34",
    "undated, balance": "{d} 12.34 1,012.34",
    "undated, signed": "{d} -12.34",
    "undated, signed credit, balance": "{d} +12.34 1,012.34",
    "undated": "{d} 12.34",
}
SCREENSHOT_FORMS = {
    "signed": "{d} -£12.34",
    "signed credit": "{d} +£12.34",
    "dated": "Mon 2 Oct {d} -£12.34",
    "dated credit": "2 Oct {d} +£12.34",
    "unsigned": "{d} £12.34",
}
# An undated line with one unsigned figure reads, for these descriptions, word for word as a
# label in a statement's summary box ("Minimum payment £25.00", "Money in £2,000.00"): such a
# line is a summary by definition (R-M3-23 (e)), so these two forms don't ask it of them.
UNDATED_UNSIGNED = {"undated", "unsigned"}
LABEL_TWINS = {"MINIMUM PAYMENT", "MONEY IN", "MONEY OUT", "PAID IN", "PAID OUT"}

HEAD = [
    "Example Bank plc",
    "Statement period 01/10/2026 to 31/10/2026",
    "Date Description Paid out Paid in Balance",
    "01/10/2026 GREENBASKET STORES 42.18 957.82",
]
SHOT_HEAD = ["Transactions", "Mon 2 Oct", "TESCO -£12.50"]


def _expected(form: str) -> list[str]:
    if form in UNDATED_UNSIGNED:
        return [d for d in DESCRIPTIONS if d not in LABEL_TWINS]
    return DESCRIPTIONS


def _lost(doc: Document, first: int, descriptions: list[str]) -> list[str]:
    """Descriptions whose line is neither sent nor reported. Line `first + k` holds the k-th."""
    refs = [line.ref for line in doc.lines]
    shown = set(doc.data_refs) | set(doc.held_amount_refs)
    return [d for k, d in enumerate(descriptions) if refs[first + k] not in shown]


def test_the_corpus_is_broad():
    assert len(DESCRIPTIONS) >= 200
    for group in (INTEREST, FEES, CHARGES, PAYMENTS, REFUNDS, CREDITS, CASHBACK, TRANSFERS,
                  POTS, STANDING_ORDERS, DIRECT_DEBITS, CASH, CHEQUES, SUMMARY_WORDS):  # fmt: skip
        assert len(group) >= 5


@pytest.mark.parametrize("path", ["text", "pdf"])
@pytest.mark.parametrize("form", list(TABLE_FORMS))
def test_every_row_in_a_statement_is_sent_or_reported(form, path):
    descriptions = _expected(form)
    rows = [TABLE_FORMS[form].format(d=d) for d in descriptions]
    lines = [*HEAD, *rows, "31/10/2026 LITTLE CAFE 3.00 954.82"]
    if path == "text":
        doc = text_document("\n".join(lines), sha256="x")
    else:
        doc = pages_document([lines], sha256="x", kind="pdf")
    lost = _lost(doc, len(HEAD), descriptions)
    if lost:
        pytest.fail(f"{len(lost)} rows withheld without a word ({form}, {path}): {lost}")


@pytest.mark.parametrize("form", list(SCREENSHOT_FORMS))
def test_every_row_in_a_screenshot_is_sent_or_reported(form):
    descriptions = _expected(form)
    rows = [SCREENSHOT_FORMS[form].format(d=d) for d in descriptions]
    doc = pages_document([[*SHOT_HEAD, *rows, "LITTLE CAFE -£3.00"]], sha256="x", kind="image")
    lost = _lost(doc, len(SHOT_HEAD), descriptions)
    if lost:
        pytest.fail(f"{len(lost)} rows withheld without a word ({form}, screenshot): {lost}")


# The reviewer's p_rowwords probe as it was run: one row between two others, each form.
ROW_WORDS = [
    "INTEREST ON CREDIT BALANCE", "CREDIT BALANCE INTEREST", "DEBIT INTEREST ON OVERDRAWN BALANCE",
    "INTEREST ON BALANCE", "BALANCE INTEREST", "CLOSING INTEREST", "STATEMENT CREDIT",
    "STATEMENT BALANCE PAYMENT", "MINIMUM PAYMENT", "PAYMENT OF MINIMUM AMOUNT DUE",
    "INTEREST ON AVAILABLE FUNDS", "CLEARED FUNDS INTEREST", "INTEREST ON YOUR BALANCE",
    "TOTAL AMOUNT DUE PAYMENT", "MINIMUM PAYMENT DIRECT DEBIT", "PAYMENT DUE INTEREST",
]  # fmt: skip
PROBE_HEAD = [
    "Example Bank",
    "Statement 01/10/2026 to 31/10/2026",
    "Date Description Paid out Paid in Balance",
    "01/10/2026 SHOP 4.00 1,000.00",
]


@pytest.mark.parametrize("description", ROW_WORDS)
def test_the_reviewers_rows_of_summary_words_are_never_lost(description):
    forms = [f"02/10/2026 {description} 10.00", f"02/10/2026 {description} 10.00 1,010.00",
             f"{description} 10.00 1,010.00"]  # fmt: skip
    for line in forms:
        doc = text_document(
            "\n".join([*PROBE_HEAD, line, "03/10/2026 CAFE 3.00 1,007.00"]), sha256="x"
        )
        assert "L5" in doc.data_refs or "L5" in doc.held_amount_refs, line
    shots = [f"{description} -£10.00"]
    if description not in LABEL_TWINS:
        shots.append(f"{description} £10.00")
    for line in shots:
        shot = pages_document(
            [["Mon 2 Oct", "TESCO -£12.50", line, "CAFE -£3.00"]], sha256="x", kind="image"
        )
        assert "P1L3" in shot.data_refs or "P1L3" in shot.held_amount_refs, line


@pytest.mark.parametrize(
    ("description", "amount"),
    [("STATEMENT CREDIT", "+10.00"), ("INTEREST ON BALANCE", "+0.12"),
     ("BALANCE INTEREST", "+0.12"), ("CLOSING INTEREST", "+1.20"),
     ("INTEREST ON CREDIT BALANCE", "+0.12"), ("CREDIT BALANCE INTEREST", "+0.12")],
)  # fmt: skip
def test_the_reviewers_signed_statement_keeps_every_row(ingest_env, description, amount):
    """test_rr2_silent: "02/10/2026 INTEREST ON BALANCE +0.12" was dropped without a message
    and 2 of 3 rows imported. Now every row is imported, or the line is reported."""
    from ingest.helpers import add_account, drain, use_local_model

    services, _ = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "savings", "Probe")
    body = "\n".join(
        ["Example Bank plc", "Statement period 01/10/2026 to 31/10/2026", "Date Description Amount",
         "01/10/2026 GREENBASKET STORES -42.18", f"02/10/2026 {description} {amount}",
         "05/10/2026 LITTLE CAFE -3.50"]
    )  # fmt: skip
    out = services.ingest.upload("signed.txt", (body + "\n").encode())
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(out.record.id)
    if record.status == "imported":
        rows = [t.raw_description for t in services.statements.transactions(record.id)]
        assert description in rows and len(rows) == 3
    else:
        assert record.status == "needs_review"
        assert any("held back from the AI" in e for e in record.check_errors)
