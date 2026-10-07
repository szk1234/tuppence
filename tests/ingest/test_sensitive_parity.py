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
    "5 Oct Balance £20.00",
    # column vocabulary is not a titled name (R-M3-16)
    "Dr Amount",
    "Cr Amount",
    "DR/CR",
    "Dr",
    "Ref",
    "Debit",
    "Credit",
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
