"""One classifier, three filters: every sensitive spelling is withheld by text prep, hidden by
CSV layout learning and withheld from screenshots. All values invented."""

import re

import pytest

from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.mapping import HIDDEN, cell_token, header_problem, shown_heading, sketch
from tuppence.ingest.models import Line
from tuppence.ingest.sensitive import classify, holds_details, mask
from tuppence.ingest.textprep import pages_document, split_preamble

NAMES = ["Alex Example"]  # the household's own names

CORPUS = [
    # sort codes, with every separator
    "12-34-56",
    "12 34 56",
    "12.34.56",
    "12–34–56",
    "12—34—56",
    "Sort code 404784",
    "sort code: 12 34 56",
    "s/c: 404784",
    "S/C 12 34 56",
    "s.c. 12-34-56",
    "Transfer 12.34.56 87654321",
    # account numbers: plain, spaced, signed, short
    "Account 12345678",
    "Account 1234 5678",
    "A/C -12345678",
    "Acct -71004",
    "Acc no 71004",
    "Account: 20261001",
    "Account number 12341225",
    # cards: full, masked, endings
    "4929 1234 5678 9012",
    "4929123456789012",
    "************4242",
    "**** 1234",
    "XXXX-XXXX-XXXX-4242",
    "4929 **** **** 4242",
    "Card ending 4242",
    "ending in 4242",
    "Visa ****1234",
    "Card no. 4929",
    # IBANs and BICs
    "GB29 NWBK 6016 1331 9268 19",
    "GB29NWBK60161331926819",
    "gb29 nwbk 6016 1331 9268 19",
    "BIC NWBKGB2L",
    # holder names: titled, introduced, and untitled ones that are the household's own
    "Mr Alex Example",
    "Statement for Alex Example",
    "Prepared for A Example",
    "Alex Example",
    "ALEX EXAMPLE",
    "A EXAMPLE",
    "A. Example",
    # postcodes and addresses
    "EX1 2MP",
    "Exampleton EX1 2MP",
    "1 Example Road",
    "12a Example Close",
    # customer and membership numbers
    "Customer number 998877",
    "Roll number 4455",
    "Membership no. 1234567",
    # balance lines: a label and one figure, nothing else (R-M3-16)
    "Balance £1,234.56",
    "Balance: -£12.00",
    "Available balance £1,184.56",
    "Current balance 105.00 D",
    "Available to spend £50.00",
    "Balance after £957.82",
    "Overdraft limit £500.00",
    "Credit limit £3,000.00",
    "New balance 909.85",
    "Previous balance (100.00)",
    # wider balance lines (R-M3-17): label variants, currency codes, no thousands separator,
    # a figure followed by its label, two figures on one line
    "Balance 1234.56",
    "Balance £1184.56",
    "Available balance £1184.56",
    "Balance: GBP 1,234.56",
    "Balance GBP 1234.56",
    "Balance 1,234.56 GBP",
    "Balance: 1234.56",
    "Your balance £1,234.56",
    "Your available balance £1,184.56",
    "Total balance £1,234.56",
    "Statement balance £1,234.56",
    "Outstanding balance £1,234.56",
    "Current balance: £1,234.56 CR",
    "Account balance £1,234.56",
    "Spending balance £1,234.56",
    "Available credit £1,234.56",
    "Available to spend: £1,184.56",
    "Balance £ 1,234.56",
    "Balance £1 234.56",
    "Balance owing £1,234.56",
    "Arranged overdraft £500.00",
    "Arranged overdraft limit £500.00",
    "£1,184.56 available",
    "£1,184.56 Available balance",
    "Balance £1,234.56 Available £1,184.56",
    "Balance £1,234.56 · Available £1,184.56",
    "Opening balance GBP 1,000.00",
    "Closing balance 1780.00 GBP",
    # card endings behind any mask characters (N5)
    "Card •••• 4242",
    "Mastercard •••• 4242",
    "•••• 4242",
    "Visa Debit ...4242",
    "...4242",
    "Card …4242",
    "…4242",
    "Card ·· 4242",
    "**4242",
    "**** 4242",
    "xxxx4242",
    "Debit card ●●●● 4242",
    # rarer spellings (re-review 2, m6)
    "SC 100520",
    "AC 12345678",
    "a/c ending 678",
    "SWIFT NWBKGB2L",
    "Swift code: NWBKGB2L",
    "Card 4929   1234   5678   9012",
    "Card 4929  1234  5678  9012",
    "Name: Alex Example",
    "Joint account: Alex Example & Sam Example",
    "Mr & Mrs A Example",
    "Example, Alex",
    "EXAMPLE A",
    "Customer reference 99887766",
    # header summaries with a date between the label and the figure, and a summary box merged
    # onto an address row (I1)
    "Balance on 31/10/2026 £2,252.32",
    "Your balance at 31 Oct 2026: £2,252.32",
    "Payment due 20/11/2026  Minimum payment £25.00",
    "Payment due by 20/11/2026: minimum payment £25.00",
    "Statement date 05/11/2026   Overdraft limit £500.00",
    "Flat 3   Minimum payment £25.00",
    "Minimum payment £25.00",
    "Minimum payment due £25.00",
    "Balance as at 31/10/2026 £2,252.32",
    # addresses without a street word
    "Flat 3",
    "Apartment 12",
    "Apt 4B",
]
# The corpus R-M3-7 added at 8ca14cc, unchanged.
SPELLINGS_8CA14CC = [
    "s/c: 123456",
    "S/C 12 34 56",
    "s.c. 12-34-56",
    "A/C 12345678",
    "Acc no 12345678",
    "Acc: 12345678",
    "Account: 12345678",
    "Account 1234 5678",
    "Account holders: A Example and B Example",
    "Account holder: A Example",
    "Customer number 998877",
    "Customer no. 998877",
    "Membership number 1234567",
    "Roll number 4455",
    "4929 **** **** 4242",
    "4929 1234 5678 4242",
    "**** 1234",
    "****1234",
    "Card ****1234",
    "Visa ****1234",
    "xxxx-xxxx-xxxx-1234",
    "Card XXXX 4242",
    "Card ending in 4242",
    "Sort code 12-34-56",
]
EVERY = list(dict.fromkeys(CORPUS + SPELLINGS_8CA14CC))
# Transaction lines and repeated plain descriptions (R-M3-7): never withheld for being sensitive.
PLAIN = [
    "Salary",
    "Tesco Stores",
    "Faster payment",
    "01/10/2026 GREENBASKET STORES 42.18 957.82",
    "01.10.26 LITTLE CAFE 3.40",
    "05 10 26 HOMEWARE DIRECT 43.27",
    "02 Oct 2026 Shop 4.00",
    "Date Description Paid out Paid in Balance",
    "TRANSFER FROM ALEX EXAMPLE 50.00",
    "BALANCE TRANSFER £100.00",
    "BALANCE TRANSFER 1234.56",
    "5 Oct Balance £20.00",
    "01/10/2026 Balance 1,234.56",
    "Balance",
    "Available",
    "Available balance",
    "Balance (£)",
    "Balance GBP",
    "-£12.80",
    "Little Cafe -£12.80",
    "Coffee... 3.40",
    "INV...1234.56",
    "Ref ...£1,234.56",
    "SWIFT TAXIS 12.00",
    "Swift deliveries",
    "AC MILAN STORE",
    "Name",
    "JOINT ACCOUNT TRANSFER 50.00",
    "05 10 26   400   12.30",
    # column vocabulary is not a titled name (R-M3-16)
    "Dr Amount",
    "Cr Amount",
    "DR/CR",
    "Dr",
    "Ref",
    "Debit",
    "Credit",
    # transactions that use the I1 header words (the date comes first)
    "01/10/2026 Minimum payment 25.00",
    "20/11/2026 Payment due 25.00",
    "FLAT WHITE 3.40",
    "Flat 5.00 fee",
    "Payment due",
]
# Deliberate narrowings of the 8ca14cc list: column names it took for a titled name.
NARROWED = {"Dr Amount"}


def withheld_by_textprep(text):
    lines = [
        Line(ref=f"P1L{i}", text=t)
        for i, t in enumerate(
            ["Date Description Amount", "02 Oct 2026 Shop 4.00", text, "03 Oct 2026 Cafe 3.00"],
            start=1,
        )
    ]
    withheld, _ = split_preamble(lines, names=NAMES)
    return "P1L3" in withheld


def withheld_from_screenshot(text):
    doc = pages_document(
        [["5 Oct SHOP -£3.40", text, "6 Oct CAFE -£1.00"]], sha256="x", kind="image", names=NAMES
    )
    return "P1L2" in doc.preamble_refs


@pytest.mark.parametrize("text", EVERY)
def test_every_filter_catches_every_spelling(text):
    assert classify(text, names=NAMES)
    assert withheld_by_textprep(text)
    assert shown_heading(text, names=NAMES) == HIDDEN
    assert cell_token(text, names=NAMES) == HIDDEN
    assert mask(text, names=NAMES) == HIDDEN
    assert withheld_from_screenshot(text)
    sent = sketch(["Date", "Description", text], [["01/10/2026", text, text]], names=NAMES)
    assert text not in sent


# A balance label on one line and its figure on the next (or above it): both lines are held
# back, wherever they are (R-M3-17).
PAIRS = [
    ["Available balance", "£1,184.56"],
    ["Balance", "£1,184.56"],
    ["Balance:", "1,184.56"],
    ["Your balance", "1184.56 GBP"],
    ["Available to spend", "-£12.00"],
    ["£1,184.56", "Available balance"],
    ["£1,184.56", "Available"],
]


def held_amounts(rows, kind):
    doc = page(rows, kind)
    by_ref = doc.by_ref()
    return [by_ref[r].text for r in doc.held_amount_refs]


def page(rows, kind):
    if kind == "image":
        return pages_document(
            [["5 Oct SHOP -£3.40", *rows, "6 Oct CAFE -£1.00"]], sha256="x", kind="image"
        )
    return pages_document(
        [["Date Description Amount", "02 Oct 2026 Shop 4.00", *rows, "03 Oct 2026 Cafe 3.00"]],
        sha256="x",
        kind="pdf",
    )


def withheld_lines(rows, kind):
    if kind == "image":
        doc = pages_document(
            [["5 Oct SHOP -£3.40", *rows, "6 Oct CAFE -£1.00"]], sha256="x", kind="image"
        )
    else:
        doc = pages_document(
            [["Date Description Amount", "02 Oct 2026 Shop 4.00", *rows, "03 Oct 2026 Cafe 3.00"]],
            sha256="x",
            kind="pdf",
        )
    by_ref = doc.by_ref()
    return [by_ref[r].text for r in doc.preamble_refs]


@pytest.mark.parametrize("kind", ["pdf", "image"])
@pytest.mark.parametrize("rows", PAIRS, ids=" / ".join)
def test_a_balance_label_and_its_figure_on_separate_lines_are_both_held_back(rows, kind):
    assert all(row in withheld_lines(rows, kind) for row in rows)
    assert held_amounts(rows, kind) == []  # plainly a balance: nothing to report


@pytest.mark.parametrize("kind", ["pdf", "image"])
def test_a_figure_that_may_be_a_rows_amount_is_held_back_and_counted(kind):
    """N6: "-£12.80" sits under its row's description and above a balance label with a figure
    under it. Either figure may be the balance, so both are held back, and both count as
    possible transactions: the statement needs a look rather than losing the row."""
    rows = ["Coffee shop", "-£12.80", "Balance", "£1,171.76"]
    assert {"-£12.80", "Balance", "£1,171.76"} <= set(withheld_lines(rows, kind))
    assert set(held_amounts(rows, kind)) == {"-£12.80", "£1,171.76"}


@pytest.mark.parametrize(
    "text",
    [
        "Balance" + " " * 20_000 + "x",
        "Account" + " " * 20_000 + "x",
        "Sort code" + " " * 20_000 + "x",
        "SWIFT code" + " " * 20_000 + "x",
        "Mr" + " " * 20_000 + "&" + " " * 20_000 + "x",
        "••••" + " " * 20_000 + "x",
        # N8: long runs of mask characters or digit groups
        "•" * 20_000 + "x",
        "·" * 20_000 + "x",
        "." * 20_000 + "x",
        "*" * 20_000 + "x",
        "Balance £" + "123," * 5_000 + "x",
        "Balance £" + "123 " * 5_000 + "x",
        "(" + " " * 20_000 + "x",
        "£1,184.56" + " " * 20_000 + "x",
        "Available balance £1.00" + " " * 20_000 + "x",
    ],
)
def test_the_classifier_takes_linear_time_on_long_gaps(text):
    import time

    start = time.monotonic()
    classify(text, names=NAMES)
    withheld_by_textprep(text)
    withheld_from_screenshot(text)
    assert time.monotonic() - start < 2


@pytest.mark.parametrize("text", [t for t in EVERY if holds_details(t, names=NAMES)])
def test_a_heading_row_that_gives_a_detail_is_refused(text):
    assert header_problem(["Date", "Description", "Amount", text], names=NAMES) is not None


@pytest.mark.parametrize("text", PLAIN)
def test_plain_descriptions_and_transactions_are_not_sensitive(text):
    assert classify(text, names=NAMES) == set()


# --- never weaker than 8ca14cc ---------------------------------------------------------------

_STREET_8CA14CC = (
    r"road|rd|street|st|lane|ln|avenue|ave|close|drive|way|gardens|court|place|terrace"
    r"|crescent|square|hill|grove|mews|walk"
)
SENSITIVE_8CA14CC = re.compile(
    r"\b(?:account|acct?|a/c)\.?\s*(?:no\.?|num(?:ber)?|name|holders?|type)\b"
    r"|\b(?:customer|membership|roll)\s*(?:no\.?|num(?:ber)?|id)\b"
    r"|(?<![\w/])(?:account|acct?|a/c|s/c|s\.c\.)\.?\s*:?\s*#?\s*\d[\d -]{4,}\d"
    r"|\bsort\s*code\b|\b\d{2}-\d{2}-\d{2}\b"
    r"|\bcard\s+(?:ending|number|no\.?)\b|\bending\s+(?:in\s+)?\d{4}\b"
    r"|\*{2,}[\s-]*\d{2,4}|(?<![a-z])x{2,}[\s-]*\d{4}\b|\b\d{4}[\s-]*\*{2,}"
    r"|\b(?:\d[ -]?){12,18}\d\b"
    r"|\biban\b|\bbic\b|\b[A-Z]{2}\d{2}\s?[A-Z0-9]{4}(?:\s?\d{4}){2,}(?:\s?[A-Z0-9]{1,4})?\b"
    r"|\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b"
    rf"|(?<![\d/.-])\d{{1,3}}[a-z]?,?\s+(?:[A-Za-z']+\s+){{1,2}}(?:{_STREET_8CA14CC})\b"
    r"|^\s*(?:mr|mrs|ms|miss|mx|dr|prof)\.?\s+[A-Za-z]"
    r"|^\s*(?:statement\s+for|prepared\s+for|holder)\b",
    re.IGNORECASE,
)
# Lines from the textprep, reader and identify tests and fixtures, plus the corpora above.
REGRESSION = [
    *EVERY,
    *PLAIN,
    "Account number 12345678   Sort code 12-34-56",
    "IBAN GB29 NWBK 6016 1331 9268 19",
    "A/C 12345678 S/C 12-34-56",
    "Customer number 99887766",
    "Your account 12345678 sort code 12-34-56 is eligible",
    "07/10/2026 FPO PAT EXAMPLE 20-00-00 87654321 10.00 1,844.42",
    "Holder: A Example",
    "Account Name: FlexAccount ****45678",
    "1 Example Road, Exampleton",
    "Flat 2, 10 Example Street",
    "Statement for 29 Sep 2026 to 28 Oct 2026",
]


@pytest.mark.parametrize("text", REGRESSION)
def test_never_weaker_than_the_8ca14cc_list(text):
    if SENSITIVE_8CA14CC.search(text) and text not in NARROWED:
        assert classify(text), text
        assert withheld_by_textprep(text)


def test_household_names_reach_the_extracted_text(tmp_path):
    path = tmp_path / "statement.txt"
    path.write_text(
        "Date Description Amount\n02 Oct 2026 Shop 4.00\nPat Example\n03 Oct 2026 Cafe 3.00\n"
    )
    doc = extract_document(path, "text", sha256="x", limits=ExtractLimits(), names=["Pat Example"])
    assert "Pat Example" not in [doc.by_ref()[r].text for r in doc.data_refs]
