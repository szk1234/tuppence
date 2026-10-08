"""R-M3-25 (1)-(4): every statement line that carries an amount ends in exactly one of three places
-- sent to the reader, held back and reported to the person, or withheld as a pure balance line
(nothing on it but balance or summary wording, figures and dates). Text prep ends with an
accounting pass that makes this true whichever of its rules withheld a line.

The guard is a property test over generated statements: header, address, account details, a
summary box, rows in every printed form (numeric and named dates, same-day rows without one,
grouped, ungrouped and signed amounts, a balance column or none), continuation lines (a payee, a
place, the holder's own name), merchants named after a street or a balance word, and split
multi-line rows. It reads amounts and pure balance lines with its own code, not text prep's.

The reviewer's re-review 3 probes are regression tests: N1 (p_r8loss, p_cont, p_n1pdf), N2
(p_ungrouped) and N3 (p_merch)."""

from __future__ import annotations

import random
import re

import pytest

from tuppence.ingest import textprep
from tuppence.ingest.models import Document
from tuppence.ingest.textprep import has_amount, pages_document, sent_lines, text_document

# --- the test's own reading of an amount and of a pure balance line ---------------------------

# Any figure with pence, grouped or not, signed or not, or a currency sign before digits.
_AMOUNT = re.compile(r"(?<![\w.])[-+−]?[£$€]?\d[\d,]*\.\d{2}(?!\d|\.\d)|[£$€]\s?\d")
_DATE = re.compile(
    r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.IGNORECASE,
)
_FIGURE = re.compile(r"[-+−(]?[£$€]?\d[\d,.]*\)?")
# The words a balance, a summary box or a card header's payment line is made of.
VOCABULARY = frozenset(
    [
        "balance", "bal", "balances", "brought", "carried", "forward", "b/f", "c/f", "fwd",
        "opening", "closing", "previous", "new", "start", "end", "statement", "current",
        "available", "cleared", "funds", "your", "you", "is", "the", "of", "on", "at", "as", "in",
        "out", "money",
        "paid", "total", "totals", "payments", "payment", "summary", "account", "credit", "debit",
        "limit", "minimum", "due", "amount", "outstanding", "owe", "owed", "overdrawn", "overdraft",
        "arranged", "interest", "refunds", "purchases", "fees", "charges", "cr", "dr", "period",
        "this", "date",
    ]
)  # fmt: skip


def carries_amount(text: str) -> bool:
    return _AMOUNT.search(text) is not None


def _words(text: str) -> list[str]:
    plain = _FIGURE.sub(" ", _DATE.sub(" ", text))
    return re.findall(r"[a-z]+(?:/[a-z]+)?", plain.casefold())


def pure_balance_line(text: str) -> bool:
    """Only balance or summary wording, figures and dates, with at least one word."""
    words = _words(text)
    return bool(words) and set(words) <= VOCABULARY


def _over_lines(texts: list[str]) -> set[int]:
    """Figures alone on their line in a run of balance labels ("Available balance", "Spent
    today") and figures, with a label for each figure: balances printed over two lines."""

    def label(text: str) -> bool:
        words = _words(text)
        return (
            not carries_amount(text)
            and bool(words)
            and set(words) <= {*VOCABULARY, "spent", "today"}
        )

    def figure(text: str) -> bool:
        return carries_amount(text) and not _words(text)

    found: set[int] = set()
    start = 0
    for end in range(len(texts) + 1):
        if end < len(texts) and (label(texts[end]) or figure(texts[end])):
            continue
        run = range(start, end)
        figures = [k for k in run if figure(texts[k])]
        if figures and 2 * len(figures) <= len(run):
            found.update(figures)
        start = end + 1
    return found


def violations(doc: Document) -> list[str]:
    """Lines with an amount that are neither sent, nor reported, nor a pure balance line (on
    one line, or over two); and any line both sent and reported."""
    data, held = set(doc.data_refs), set(doc.held_amount_refs)
    out = [f"both sent and held: {ref}" for ref in data & held]
    over_lines = _over_lines([line.text for line in doc.lines])
    for k, line in enumerate(doc.lines):
        if not carries_amount(line.text) or line.ref in data or line.ref in held:
            continue
        if not (pure_balance_line(line.text) or k in over_lines):
            out.append(line.text)
    return out


def _docs(lines: list[str], names: tuple[str, ...] = ()) -> dict[str, Document]:
    return {
        "text": text_document("\n".join(lines), sha256="x", names=names),
        "pdf": pages_document([lines], sha256="x", kind="pdf", names=names),
    }


def _fate(doc: Document, k: int) -> str:
    ref = doc.lines[k].ref
    if ref in doc.data_refs:
        return "sent"
    return "held" if ref in doc.held_amount_refs else "withheld"


# --- N1 (re-review 3): a continuation line after the first run never moves the table start ----

N1_HEAD = ["Example Bank plc", "Current account", "Statement period 01/10/2026 to 31/10/2026"]
N1_LAYOUTS = {
    "location line after the second dated row": (
        ["01/10/2026 TESCO STORES 25.00 975.00", "SAINSBURYS 10.00 965.00",
         "02/10/2026 BOOTS 4.00 961.00", "NOTTING HILL GATE", "03/10/2026 COSTA 3.00 958.00",
         "04/10/2026 PRET 5.00 953.00"], ()),
    "payee title continuation": (
        ["01/10/2026 TESCO STORES 25.00 975.00", "SAINSBURYS 10.00 965.00",
         "02/10/2026 FASTER PAYMENT TO 50.00 915.00", "MR J SMITH",
         "03/10/2026 COSTA 3.00 912.00", "04/10/2026 PRET 5.00 907.00"], ()),
    "the holder's own name after a transfer": (
        ["01/10/2026 TESCO STORES 25.00 975.00", "SAINSBURYS 10.00 965.00",
         "02/10/2026 TRANSFER TO SAVINGS 50.00 915.00", "ALEX EXAMPLE",
         "03/10/2026 COSTA 3.00 912.00", "04/10/2026 PRET 5.00 907.00"], ("Alex Example",)),
    "named-month dates, a town after a row": (
        ["01 Oct TESCO STORES 25.00 975.00", "SAINSBURYS 10.00 965.00",
         "02 Oct BOOTS HIGH ST 4.00 961.00", "ST ALBANS", "03 Oct COSTA 3.00 958.00",
         "04 Oct PRET 5.00 953.00"], ()),
}  # fmt: skip


@pytest.mark.parametrize("path", ["text", "pdf"])
@pytest.mark.parametrize("layout", list(N1_LAYOUTS))
def test_n1_a_continuation_line_never_moves_the_table_start(layout, path):
    """p_r8loss: the undated same-day row was neither sent nor reported, and the first row was
    held for review. Every row is sent now; the continuation line itself stays here."""
    rows, names = N1_LAYOUTS[layout]
    doc = _docs([*N1_HEAD, *rows], names)[path]
    for k, line in enumerate(rows, start=len(N1_HEAD)):
        if has_amount(line):
            assert _fate(doc, k) == "sent", line
    assert doc.held_amount_refs == []
    assert violations(doc) == []


NO_HEADING = ["Example Bank plc", "Current account statement", "Mr Alex Example",
              "1 Example Road", "Exampletown EX1 1AA",
              "Statement period 01/10/2026 to 31/10/2026"]  # fmt: skip


@pytest.mark.parametrize(
    "rows",
    [
        ["01/10/2026 TESCO STORES 25.00 975.00", "02/10/2026 SAINSBURYS 10.00 965.00",
         "NOTTING HILL GATE", "03/10/2026 COSTA COFFEE 3.00 962.00",
         "04/10/2026 BOOTS 4.00 958.00", "05/10/2026 PRET 5.00 953.00"],
        ["01/10/2026 TESCO STORES 25.00 975.00", "FINSBURY PARK",
         "02/10/2026 SAINSBURYS 10.00 965.00", "BETHNAL GREEN",
         "03/10/2026 COSTA COFFEE 3.00 962.00", "04/10/2026 BOOTS 4.00 958.00",
         "05/10/2026 PRET 5.00 953.00"],
    ],
    ids=["location after the second row", "location after each row"],
)  # fmt: skip
def test_n1_a_headingless_statement_with_places_sends_its_first_row(rows):
    """p_cont: the first row was held for review because a place followed the second row."""
    doc = text_document("\n".join([*NO_HEADING, *rows]), sha256="x")
    sent = [doc.by_ref()[r].text for r in doc.data_refs]
    assert [row for row in rows if has_amount(row)] == [s for s in sent if has_amount(s)]
    assert doc.held_amount_refs == []
    assert not any(secret in " ".join(sent) for secret in ("Alex", "Example Road", "EX1"))


HSBC_ROWS = [
    "01 Oct 26 BALANCE BROUGHT FORWARD 1,000.00",
    "02 Oct 26 VIS TESCO HIGH ST",
    "LONDON 25.00 975.00",
    "03 Oct 26 DD BRITISH GAS",
    "CUSTOMER REF 99 40.00 935.00",
    "04 Oct 26 VIS COSTA NOTTING HILL",
    "3.00 932.00",
    "05 Oct 26 CR SALARY ACME 1,000.00 1,932.00",
]


def test_n2_a_dated_description_line_is_never_an_address_line():
    """M4, the HSBC minor (ruling (2)): a row's dated description line with a street or place
    word in it is the row's, not an address, so the reader gets the date and the merchant."""
    lines = ["Date Payment type and details Paid out Paid in Balance", *HSBC_ROWS]
    for path, doc in _docs(lines).items():
        sent = [doc.by_ref()[r].text for r in doc.data_refs]
        assert "02 Oct 26 VIS TESCO HIGH ST" in sent, path
        assert "04 Oct 26 VIS COSTA NOTTING HILL" in sent, path
        assert violations(doc) == [], path


def test_n1_a_headingless_multi_line_statement_loses_nothing():
    """p_cont "HSBC-like no heading": the amount lines of split rows were withheld without a
    word. They are sent or reported now."""
    for path, doc in _docs([*NO_HEADING, *HSBC_ROWS]).items():
        assert violations(doc) == [], path
        sent = " ".join(sent_lines(doc)[r].text for r in doc.data_refs)
        assert "Example Road" not in sent and "Alex" not in sent, path


def test_n1_pdf_probe():
    """p_n1pdf: the same-day row after the first dated row is sent, in text and on a page."""
    lines = ["Example Bank plc", "Statement period 01/10/2026 to 31/10/2026",
             "01/10/2026 TESCO STORES 25.00 975.00", "SAINSBURYS 10.00 965.00",
             "02/10/2026 FASTER PAYMENT TO 50.00 915.00", "MR J SMITH",
             "03/10/2026 COSTA 3.00 912.00", "04/10/2026 PRET 5.00 907.00"]  # fmt: skip
    for path, doc in _docs(lines).items():
        assert [_fate(doc, k) for k in (2, 3, 4, 6, 7)] == ["sent"] * 5, path
        assert _fate(doc, 5) == "withheld", path  # a titled name: never sent


# --- N2 (re-review 3): an ungrouped amount is an amount; an amount line is no address ---------

N2_HEAD = ["Example Bank plc", "Statement period 01/10/2026 to 31/10/2026",
           "Date Description Paid out Paid in Balance",
           "01/10/2026 GREENBASKET STORES 42.18 957.82"]  # fmt: skip
UNGROUPED = [
    "02/10/2026 TRANSFER FROM NATIONWIDE BUILDING SOCIETY 1500.00 2457.82",
    "02/10/2026 RENT TO MR J SMITH 1250.00 1207.82",
    "02/10/2026 HOUSE OF FRASER 1099.00 108.82",
    "02/10/2026 NCP CAR PARK 1012.00 1119.82",
    "NATIONWIDE BUILDING SOCIETY 1500.00",
    "02/10/2026 SALARY ACME LTD 2500.00 3457.82",
    "02/10/2026 MORTGAGE NATIONWIDE BUILDING SOCIETY -1250.00",
    "02/10/2026 TRANSFER FROM NATIONWIDE BUILDING SOCIETY 1,500.00 2,457.82",
    "02/10/2026 PIZZA HOUSE 12.50 945.32",
    "02/10/2026 PARK LANE HOTEL 1012.00",
    "RENT 10 HIGH ST -1100.00",
]


@pytest.mark.parametrize("figure", ["1500.00", "-1250.00", "1012.00", "+2500.00", "−1250.00"])
def test_n2_an_ungrouped_or_signed_figure_with_pence_is_an_amount(figure):
    assert has_amount(f"NATIONWIDE BUILDING SOCIETY {figure}")


@pytest.mark.parametrize("path", ["text", "pdf"])
@pytest.mark.parametrize("line", UNGROUPED)
def test_n2_a_row_with_a_street_word_and_an_ungrouped_amount_is_sent(line, path):
    """p_ungrouped: these were neither sent nor reported (an address part, for want of an
    amount `has_amount` read). An amount line is never part of an address block now."""
    doc = _docs([*N2_HEAD, line, "31/10/2026 LITTLE CAFE 3.00 954.82"])[path]
    assert _fate(doc, len(N2_HEAD)) == "sent", line


@pytest.mark.parametrize("path", ["text", "pdf"])
def test_n2_an_account_detail_beside_an_ungrouped_amount_is_never_lost(path):
    """The pre-existing sibling: withheld without a word at 8bc2649 too."""
    line = "02/10/2026 FPO J SMITH 20-11-33 87654321 1500.00"
    doc = _docs([*N2_HEAD, line, "31/10/2026 LITTLE CAFE 3.00 954.82"])[path]
    ref = doc.lines[len(N2_HEAD)].ref
    assert ref in doc.data_refs or ref in doc.held_amount_refs
    if ref in doc.data_refs:
        sent = sent_lines(doc)[ref].text
        assert "87654321" not in sent and "20-11-33" not in sent and "1500.00" in sent


# --- N3 (re-review 3): a merchant named after a balance is a row, a bare balance is not --------

N3_ROWS = [
    "NEW BALANCE LONDON", "NEW BALANCE ATHLETICS", "OPENING BALANCE GYM", "PREVIOUS BALANCE ADJ",
    "BALANCE BROUGHT FORWARD LTD", "CARRIED FORWARD CAFE", "CARD PAYMENT - STATEMENT BALANCE",
    "NEWBALANCE.CO.UK", "PAYMENT TO AMEX - STATEMENT BALANCE",
    "PAYMENT TOWARDS STATEMENT BALANCE", "CREDIT CARD STATEMENT BALANCE",
]  # fmt: skip
# A bare vocabulary line is a pure balance line however it is printed (the ruling's accepted
# residual: "NEW BALANCE -89.99" reads exactly as a card's summary line).
N3_BARE = ["NEW BALANCE", "STATEMENT BALANCE", "OUTSTANDING BALANCE", "BALANCE", "BAL FWD",
           "AVAILABLE FUNDS", "CLEARED FUNDS", "OPENING", "CLOSING"]  # fmt: skip
N3_FORMS = {
    "dated": "02/10/2026 {d} 12.34",
    "dated, balance": "02/10/2026 {d} 12.34 1,012.34",
    "dated, signed": "02/10/2026 {d} -12.34",
    "named": "02 Oct {d} 12.34 1,012.34",
    "undated": "{d} 12.34",
    "undated, balance": "{d} 12.34 1,012.34",
    "undated, signed": "{d} -12.34",
}
N3_SHOT = {"signed": "{d} -£12.34", "unsigned": "{d} £12.34", "dated": "2 Oct {d} -£12.34"}


@pytest.mark.parametrize("path", ["text", "pdf"])
@pytest.mark.parametrize("form", list(N3_FORMS))
def test_n3_a_row_named_after_a_balance_is_sent_or_reported(form, path):
    """p_merch: New Balance's rows were dropped without a word in every form."""
    rows = [N3_FORMS[form].format(d=d) for d in N3_ROWS]
    doc = _docs([*N2_HEAD, *rows, "31/10/2026 LITTLE CAFE 3.00 954.82"])[path]
    lost = [d for k, d in enumerate(N3_ROWS) if _fate(doc, len(N2_HEAD) + k) == "withheld"]
    assert lost == []


@pytest.mark.parametrize("form", list(N3_SHOT))
def test_n3_in_a_screenshot(form):
    rows = [N3_SHOT[form].format(d=d) for d in N3_ROWS]
    doc = pages_document(
        [["Transactions", "Mon 2 Oct", "TESCO -£12.50", *rows, "CAFE -£3.00"]],
        sha256="x",
        kind="image",
    )
    lost = [d for k, d in enumerate(N3_ROWS) if _fate(doc, 3 + k) == "withheld"]
    assert lost == []


@pytest.mark.parametrize("form", ["dated", "undated", "undated, signed"])
def test_n3_a_dated_merchant_row_is_sent_not_held(form):
    """Ruling (4): sent when it is otherwise a row."""
    for description in ("NEW BALANCE LONDON", "OPENING BALANCE GYM",
                        "CARD PAYMENT - STATEMENT BALANCE"):  # fmt: skip
        line = N3_FORMS[form].format(d=description)
        doc = _docs([*N2_HEAD, line, "31/10/2026 LITTLE CAFE 3.00 954.82"])["text"]
        assert _fate(doc, len(N2_HEAD)) == "sent", line


@pytest.mark.parametrize("description", N3_BARE)
def test_n3_a_bare_balance_line_stays_a_pure_balance_line(description):
    for form in N3_FORMS.values():
        line = form.format(d=description)
        doc = _docs([*N2_HEAD, line, "31/10/2026 LITTLE CAFE 3.00 954.82"])["text"]
        assert _fate(doc, len(N2_HEAD)) == "withheld", line
        assert pure_balance_line(line)


@pytest.mark.parametrize(
    "line",
    ["Closing balance £1,234.56 as shown", "Opening balance £1,000.00 incl. pending items",
     "New balance £909.85 see note 4", "Balance brought forward 1,000.00 from last statement"],
)  # fmt: skip
def test_n3_balance_wording_with_other_words_not_shaped_as_a_row_is_reported_not_sent(line):
    """Ruling (4), "else reported": words after the figure make it no row's line."""
    for path, doc in _docs([*N2_HEAD, line, "31/10/2026 LITTLE CAFE 3.00 954.82"]).items():
        assert _fate(doc, len(N2_HEAD)) == "held", (path, line)


@pytest.mark.parametrize(
    "line",
    ["Closing balance £1,234.56 Page 2", "Opening balance on 01/10/2026 £5,555.55 (see overleaf)",
     "Balance carried forward 825.07 continued", "Previous balance £842.16 Page 1 of 2"],
)  # fmt: skip
def test_page_furniture_on_a_balance_line_keeps_it_a_pure_balance_line(line):
    for path, doc in _docs([*N2_HEAD, line, "31/10/2026 LITTLE CAFE 3.00 954.82"]).items():
        assert _fate(doc, len(N2_HEAD)) == "withheld", (path, line)


def _run(services, body: str, kind: str = "current"):
    from ingest.helpers import add_account, drain, use_local_model

    use_local_model(services)
    account = add_account(services, "other", kind, "Probe")
    out = services.ingest.upload("statement.txt", (body + "\n").encode())
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(out.record.id)
    return record


@pytest.mark.parametrize(
    ("middle", "description"),
    [("02/10/2026 MORTGAGE NATIONWIDE BUILDING SOCIETY -1250.00",
      "MORTGAGE NATIONWIDE BUILDING SOCIETY"),
     ("02/10/2026 NCP CAR PARK SEASON TICKET -1100.00", "NCP CAR PARK SEASON TICKET"),
     ("02/10/2026 NEW BALANCE LONDON -89.99", "NEW BALANCE LONDON")],
    ids=["bs_signed", "carpark_signed", "new_balance_signed"],
)  # fmt: skip
def test_the_reviewers_signed_statements_import_every_row(ingest_env, middle, description):
    """test_rr3_e2e2 bs_signed and new_balance_signed: imported with 2 of 3 rows and no message.
    Every row is imported now, or the line is reported."""
    services, _ = ingest_env
    body = "\n".join(["Example Bank plc", "Statement period 01/10/2026 to 31/10/2026",
                      "Date Description Amount", "01/10/2026 GREENBASKET STORES -42.18", middle,
                      "05/10/2026 LITTLE CAFE -3.50"])  # fmt: skip
    record = _run(services, body)
    if record.status == "imported":
        rows = [t.raw_description for t in services.statements.transactions(record.id)]
        assert description in rows and len(rows) == 3
    else:
        assert record.status == "needs_review"
        assert any("held back from the AI" in e for e in record.check_errors)


# --- the property: generated statements ------------------------------------------------------

BANKS = ["Example Bank plc", "Northwind Building Society", "Example Card Services"]
TITLES = ["Current account statement", "Card statement Page 1 of 2", "Statement of account"]
# The holders' lines, each with the printed forms of the names on it that rows may carry.
ALEX = ["Alex", "ALEX", "ALEXEXAMPLE", "A EXAMPLE", "EXAMPLE A"]
PAT = ["PAT EXAMPLE", "P EXAMPLE", "EXAMPLE P"]
HOLDERS = {"MR ALEX EXAMPLE": ALEX, "Alex Example": ALEX, "MRS P EXAMPLE": PAT,
           "MR ALEX EXAMPLE & MRS PAT EXAMPLE": [*ALEX, *PAT]}  # fmt: skip
HOUSEHOLDS = {(): [], ("Alex Example",): ALEX, ("Alex",): []}  # a first name alone masks nothing
ADDRESSES = [
    ["1 Example Road", "Exampletown", "EX1 2MP"],
    ["Flat 3", "Example House", "Exampletown"],
    ["Rose Cottage", "Mill Lane", "Exampletown"],
    ["The Old Rectory", "Upper Example", "Littlebury"],
    ["Apt 2B", "10/12 High St.", "Exampletown EX1 1AA"],
]
ACCOUNTS = ["Account number 12345678 Sort code 20-11-33", "Sort code 07-12-34 Account 12345678",
            "Card ending 4242", "IBAN GB29 NWBK 6016 1331 9268 19"]  # fmt: skip
PERIODS = ["Statement period 01/10/2026 to 31/10/2026", "Statement date 05/11/2026",
           "Statement for 29 Sep 2026 to 28 Oct 2026"]  # fmt: skip
SUMMARY_BOX = [
    "Opening balance £1,000.00", "Closing balance £2,252.32", "Previous balance £842.16",
    "Payments £190.00", "Refunds £6.15", "New purchases £259.04", "Interest £4.80",
    "New balance £909.85", "Minimum payment £25.00", "Credit limit £3,000.00",
    "Money in £2,000.00 Money out £438.78", "Available balance £2,252.32",
    "Total paid in 2,000.00", "Your balance is £1,234.56",
]  # fmt: skip
HEADINGS = ["Date Description Paid out Paid in Balance", "Date Description Amount",
            "Date Payment type and details Paid out Paid in Balance"]  # fmt: skip
DESCRIPTIONS = [
    "TESCO STORES", "SAINSBURYS", "COSTA COFFEE", "PRET A MANGER", "GREENBASKET STORES",
    "NATIONWIDE BUILDING SOCIETY", "MORTGAGE NATIONWIDE BUILDING SOCIETY", "NCP CAR PARK",
    "HOUSE OF FRASER", "PARK LANE HOTEL", "TRANSFER FROM NATIONWIDE BUILDING SOCIETY",
    "NEW BALANCE LONDON", "NEW BALANCE ATHLETICS", "OPENING BALANCE GYM",
    "CARD PAYMENT - STATEMENT BALANCE", "FASTER PAYMENT TO", "RENT TO MR J SMITH",
    "SALARY ACME LTD", "FPO J SMITH 20-11-33 87654321", "TOTAL FITNESS GYM",
    "BALANCE TRANSFER FEE", "INTEREST", "STATEMENT CREDIT", "DD CITY WATER", "VIS TESCO HIGH ST",
    "CR SALARY ACME", "CASH PAID IN AT BRANCH", "CREDIT", "PAYPAL *EBAY 12345", "7-ELEVEN",
    "3 MOBILE", "TFL TRAVEL NOTTING HILL", "ST ALBANS CATHEDRAL SHOP", "MILL LANE GARAGE",
    # a holder's own name in a row (re-review 4 I1)
    "FASTER PAYMENT FROM ALEX EXAMPLE", "TFR TO EXAMPLE A", "STANDING ORDER A EXAMPLE",
    "PAYPAL *ALEXEXAMPLE", "STANDING ORDER P EXAMPLE",
]  # fmt: skip
CONTINUATIONS = ["MR J SMITH", "NOTTING HILL GATE", "ST ALBANS", "Alex Example",
                 "REF RENT OCTOBER", "LONDON", "CARD PURCHASE", "Rose Court", "FINSBURY PARK",
                 "BETHNAL GREEN", "AMZN.CO.UK/PM"]  # fmt: skip
BALANCE_ROWS = ["BALANCE BROUGHT FORWARD {b}", "{date} BALANCE B/F {b}",
                "Balance carried forward {b}", "{date} BALANCE CARRIED FORWARD {b}"]  # fmt: skip
SECRETS = ["12345678", "87654321", "20-11-33", "07-12-34", "4242", "GB29", "EX1 2MP", "EX1 1AA",
           "Example Road", "Example House", "Rose Cottage", "Mill Lane", "Exampletown",
           "Littlebury", "Old Rectory", "Flat 3", "Apt 2B", "PAT EXAMPLE"]  # fmt: skip


def _shows(secret: str, text: str) -> bool:
    return re.search(rf"(?<![\w.,]){re.escape(secret)}(?!\w|[.,]\d)", text) is not None


def _amount(rnd: random.Random) -> tuple[str, int]:
    pence = rnd.choice([rnd.randint(1, 9_999), rnd.randint(100_000, 300_000)])
    pounds = f"{pence // 100}.{pence % 100:02d}"
    grouped = f"{pence // 100:,}.{pence % 100:02d}"
    form = rnd.choice(["{g}", "{u}", "-{u}", "-{g}", "£{g}", "-£{u}", "{g} CR", "+{u}"])
    return form.format(g=grouped, u=pounds), pence


def _date(rnd: random.Random, day: int) -> str:
    return rnd.choice([f"{day:02d}/10/2026", f"{day:02d} Oct", f"{day:02d} Oct 26",
                       f"{day:02d} Oct 2026", f"{day} Oct 2026"])  # fmt: skip


def _statement(rnd: random.Random) -> tuple[list[str], tuple[str, ...]]:
    lines, names, _ = _statement_and_names(rnd)
    return lines, names


def _statement_and_names(rnd: random.Random) -> tuple[list[str], tuple[str, ...], list[str]]:
    """A statement, the household's names, and the printed forms of every name it shows (the
    household's own, and the holders it prints, wherever it prints them)."""
    names: tuple[str, ...] = rnd.choice(list(HOUSEHOLDS))
    secrets = list(HOUSEHOLDS[names])
    header = [rnd.choice(BANKS)]
    if rnd.random() < 0.6:
        header.append(rnd.choice(TITLES))
    holder: list[str] = []
    if rnd.random() < 0.7:
        holder.append(rnd.choice(list(HOLDERS)))
        secrets += HOLDERS[holder[0]]
    if rnd.random() < 0.7 or holder:
        holder += rnd.choice(ADDRESSES)
    after_rows = rnd.random() < 0.3  # the holder and address after the first rows (I1)
    if not after_rows:
        header += holder
    if rnd.random() < 0.6:
        header.append(rnd.choice(ACCOUNTS))
    header.append(rnd.choice(PERIODS))
    header += rnd.sample(SUMMARY_BOX, rnd.randint(0, 5))
    if rnd.random() < 0.5:
        header.append(rnd.choice(HEADINGS))
    balance = 100_000
    with_balance = rnd.random() < 0.6
    rows: list[str] = []
    day = 1
    for _ in range(rnd.randint(4, 14)):
        day = min(28, day + rnd.choice([0, 0, 1, 2]))
        amount, pence = _amount(rnd)
        balance -= pence
        tail = f" {balance // 100:,}.{balance % 100:02d}" if with_balance else ""
        description = rnd.choice(DESCRIPTIONS)
        roll = rnd.random()
        if roll < 0.1:  # a same-day row printed without its date
            rows.append(f"{description} {amount}{tail}")
        elif roll < 0.2:  # a multi-line row: the date and details, then a place and the amount
            rows += [
                f"{_date(rnd, day)} {description}",
                f"{rnd.choice(CONTINUATIONS)} {amount}{tail}",
            ]
        else:
            rows.append(f"{_date(rnd, day)} {description} {amount}{tail}")
        if rnd.random() < 0.2:
            rows.append(rnd.choice(CONTINUATIONS))
        if rnd.random() < 0.08:
            rows.append(rnd.choice(BALANCE_ROWS).format(date=_date(rnd, day), b="1,000.00"))
        if rnd.random() < 0.04:
            rows += rnd.choice(ADDRESSES)
    if after_rows:
        rows[2:2] = holder
    footers = ["Closing balance £2,252.32", "Nationwide Building Society. Synthetic.",
               "SYNTHETIC TEST STATEMENT - NOT A REAL DOCUMENT"]  # fmt: skip
    footer = rnd.sample(footers, rnd.randint(0, 2))
    return [*header, *rows, *footer], names, secrets


SHOT_ROWS = ["Little Cafe", "Greenbasket Stores", "New Balance London", "Northline Rail",
             "Nationwide Building Society", "Park Lane Hotel", "Tesco",
             "Acme Payroll Ltd"]  # fmt: skip


def _screenshot(rnd: random.Random) -> list[str]:
    lines = [rnd.choice(["Transactions", "Recent activity", "Current Account"])]
    if rnd.random() < 0.5:
        lines += ["Available balance", "£1,184.56"]
    for _ in range(rnd.randint(3, 10)):
        amount, _ = _amount(rnd)
        figure = amount.replace("£", "").replace(" CR", "").lstrip("+-")
        signed = f"{rnd.choice(['-', '+'])}£{figure}"
        name = rnd.choice(SHOT_ROWS)
        roll = rnd.random()
        if roll < 0.3:
            day = f"{rnd.choice(['Mon', 'Tue', 'Wed'])} {rnd.randint(1, 28)} Oct"
            lines.append(f"{day} {name} {signed}")
        elif roll < 0.5:
            lines += ["Today", f"{name} {signed}"]
        elif roll < 0.7:
            lines += [name, signed]
        else:
            lines.append(f"{name} {signed}")
    return lines


def _check(seed: int, count: int) -> list[tuple[str, str]]:
    rnd = random.Random(seed)
    found: list[tuple[str, str]] = []
    for _ in range(count):
        lines, names, named = _statement_and_names(rnd)
        cut = rnd.randint(len(lines) // 2, len(lines))
        docs = {
            "text": text_document("\n".join(lines), sha256="x", names=names),
            "pdf": pages_document([lines[:cut], lines[cut:]], sha256="x", kind="pdf", names=names),
            "image": pages_document([_screenshot(rnd)], sha256="x", kind="image", names=names),
        }
        for path, doc in docs.items():
            found += [(path, v) for v in violations(doc)]
            if path != "image":
                sent = " ".join(sent_lines(doc)[r].text for r in doc.data_refs)
                secrets = [*SECRETS, *named]  # a printed name, with no names set too (I1)
                found += [(path, f"sent: {s}") for s in secrets if _shows(s, sent)]
    return found


@pytest.mark.parametrize("seed", range(6))
def test_every_amount_line_is_sent_reported_or_a_pure_balance_line(seed):
    """The partition, over 60 generated statements per seed in text, PDF and screenshot form."""
    found = _check(seed, 60)
    if found:
        pytest.fail(f"{len(found)} lines break the partition, e.g. {found[:6]}")


# The rules that withhold a line, each made to withhold far too much: the accounting pass still
# reports every amount line that isn't a pure balance line, whichever rule withheld it.
def _every_line(texts, holders=None, *, single=True):  # noqa: ARG001 - the rule's own signature
    return set(range(len(texts)))


ROGUE_RULES = {
    "every line an address": ("_address_blocks", _every_line),
    "every line a balance": ("_summary_kind", lambda text: "balance"),
    "the whole page a balance block": ("_balance_blocks", lambda texts: [range(len(texts))]),
    "the table starting at the last line": (
        "_table_start", lambda rows, headings, lo, hi, **_: hi - 1 if hi > lo else None),
}  # fmt: skip


@pytest.mark.parametrize("rule", list(ROGUE_RULES))
def test_the_accounting_pass_reports_whatever_rule_withheld_a_line(rule, monkeypatch):
    name, rogue = ROGUE_RULES[rule]
    monkeypatch.setattr(textprep, name, rogue)
    found = [v for v in _check(100, 25) if not v[1].startswith("sent:")]
    if found:
        pytest.fail(f"{len(found)} lines lost with {rule}, e.g. {found[:6]}")


@pytest.mark.parametrize("rule", list(ROGUE_RULES))
def test_the_guard_can_fail(rule, monkeypatch):
    """The control: with the accounting pass off, each rogue rule loses lines and the property
    test sees it."""
    name, rogue = ROGUE_RULES[rule]
    monkeypatch.setattr(textprep, name, rogue)
    monkeypatch.setattr(textprep, "_accounted", lambda split, *args, **kwargs: split)
    assert [v for v in _check(100, 25) if not v[1].startswith("sent:")]


def test_the_name_check_runs_with_no_household_names(monkeypatch):
    """Re-review 4 I1, the control: with the holders' names read only above the table start
    again (a11567f), statements that print the holder after their first rows send the name,
    and the property test sees it although no household names are set."""
    monkeypatch.setattr(textprep, "_printed_holder_lines", lambda texts, addresses: set())
    sent = [v for v in _check(0, 60) if v[1].startswith("sent:")]
    assert any(name in v[1] for v in sent for name in ("ALEX", "A EXAMPLE", "EXAMPLE A"))
