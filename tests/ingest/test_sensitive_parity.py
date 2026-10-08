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


def sent_by_textprep(text):
    """The text sent for `text` between two rows, or None when it is withheld."""
    doc = pages_document(
        [["Date Description Amount", "02 Oct 2026 Shop 4.00", text, "03 Oct 2026 Cafe 3.00"]],
        sha256="x",
        kind="pdf",
        names=NAMES,
    )
    if "P1L3" not in doc.data_refs:
        return None
    return doc.masked["P1L3"].text if "P1L3" in doc.masked else text


@pytest.mark.parametrize("text", REGRESSION)
def test_never_weaker_than_the_8ca14cc_list(text):
    """Every line the 8ca14cc list caught is withheld, or (a transaction line, since I2) sent
    with every account detail masked."""
    if SENSITIVE_8CA14CC.search(text) and text not in NARROWED:
        assert classify(text), text
        sent = sent_by_textprep(text)
        if sent is not None:
            from tuppence.ingest.sensitive import LABEL_CLASSES

            assert sent != text and not (classify(sent, names=NAMES) - LABEL_CLASSES), sent


def test_household_names_reach_the_extracted_text(tmp_path):
    path = tmp_path / "statement.txt"
    path.write_text(
        "Date Description Amount\n02 Oct 2026 Shop 4.00\nPat Example\n03 Oct 2026 Cafe 3.00\n"
    )
    doc = extract_document(path, "text", sha256="x", limits=ExtractLimits(), names=["Pat Example"])
    assert "Pat Example" not in [doc.by_ref()[r].text for r in doc.data_refs]


# --- I2: account details inside a transaction line are masked in place, never dropped --------

# Transaction lines (or a row's amount line) that carry account details. Each is sent with its
# details replaced by placeholders; its date and amount are left as printed.
WITH_DETAILS = [
    "03/10/2026 Transfer to A/C 87654321 -250.00",
    "09/10/2026 Payment to 20-11-33 41234567 J SMITH -75.00",
    "FPO J SMITH 20-11-33 41234567 -250.00",
    "07/10/2026 FPO PAT EXAMPLE 20-00-00 87654321 10.00 1,844.42",
    "TFR TO SAVINGS ...5678 50.00",
    "CHQ ...1234 100.00",
    "CUSTOMER REF 99887766 20.00",
    "02/10/2026 Card ending 4242 Greenbasket 12.00",
    "02 Oct 2026 Transfer GB29 NWBK 6016 1331 9268 19 100.00",
    "02 Oct 2026 Rent 10 Example Road 500.00",
    "02 Oct 2026 Sort code 12-34-56 acc 12345678 25.00",
    "02 Oct 2026 Refund to card ****4242 12.00",
    "Mr A Example 12.00",
    "02 Oct 2026 Transfer 12 34 56 87654321 -£40.00",
    "02 Oct 2026 Rent Flat 3 Example House 650.00",
    "Payee: Exampletown EX1 2MP 45.00",
    "TRANSFER FROM ALEX EXAMPLE 50.00",  # a household name, anywhere in the line
    "02 Oct 2026 FPI Example, Alex 25.00",
    "02 Oct 2026 Payee name: J Smith 25.00",
]
_KEPT = re.compile(r"\d{1,3}(?:,\d{3})*\.\d{2}|\d{2}/\d{2}/\d{4}|\d{2} [A-Z][a-z]{2} \d{4}")


def _restored(masked):
    text = masked.text
    for placeholder, original in masked.hidden.items():
        text = text.replace(placeholder, original)
    return text


@pytest.mark.parametrize("text", WITH_DETAILS)
def test_details_inside_a_transaction_line_are_masked_in_place(text):
    from tuppence.ingest.sensitive import LABEL_CLASSES, mask_line

    masked = mask_line(text, names=NAMES)
    assert masked is not None and masked.hidden
    assert not (classify(masked.text, names=NAMES) - LABEL_CLASSES), masked.text
    for kept in _KEPT.findall(text):  # dates and amounts are sent as printed
        assert kept in masked.text
    for value in masked.hidden.values():
        assert value not in masked.text
    assert _restored(masked) == text  # the description is stored as printed


@pytest.mark.parametrize("text", WITH_DETAILS)
def test_a_transaction_line_with_details_is_sent_masked_not_withheld(text):
    lines = [
        Line(ref=f"P1L{i}", text=t)
        for i, t in enumerate(
            ["Date Description Amount", "02 Oct 2026 Shop 4.00", text, "03 Oct 2026 Cafe 3.00"],
            start=1,
        )
    ]
    doc = pages_document([[line.text for line in lines]], sha256="x", kind="pdf", names=NAMES)
    assert "P1L3" in doc.data_refs and "P1L3" in doc.masked
    assert doc.held_amount_refs == []
    sent = doc.masked["P1L3"].text
    for value in doc.masked["P1L3"].hidden.values():
        assert value not in sent


@pytest.mark.parametrize("text", PLAIN)
def test_a_line_without_details_is_sent_as_printed(text):
    from tuppence.ingest.sensitive import mask_line, normalise

    masked = mask_line(text, names=NAMES)
    assert masked is not None and masked.text == normalise(text) and masked.hidden == {}


# --- one classifier, one masker: no differential (scan follow-up) ------------------------------


def _normal_text(text):
    import unicodedata

    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _digit_runs(text):
    """Runs of three or more digits, once amounts are taken out (they are never masked) and
    separators inside a number are taken out."""
    plain = re.sub(r"[£$]?\d{1,3}(?:,\d{3})*\.\d{2}(?![\d.])", " ", _normal_text(text))
    joined = re.sub(r"(?<=\d)[\s\-./]+(?=\d)", "", plain)
    return set(re.findall(r"\d{3,}", joined))


def _never_sent(text, sent):
    """Nothing identifying in `text` is in `sent`: no value class, no label with a value left
    after it, no digits of the identifier (beyond the row's own date and amount) and no form of
    the household's name."""
    from tuppence.ingest.sensitive import LABEL_CLASSES, unmasked_label

    assert not (classify(sent, names=NAMES) - LABEL_CLASSES), sent
    assert not unmasked_label(sent), sent
    allowed = {"2026", "1200"}  # the row's date and amount
    shown = _digit_runs(sent)
    for run in _digit_runs(text) - allowed:
        assert not any(run in s for s in shown), (run, sent)
    lowered = _normal_text(sent).casefold()
    for form in ("alex example", "a example", "example alex", "example a"):
        assert form not in re.sub(r"[.,]", " ", lowered).replace("  ", " "), sent


# Every spelling the classifier holds sensitive, but a balance line (a line that is only
# balances; inside a row its figures are amounts).
IDENTIFIERS = [t for t in EVERY if classify(t, names=NAMES) - {"balance_line"}]


@pytest.mark.parametrize("text", IDENTIFIERS)
def test_an_identifier_anywhere_in_a_row_is_recognised_and_never_sent(text):
    from tuppence.ingest.sensitive import mask_line

    line = f"02 Oct 2026 Payment {text} 12.00"
    assert classify(line, names=NAMES), line  # wherever it sits in the line
    masked = mask_line(line, names=NAMES)
    sent = sent_by_textprep(line)
    if sent is None:  # withheld: then it is reported, as a row it may be
        return
    assert masked is not None and sent == masked.text
    _never_sent(text, sent)


@pytest.mark.parametrize("text", PLAIN)
def test_a_plain_phrase_anywhere_in_a_row_is_left_alone(text):
    from tuppence.ingest.sensitive import mask_line, normalise

    line = f"02 Oct 2026 Payment {text} 12.00"
    masked = mask_line(line, names=NAMES)
    assert masked is not None and masked.text == normalise(line) and masked.hidden == {}


# The same identifiers printed with what a PDF or OCR may put in them: full-width or other
# Unicode digits, zero-width characters, non-breaking and doubled spaces.
ODD = [
    "Transfer to A/C \uff18\uff17\uff16\uff15\uff14\uff13\uff12\uff11",  # full-width
    "Transfer to A/C 8765\u200b4321",  # zero-width space
    "Transfer to A/C 8765\u200d4321",  # zero-width joiner
    "Transfer to A/C 8765\u00ad4321",  # soft hyphen
    "Payment to 20\u200b-11-33",
    "Payment to 20-11-33\u00a041234567",  # non-breaking space
    "Payment to 12\u00a034\u00a056 41234567",
    "Payment to 12  34  56 41234567",  # doubled spaces
    "Payment to 12\u202f34\u202f56",  # narrow no-break space
    "GB29  NWBK  6016  1331  9268  19",
    "Card \uff14\uff19\uff12\uff19 \u2022\u2022\u2022\u2022 "
    "\u2022\u2022\u2022\u2022 \uff14\uff12\uff14\uff12",
    "Card ending \U0001d7d2\U0001d7d0\U0001d7d2\U0001d7d0",  # mathematical bold digits
    "Card ending \u0664\u0662\u0664\u0662",  # Arabic-Indic digits
    "Sort code \uff11\uff12-\uff13\uff14-\uff15\uff16",
    "A\u200b/C 87654321",
    "Alex\u00a0Example",
    "ALEX\u200bEXAMPLE",
    "Example,\u00a0Alex",
    "Flat\u00a03",
    "EX1\u00a02MP",
]


@pytest.mark.parametrize("text", ODD)
def test_identifiers_in_unicode_or_spacing_variants_are_masked(text):
    from tuppence.ingest.sensitive import mask_line

    line = f"02 Oct 2026 Payment {text} 12.00"
    assert classify(line, names=NAMES), line
    sent = sent_by_textprep(line)
    if sent is not None:
        assert mask_line(line, names=NAMES) is not None
        _never_sent(text, sent)


@pytest.mark.parametrize("text", ODD)
def test_unicode_variants_alone_are_withheld_by_every_filter(text):
    assert classify(text, names=NAMES), text
    assert withheld_by_textprep(text)
    assert withheld_from_screenshot(text)
    assert cell_token(text, names=NAMES) == HIDDEN
    assert shown_heading(text, names=NAMES) == HIDDEN


def test_a_household_name_with_a_curly_apostrophe_is_found_either_way():
    from tuppence.ingest.sensitive import mask_line

    names = ["Sam O\u2019Brien"]
    for line in (
        "02 Oct 2026 Payment SAM O'BRIEN 12.00",
        "02 Oct 2026 Payment S O\u2019Brien 12.00",
    ):
        assert classify(line, names=names), line
        masked = mask_line(line, names=names)
        assert masked is not None and "brien" not in masked.text.casefold(), line


# --- names, every embedding, every variant, one normalisation (scan follow-up) -----------------

SMITH = ["John Smith"]  # a household member with a common name


@pytest.mark.parametrize(
    "line",
    [
        "02 Oct 2026 FPO J SMITH 25.00",
        "02 Oct 2026 FPO SMITH J 25.00",
        "02 Oct 2026 FPO J. Smith 25.00",
        "02 Oct 2026 FPO MR J SMITH 25.00",
        "02 Oct 2026 FPO Smith, John 25.00",
        "02 Oct 2026 FPO JOHN    SMITH 25.00",
        "02 Oct 2026 FPO JOHN SMITH 25.00",
        "02 Oct 2026 FPO JOHN​ SMITH 25.00",
        "02 Oct 2026 FPO john smith 25.00",
        "02 Oct 2026 FPO JSMITH 25.00",
        "02 Oct 2026 FPO SMITHJ 25.00",
        "02 Oct 2026 FPO JOHNSMITH 25.00",
        "02 Oct 2026 FPO J SMITH-JONES 25.00",
        "02 Oct 2026 FPO J SMITH‐JONES 25.00",
        "02 Oct 2026 FPO J SMITH 25.00",
        "02 Oct 2026 FPO Ｊ ＳＭＩＴＨ 25.00",
        "02 Oct 2026 FPO J SMÏTH 25.00",
    ],
)
def test_household_names_are_masked_in_every_spelling(line):
    from tuppence.ingest.sensitive import mask_line

    assert "holder_name" in classify(line, names=SMITH), line
    masked = mask_line(line, names=SMITH)
    assert masked is not None, line
    assert "smith" not in masked.text.casefold().replace("ï", "i"), masked.text
    assert "25.00" in masked.text and "02 Oct 2026" in masked.text


def test_a_name_is_masked_on_its_own_without_any_other_detail():
    from tuppence.ingest.sensitive import mask_line

    masked = mask_line("02 Oct 2026 Gift from John Smith 25.00", names=SMITH)
    assert masked is not None and masked.text == "02 Oct 2026 Gift from [hidden-a] 25.00"


def test_names_from_the_statement_header_are_masked_in_rows():
    """The holder printed above the address is masked in the rows too, even when the person
    isn't in the household's list."""
    page = [
        "Example Bank plc",
        "MR ALEX EXAMPLE & MRS SAM SAMPLE",
        "1 Example Road",
        "Exampletown",
        "EX1 2MP",
        "Date Description Amount",
        "02 Oct 2026 Transfer to SAM SAMPLE 25.00",
        "03 Oct 2026 FPO S SAMPLE 5.00",
    ]
    doc = pages_document([page], sha256="x", kind="pdf")
    sent = [doc.masked[r].text if r in doc.masked else doc.by_ref()[r].text for r in doc.data_refs]
    assert not any("sample" in s.casefold() for s in sent), sent
    assert len(sent) == 3


# Every way a detail may be printed: as typed, full-width, with invisible characters inside,
# with non-breaking or doubled spaces, with accents on its letters.
def _fullwidth(text):
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


def _zero_width(text):
    return "​".join(text)


def _accented(text):
    return text.translate(str.maketrans("aeiouAEIOU", "àéîõüÀÉÎÕÜ"))


VARIANTS = {
    "plain": lambda t: t,
    "full-width": _fullwidth,
    "zero-width": _zero_width,
    "nbsp": lambda t: t.replace(" ", " "),
    "doubled spaces": lambda t: t.replace(" ", "  "),
    "accents": _accented,
}
POSITIONS = {
    "start": "{} 02/10/2026 12.00",
    "middle": "02 Oct 2026 Payment {} 12.00",
    "end": "02 Oct 2026 Payment 12.00 {}",
}


@pytest.mark.parametrize("variant", list(VARIANTS))
@pytest.mark.parametrize("text", IDENTIFIERS)
def test_every_class_in_every_position_and_variant_is_masked_or_withheld(text, variant):
    from tuppence.ingest.sensitive import mask_line

    shown = VARIANTS[variant](text)
    for position, template in POSITIONS.items():
        line = template.format(shown)
        assert classify(line, names=NAMES), (position, line)
        masked = mask_line(line, names=NAMES)
        if masked is None:  # withheld whole: textprep reports it if it holds an amount
            continue
        _never_sent(text, masked.text)


def test_classify_mask_and_the_send_path_share_one_normalisation(monkeypatch):
    """The line that is classified, masked, kept and sent is one string: `sensitive.normalise`
    of the printed line. Swapping that one function changes all of them."""
    from tuppence.ingest import sensitive
    from tuppence.ingest.textprep import plan_chunks, render, text_document

    raw = "02/10/2026  Transfer to A/C ８７６５​4321  -250.00"
    normal = sensitive.normalise(raw)
    assert normal == "02/10/2026 Transfer to A/C 87654321 -250.00"
    masked = sensitive.mask_line(raw)
    assert masked is not None
    assert _restored(masked) == normal
    doc = text_document(f"Date Description Amount\n{raw}\n", sha256="x")
    assert doc.lines[1].text == normal
    assert doc.masked["L2"] == masked
    assert masked.text in render(plan_chunks(doc, rows_per_chunk=5)[0].lines)

    calls = []
    real = sensitive.normalise

    def spy(text):
        calls.append(text)
        return real(text)

    monkeypatch.setattr(sensitive, "normalise", spy)
    sensitive.classify(raw)
    assert calls, "classify"
    calls.clear()
    sensitive.mask_line(raw)
    assert calls, "mask_line"
    calls.clear()
    text_document(f"{raw}\n", sha256="x")
    assert calls, "text prep"


# --- fuzz: decorated identifiers never leave in prepare_outbound's output ---------------------

_COMBINING = [chr(c) for c in range(0x0300, 0x0370)]
_DASHES = ["-", "–", "—", "−", " "]


def _decorate(text, rnd):
    """`text` printed the way a hostile or merely odd PDF might: full-width digits and letters,
    non-breaking, narrow and doubled spaces, zero-width characters, combining marks, mixed case
    and mixed separators between digit groups."""
    out = []
    for i, ch in enumerate(text):
        before, after = text[:i], text[i + 1 :]
        between_digit_groups = (
            len(before) >= 2 and before[-2:].isdigit() and len(after) >= 2 and after[:2].isdigit()
        )
        if ch in "-" and between_digit_groups or ch == " " and between_digit_groups:
            ch = rnd.choice(_DASHES if ch == "-" else [" ", "-"])
        if ch.isdigit() and rnd.random() < 0.3:
            ch = chr(ord(ch) + 0xFEE0)
        elif ch.isalpha():
            ch = ch.upper() if rnd.random() < 0.5 else ch.lower()
            if rnd.random() < 0.1:
                ch = chr(ord(ch) + 0xFEE0) if ch.isascii() else ch
            if rnd.random() < 0.15:
                ch += rnd.choice(_COMBINING)
        elif ch == " ":
            ch = rnd.choice([" ", " ", "  ", " ", " ​"])
        if rnd.random() < 0.15:
            ch += rnd.choice(["​", "‌", "‍", "⁠", "﻿", "­"])
        out.append(ch)
    return "".join(out)


@pytest.mark.parametrize("seed", range(8))
def test_fuzzed_identifiers_never_come_out_of_prepare_outbound(seed):
    import random

    from tuppence.ingest.sensitive import normalise, prepare_outbound

    rnd = random.Random(seed)
    for text in IDENTIFIERS:
        for _ in range(4):
            shown = _decorate(text, rnd)
            template = rnd.choice(list(POSITIONS.values()))
            line = template.format(shown)
            out = prepare_outbound(line, names=NAMES)
            if out is None:  # withheld whole (and reported, as it holds an amount)
                continue
            _never_sent(text, normalise(out.text))
            _never_sent(text, out.text)
