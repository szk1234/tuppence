"""R-M3-23 (e), re-review N6, m1 and m2: no line with an amount is withheld without being reported
unless it is a pure balance line, in the text path and the screenshot path alike.

A pure balance line is nothing but balance or summary wording and its figures: a balance in any
wording ("Remaining balance £250.00", "BAL B/FWD 1,000.00", m1), a card header's payment
sentence ("Your minimum payment of £25.00 is due by 20 November 2026", m2), or a total with
each figure under its own label ("Money in £2,000.00 Money out £438.78"). Anything that may be
a row (payee words, a figure with a sign, an amount and a balance side by side, a date) is
reported for the person to decide."""

from __future__ import annotations

import pytest

from tuppence.ingest import textprep
from tuppence.ingest.textprep import has_amount, pages_document, text_document

HEAD = ["Example Bank", "Statement 01/10/2026 to 31/10/2026", "Date Description Paid out Paid in Balance",
        "01/10/2026 SHOP 4.00 1,000.00"]  # fmt: skip
SHOT = ["Mon 2 Oct", "TESCO -£12.50"]

# The reviewer's p_held probe: withheld and dropped before; each may be a row.
HELD_TEXT = [
    "PAID IN AT POST OFFICE 50.00 1,100.00",
    "CASH PAID IN 50.00 1,100.00",
    "MONEY IN FROM J SMITH 25.00 1,125.00",
    "TOTAL 12.50 1,087.50",
    "CREDIT 900.00 1,900.00",
    "02/10/2026 CASH PAID IN AT BRANCH 50.00 1,050.00",
    "02/10/2026 TOTAL 12.50 1,087.50",
    "02/10/2026 CREDIT 900.00 1,900.00",
    "SAVINGS 50.00 1,000.00",
    "Savings pot 50.00 1,000.00",
    "ROUND UP 0.43 999.57",
    "PAYMENTS 190.00",
]
HELD_SHOT = [
    "Paid in at Post Office +£50.00",
    "Money in +£25.00",
    "Savings pot -£50.00",
    "Round up -£0.43",
]
# Rows that go to the reader as they are.
SENT = [
    "INTEREST PAID 0.12 1,000.12",
    "PAYMENTS RECEIVED 250.00 750.00",
    "02/10/2026 Savings pot 50.00 1,000.00",
    "Transfer to Savings pot 50.00 1,000.00",
    "OVERDRAFT INTEREST 3.21 996.79",
    "DIRECT DEBIT 25.00 975.00",
    "CARD PAYMENT 12.50 987.50",
    "INTEREST 4.80 995.20",
    "BALANCE TRANSFER FEE 5.00 995.00",
    "02/10/2026 YOUR PAYMENT 25.00 975.00",
]
# m1: balance wording outside the old vocabulary; never sent, never reported.
BALANCE_WORDS = [
    "Balance after this transaction 1,234.56", "Cleared funds £900.00", "Remaining balance £250.00",
    "Bal fwd 1,000.00", "Balance carried fwd 1,234.56", "BAL B/FWD 1,000.00",
    "Account balance as of today £1,234.56", "Balance due £250.00", "Pending balance £1,180.00",
    "Balance on card £250.00", "Balance after pending £1,180.00",
    "Balance excluding pending £1,200.00", "Overdraft remaining £300.00",
    "Spendable balance £900.00", "Balance to pay £250.00", "Outstanding amount on card £250.00",
    "Closing bal 1,234.56", "Bal c/fwd 1,234.56", "Total spent £12.50",
]  # fmt: skip
# m2: a card header's payment sentences; they don't send every card statement to review.
CARD_SENTENCES = [
    "Your minimum payment of £25.00 is due by 20 November 2026",
    "Your minimum payment is £25.00 and is due by 20 Nov 2026",
    "We'll collect your Direct Debit of £25.00 on 20 November 2026",
    "Minimum payment £25.00 Payment date 20/11/2026",
    "Please pay £25.00 by 20/11/2026",
    "Pay by 20/11/2026 to avoid interest on £250.00",
    "Direct Debit of £25.00 will be collected on 20/11/2026",
    "Amount to pay 20/11/2026 £25.00",
    "As of 31/10/2026 you owe £250.00",
    "Credit available £2,750.00 at 05/11/2026",
    "Statement Balance on 31/10/2026 is £2,252.32",
    "Estimated interest next statement 05/12/2026 £3.21",
]


def _text(line: str):
    doc = text_document("\n".join([*HEAD, line, "03/10/2026 CAFE 3.00 997.00"]), sha256="x")
    return doc, f"L{len(HEAD) + 1}"


def _shot(line: str):
    doc = pages_document([[*SHOT, line, "CAFE -£3.00"]], sha256="x", kind="image")
    return doc, f"P1L{len(SHOT) + 1}"


@pytest.mark.parametrize("line", HELD_TEXT)
def test_a_line_that_may_be_a_row_is_reported_in_text(line):
    doc, ref = _text(line)
    assert ref not in doc.data_refs and ref in doc.held_amount_refs


@pytest.mark.parametrize("line", HELD_SHOT)
def test_a_line_that_may_be_a_row_is_reported_in_a_screenshot(line):
    doc, ref = _shot(line)
    assert ref not in doc.data_refs and ref in doc.held_amount_refs


@pytest.mark.parametrize("line", SENT)
def test_rows_with_summary_words_are_still_sent(line):
    doc, ref = _text(line)
    assert ref in doc.data_refs


@pytest.mark.parametrize("path", [_text, _shot], ids=["text", "screenshot"])
@pytest.mark.parametrize("line", BALANCE_WORDS)
def test_balance_wording_is_never_sent_nor_reported(line, path):
    doc, ref = path(line)
    assert ref not in doc.data_refs and ref not in doc.held_amount_refs


@pytest.mark.parametrize("sentence", CARD_SENTENCES)
def test_a_card_headers_payment_sentence_is_not_held_for_review(sentence):
    page = ["Example Card plc", "Statement date 05/11/2026", sentence, "MR ALEX EXAMPLE",
            "Flat 3", "Example House", "Exampletown", "EX1 2MP", "Date Description Amount",
            "01 Oct 2026 Greenbasket Stores 42.18", "03 Oct 2026 Little Cafe 3.50"]  # fmt: skip
    doc = pages_document([page], sha256="x", kind="pdf", names=["Alex Example"])
    assert doc.held_amount_refs == []
    assert [doc.by_ref()[r].text for r in doc.data_refs] == page[-3:]


@pytest.mark.parametrize("sentence", CARD_SENTENCES)
def test_the_same_sentence_in_the_table_is_withheld_not_sent(sentence):
    doc, ref = _text(sentence)
    assert ref not in doc.data_refs and ref not in doc.held_amount_refs


def _corpus() -> list[str]:
    from ingest.test_balance_lines import BALANCES, ROWS_STILL_SENT, UNSURE
    from ingest.test_long_numbers import N3
    from ingest.test_sensitive_parity import EVERY, PLAIN, REGRESSION, WITH_DETAILS
    from ingest.test_textprep import MIXED_SUMMARIES, OUTCOMES

    return list(dict.fromkeys([
        *EVERY, *PLAIN, *REGRESSION, *WITH_DETAILS, *BALANCES, *UNSURE, *ROWS_STILL_SENT,
        *MIXED_SUMMARIES, *(line for line, _ in OUTCOMES), *N3, *HELD_TEXT, *HELD_SHOT, *SENT,
        *BALANCE_WORDS, *CARD_SENTENCES,
    ]))  # fmt: skip


@pytest.mark.parametrize("path", [_text, _shot], ids=["text", "screenshot"])
def test_nothing_with_an_amount_is_withheld_unreported_unless_a_pure_balance_line(path):
    """The rule itself, over every line the corpora hold, past the table start."""
    lost = []
    for line in _corpus():
        doc, ref = path(line)
        text = doc.by_ref()[ref].text
        if ref in doc.data_refs or ref in doc.held_amount_refs or not has_amount(text):
            continue
        if not textprep.pure_balance(text, names=()):
            lost.append(line)
    assert lost == []


@pytest.mark.parametrize("line", [*HELD_TEXT, *HELD_SHOT, *SENT])
def test_a_line_that_may_be_a_row_is_never_a_pure_balance_line(line):
    assert not textprep.pure_balance(line, names=())
