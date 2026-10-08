"""R-M3-23 (b), re-review N4: the table starts at the column-heading row whenever the page has one,
and address blocks are found wherever they are, a screenshot included. Two row-shaped lines in
the header (a summary box merged onto the address rows) no longer start the table, so the
address and summary lines below them stay on this device."""

from __future__ import annotations

import json

import pytest

from ingest.helpers import add_account, drain, use_local_model
from tuppence.ingest.textprep import pages_document, sent_lines, text_document

ROWS = ["01 Oct 2026 Greenbasket Stores -42.18 2,210.14", "03 Oct 2026 Little Cafe -3.50 2,206.64"]
HEADING = "Date Description Amount Balance"
SECRETS = ["ALEX", "Rose Cottage", "Mill Lane", "Flat 3", "Example House", "Exampletown", "EX1",
           "2MP", "25.00", "3.21", "1,000.00", "250.00"]  # fmt: skip


def _sent(page: list[str], kind: str = "pdf") -> list[str]:
    doc = pages_document([page], sha256="x", kind=kind, names=["Alex Example"])
    sent = sent_lines(doc)
    return [sent[r].text for r in doc.data_refs]


# The reviewer's p_header2 pages: two row-shaped lines above the heading row.
HEADED = {
    "two dated summary lines": [
        "Example Bank plc", "Statement date 05/11/2026",
        "Previous statement balance on 05/10/2026 £1,000.00 Payments received £1,000.00",
        "New purchases 01/10/2026 to 31/10/2026 £250.00", "Example House", "Exampletown",
        HEADING, *ROWS,
    ],
    "payment lines": [
        "Example Bank plc", "Please pay £25.00 by 20/11/2026",
        "We will collect £25.00 on 21/11/2026", "Rose Cottage", "Mill Lane", "Exampletown",
        HEADING, *ROWS,
    ],
    "merged address": [
        "Example Bank plc", "Please pay £25.00 by 20/11/2026",
        "Rose Cottage We will collect £25.00 on 21/11/2026", "Exampletown", HEADING, *ROWS,
    ],
    "interest lines": [
        "Example Bank plc", "Interest charged 05/11/2026 £3.21",
        "Interest on purchases 05/11/2026 £2.10", "Rose Cottage", "Exampletown", HEADING, *ROWS,
    ],
}  # fmt: skip
# p_header's merged pages without a heading row: the rows start with their date, the header
# lines don't, so the rows start the table.
UNHEADED = {
    "merged box": [
        "Example Bank plc", "Example House Please pay £25.00 by 20/11/2026",
        "Exampletown We will collect £25.00 on 20/11/2026", "EX1 2MP", *ROWS,
    ],
    "box above the address": [
        "Example Bank plc", "Please pay £25.00 by 20/11/2026",
        "We will collect £25.00 on 21/11/2026", "MR ALEX EXAMPLE", "Example House",
        "Exampletown", *ROWS,
    ],
    "box above a two-line address": [
        "Example Bank plc", "Please pay £25.00 by 20/11/2026",
        "We will collect £25.00 on 21/11/2026", "Example House", "Exampletown", *ROWS,
    ],
    "box above a one-line address": [
        "Example Bank plc", "Please pay £25.00 by 20/11/2026",
        "We will collect £25.00 on 21/11/2026", "Example House Exampletown", *ROWS,
    ],
}  # fmt: skip


@pytest.mark.parametrize("name", list(HEADED))
def test_with_a_heading_row_only_the_table_is_sent(name):
    assert _sent(HEADED[name]) == [HEADING, *ROWS]


@pytest.mark.parametrize("name", list(UNHEADED))
def test_without_one_the_first_run_of_rows_starting_with_a_date_starts_the_table(name):
    assert _sent(UNHEADED[name]) == ROWS


def test_a_run_above_the_heading_on_the_same_page_is_header():
    text = "\n".join(["Example Bank", "02/10/2026 Interest 3.21", "03/10/2026 Fee 5.00",
                      "Flat 3", "Mill Lane", HEADING, *ROWS])  # fmt: skip
    doc = text_document(text, sha256="x")
    assert [doc.by_ref()[r].text for r in doc.data_refs] == [HEADING, *ROWS]
    assert len(doc.held_amount_refs) == 2  # dated amounts above the table: reported


def test_rows_on_a_page_before_the_first_heading_still_start_the_table():
    """The one case a run comes first: the headings are first printed on a later page."""
    doc = pages_document(
        [["Example Bank plc", "01 Oct 2026 Shop 4.00", "02 Oct 2026 Cafe 3.00"],
         ["Date Description Amount", "03 Oct 2026 Bus 2.00"]],
        sha256="x",
        kind="pdf",
    )  # fmt: skip
    assert "P1L2" in doc.data_refs and "P1L3" in doc.data_refs


def test_an_address_block_in_a_screenshot_is_withheld():
    shot = ["Mon 5 Oct Little Cafe -£3.40", "Flat 3", "Example House", "Exampletown",
            "Tue 6 Oct Northline Rail -£12.80"]  # fmt: skip
    sent = _sent(shot, kind="image")
    assert sent == ["Mon 5 Oct Little Cafe -£3.40", "Tue 6 Oct Northline Rail -£12.80"]


def test_the_reviewers_card_pdf_sends_only_the_heading_and_rows(ingest_env):
    """test_rr_header_run: the right-hand box merges onto the address rows, giving two dated
    lines with an amount just above the column headings. They and the address stay here."""
    from ingest.pdfgen import L, pdf

    data = pdf([[
        L((56, "Example Card Services", False)),
        L((56, "MR ALEX EXAMPLE", False), (330, "Statement date 05/11/2026", False)),
        L((56, "Rose Cottage", False), (330, "We will collect £25.00 on 20/11/2026", False)),
        L((56, "Mill Lane", False), (330, "Interest charged 05/11/2026 £3.21", False)),
        L((56, "Exampletown EX1 2MP", False)),
        L((56, "Date", False), (140, "Description", False), (470, "Amount", True)),
        L((56, "02 Oct 2026", False), (140, "Northwind Books", False), (470, "18.40", True)),
        L((56, "04 Oct 2026", False), (140, "City Cinema", False), (470, "24.00", True)),
    ]])  # fmt: skip
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "credit_card", "Card")
    out = services.ingest.upload("card.pdf", data)
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(out.record.id)
    sent = json.dumps(scripted.requests)
    assert [s for s in SECRETS if s in sent] == []
    assert "Northwind Books" in sent and "City Cinema" in sent
    assert record.status in ("imported", "needs_review")


# The reviewer's p_header probe: header lines a UK statement prints above the address, with
# and without a column-heading row. Only the heading (if any) and the rows are ever sent.
WORDINGS = [
    "Balance as at 31 October 2026 £2,252.32", "Your balance on 31 Oct: £2,252.32",
    "Balance at 31.10.26 £2,252.32", "Statement balance 31/10/2026 £2,252.32",
    "Statement Balance on 31/10/2026 is £2,252.32", "Minimum payment due by 20/11/2026: £25.00",
    "Please pay £25.00 by 20/11/2026", "Direct Debit of £25.00 will be collected on 20/11/2026",
    "Total amount due 20/11/2026 £250.00", "New balance as at 05 Nov 2026 £250.00",
    "Overdraft limit £500.00 Statement date 05/11/2026",
    "Interest rate 1.5% Statement date 05/11/2026 Balance £100.00",
    "05/11/2026 Statement balance £250.00 Credit limit £3,000.00",
    "Pay by 20/11/2026 to avoid interest on £250.00",
    "Your account summary 01/10/2026 - 31/10/2026 Opening £1,000.00",
    "We will collect £25.00 on 20 November 2026", "As of 31/10/2026 you owe £250.00",
    "Credit available £2,750.00 at 05/11/2026",
    "Estimated interest next statement 05/12/2026 £3.21", "Amount to pay 20/11/2026 £25.00",
]  # fmt: skip
ADDRESS = ["MR ALEX EXAMPLE", "Flat 3", "Example House", "Exampletown", "EX1 2MP"]


@pytest.mark.parametrize("heading", [True, False], ids=["heading", "no heading"])
@pytest.mark.parametrize("wording", WORDINGS)
def test_header_wordings_never_reach_the_reader(wording, heading):
    page = ["Example Bank plc", wording, *ADDRESS, "Sort code 07-12-34 Account 12345678",
            *([HEADING] if heading else []), *ROWS]  # fmt: skip
    assert _sent(page) == [*([HEADING] if heading else []), *ROWS]


# Re-review 2 R8 (p_header3): without a heading row, two date-first summary lines above the
# holder's name and an address without a postcode or street word started the table, so the
# address was sent. The address is withheld wherever it is printed now, and summary lines that
# may be rows (held) never count toward a run. Lines with no amount after the first run (the
# holder's name, an address, a payee or a place) never move the table start (R-M3-25 (3),
# re-review 3 N1): two date-first lines with an amount are the first run, so they are the
# table's, sent or reported like any row.
NO_HEADING = {
    "summary lines above a name and an address": [
        "Example Card plc", "05/11/2026 Interest charged £3.21",
        "05/11/2026 Payment received £250.00", "MR ALEX EXAMPLE", "Rose Cottage", "Exampletown",
        *ROWS,
    ],
    "summary lines above an address block and odd headings": [
        "Example Card plc", "05/11/2026 Interest charged £3.21", "06/11/2026 Fees £12.00",
        "Rose Cottage", "Mill Lane", "Exampletown",
        "Trans | Particulars | Withdrawn | Lodged | Running", *ROWS,
    ],
    "held lines above an address": [
        "Example Bank plc", "02/10/2026 CASH PAID IN AT BRANCH 50.00",
        "02/10/2026 CREDIT 900.00", "Rose Cottage", "Exampletown", *ROWS,
    ],
}  # fmt: skip


@pytest.mark.parametrize("name", list(NO_HEADING))
def test_the_holders_name_and_address_below_a_run_are_never_sent(name):
    page = NO_HEADING[name]
    doc = pages_document([page], sha256="x", kind="pdf", names=["Alex Example"])
    sent = sent_lines(doc)
    texts = [sent[r].text for r in doc.data_refs]
    assert all(row in texts for row in ROWS)
    assert [s for s in SECRETS[:6] if any(s in t for t in texts)] == []
    shown = [*texts, *(doc.by_ref()[r].text for r in doc.held_amount_refs)]
    assert all(line in shown for line in page[1:3])  # the first run: sent or reported


def test_held_lines_never_start_the_table():
    """Two dated lines that may be rows or totals ("CASH PAID IN", "CREDIT") don't make a run:
    the table starts at the rows after them."""
    text = "\n".join(["Example Bank", "02/10/2026 CASH PAID IN AT BRANCH 50.00",
                      "02/10/2026 CREDIT 900.00", *ROWS])  # fmt: skip
    doc = text_document(text, sha256="x")
    assert [doc.by_ref()[r].text for r in doc.data_refs] == ROWS
    assert doc.held_amount_refs == ["L2", "L3"]


def test_a_two_row_page_followed_by_an_address_still_starts_the_table():
    """The one run there is stays the table start, even with an address after it."""
    page = ["Example Bank plc", *ROWS, "1 Example Street", "Exampletown EX1 2MP"]
    doc = pages_document([page], sha256="x", kind="pdf")
    assert [doc.by_ref()[r].text for r in doc.data_refs] == ROWS


# Coordinator probe after 9f27c16 (p_fallback): an address is withheld wherever it is printed,
# whatever the table start (rule (b)). A block is two or more short lines with no amount, date or
# merchant-like token, with a street or building word, a postcode, a holder's or household name,
# a titled name, or three or more lines of capitalised words.
ROW_A, ROW_B = "02/10/2026 TESCO 12.50", "03/10/2026 SALARY 2,000.00"
ROW_C, ROW_D = "04/10/2026 BOOTS 5.00", "05/10/2026 SHELL 40.00"
ADDRESS_LINES = {
    "street word": ["Rose Cottage", "Mill Lane", "Exampletown"],
    "no street word, three lines": ["The Old Rectory", "Church Lodge", "Littlebury"],
    "one street word": ["The Old Rectory", "Church Road", "Littlebury"],
    "capitalised lines only": ["The Old Rectory", "Upper Example", "Littlebury"],
    "postcode": ["Rectory Farmhouse", "Littlebury EX1 2MP"],
}
NAMES_ABOVE = {"no name": [], "titled name": ["MRS A EXAMPLE"], "household name": ["Alex Example"]}
PLACES = {
    "between rows": lambda block: [ROW_A, ROW_B, *block, ROW_C, ROW_D],
    "at the end": lambda block: [ROW_A, ROW_B, *block],
    "at the start": lambda block: ["Example Bank plc", *block, ROW_A, ROW_B],
}


@pytest.mark.parametrize("names", list(NAMES_ABOVE))
@pytest.mark.parametrize("place", list(PLACES))
@pytest.mark.parametrize("address", list(ADDRESS_LINES))
def test_an_address_is_never_sent_wherever_it_is(address, place, names):
    block = [*NAMES_ABOVE[names], *ADDRESS_LINES[address]]
    lines = PLACES[place](block)
    doc = text_document("\n".join(lines), sha256="x", names=["Alex Example"])
    sent = [doc.by_ref()[r].text for r in doc.data_refs]
    assert [line for line in block if line in sent] == [], sent
    rows = [line for line in lines if line[:2].isdigit()]
    shown = set(doc.data_refs) | set(doc.held_amount_refs)
    assert all(f"L{lines.index(row) + 1}" in shown for row in rows)  # sent or reported


def test_the_coordinators_five_pages():
    addr = ["Rose Cottage", "Mill Lane", "Exampletown"]
    pages = [
        [ROW_A, ROW_B, *addr],
        [ROW_A, ROW_B, *addr, ROW_C, ROW_D],
        [ROW_A, ROW_B, "MRS A EXAMPLE", *addr, ROW_C],
        [ROW_A, ROW_B, "Alex Example", "1 Mill Lane", "Exampletown EX1 1AA", ROW_C],
        [ROW_A, ROW_B, "The Old Rectory", "Church Road", "Littlebury"],
    ]
    for lines in pages:
        doc = text_document("\n".join(lines), sha256="0" * 64, names=["Alex Example"])
        sent = [doc.by_ref()[r].text for r in doc.data_refs]
        assert all(line[:2].isdigit() for line in sent), sent


def test_a_wrapped_merchant_description_is_still_sent():
    lines = [ROW_A, "03/10/2026 AMAZON MARKETPLACE 23.99", "AMAZON MARKETPLACE",
             "AMZN.CO.UK/PM", ROW_C]  # fmt: skip
    doc = text_document("\n".join(lines), sha256="x")
    sent = [doc.by_ref()[r].text for r in doc.data_refs]
    assert sent == lines


def test_a_single_continuation_line_after_a_row_is_sent_unless_it_shows_a_detail():
    lines = [ROW_A, "03/10/2026 FASTER PAYMENT 25.00", "REF RENT OCTOBER", ROW_C]
    doc = text_document("\n".join(lines), sha256="x")
    assert "L3" in doc.data_refs
    lines[2] = "Sort code 20-11-33"
    doc = text_document("\n".join(lines), sha256="x")
    assert "L3" not in doc.data_refs


# Coordinator probe after b0f51cc (p_addr2): address lines with a merchant-like or date-like token
# ("10/12 High St.", "Flat 2/1", "Unit 5+6") are still parts of the address, so the block doesn't
# end at them, and a street line standing alone after a row is withheld too.
ADDRESS_LAYOUTS = [
    ["Flat 2/1", "10 Example Street", "Glasgow"],
    ["Flat 2/1", "Rose Court", "Exampletown"],
    ["Unit 5+6", "Mill Lane", "Exampletown"],
    ["The Old Rectory", "St. John's Close", "Littlebury"],
    ["Apt 2B", "10/12 High St.", "Exampletown"],
    ["c/o A Example", "Rose Cottage", "Exampletown"],
]


@pytest.mark.parametrize("where", ["after rows", "header"])
@pytest.mark.parametrize("address", ADDRESS_LAYOUTS, ids=[" / ".join(a) for a in ADDRESS_LAYOUTS])
def test_the_coordinators_address_layouts_are_withheld(address, where):
    if where == "after rows":
        lines = [ROW_A, ROW_B, *address, ROW_C]
    else:
        lines = ["Example Bank", "Alex Example", *address,
                 "Date Description Paid out Paid in Balance", ROW_A, ROW_B]  # fmt: skip
    doc = text_document("\n".join(lines), sha256="0" * 64, names=["Alex Example"])
    sent = [doc.by_ref()[r].text for r in doc.data_refs]
    assert [line for line in address if line in sent] == [], sent
    assert all(row in sent for row in (ROW_A, ROW_B))


@pytest.mark.parametrize("line", ["10/12 High St.", "Flat 3, 5 Example Road", "Rose Court"])
def test_a_street_line_standing_alone_after_a_row_is_withheld(line):
    lines = [ROW_A, line, ROW_C]
    doc = text_document("\n".join(lines), sha256="x")
    assert "L2" not in doc.data_refs and "L2" not in doc.held_amount_refs  # no amount on it
    assert doc.data_refs == ["L1", "L3"]


# R-M3-25 (2): a line that starts with a row's date is never an address line ("02 Oct 26 VIS
# TESCO HIGH ST", a row's details over two lines), but a house number before a house or street
# named after a month ("12 March Cottage", "1 May Road") still may be one.
MONTH_NAMED = [
    ["Alex Example", "12 March Cottage", "Exampletown EX1 1AA"],
    ["1 May House", "Exampletown"],
    ["1 May Road", "Exampletown"],
    ["Rose Cottage", "3 June Villas", "Littlebury"],
]


@pytest.mark.parametrize("heading", [True, False], ids=["heading", "no heading"])
@pytest.mark.parametrize("block", MONTH_NAMED, ids=[" / ".join(b) for b in MONTH_NAMED])
def test_an_address_named_after_a_month_is_still_withheld(block, heading):
    lines = [*(["Example Bank", "Date Description Amount"] if heading else []), ROW_A, ROW_B,
             *block, ROW_C, ROW_D]  # fmt: skip
    for doc in (
        text_document("\n".join(lines), sha256="x", names=["Alex Example"]),
        pages_document([lines], sha256="x", kind="pdf", names=["Alex Example"]),
    ):
        sent = [doc.by_ref()[r].text for r in doc.data_refs]
        assert [line for line in block if line in sent] == [], sent
        assert all(row in sent for row in (ROW_A, ROW_B, ROW_C, ROW_D))
