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
