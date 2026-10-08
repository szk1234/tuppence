import codecs
import time

import pytest

from tuppence.ingest.models import Line
from tuppence.ingest.textprep import (
    UnreadableFile,
    csv_document,
    csv_records,
    has_amount,
    is_anchor,
    is_summary,
    pages_document,
    plan_chunks,
    render,
    sniff_delimiter,
    split_preamble,
    text_document,
)

EXCEL_STYLE = (
    '"Account Name:","FlexAccount ****45678"\r\n'
    '"Account Balance:","£1,252.70"\r\n'
    "\r\n"
    '"Date","Transaction type","Description","Paid out","Paid in","Balance"\r\n'
    '"02 Oct 2026","Payment to","Page and\r\nSpine Books","£12.40","","£987.60"\r\n'
    '"04 Oct 2026","Direct debit","City Water","£31.15","","£956.45"\r\n'
)


def test_excel_resaved_csv_reads_the_same_in_every_encoding():
    for data in (
        EXCEL_STYLE.encode(),
        codecs.BOM_UTF8 + EXCEL_STYLE.encode(),
        EXCEL_STYLE.encode("cp1252"),
    ):
        doc = csv_document(data, sha256="x")
        assert doc.preamble_refs == ["L1", "L2"]
        assert doc.header_refs == ["L4"]
        assert doc.data_refs == ["L5", "L7"]  # the quoted line break keeps L6 inside the L5 record
        by_ref = doc.by_ref()
        assert (
            by_ref["L5"].text
            == '"02 Oct 2026","Payment to","Page and Spine Books","£12.40","","£987.60"'
        )
        assert doc.table[doc.lines.index(by_ref["L5"])][2] == "Page and\nSpine Books"


def test_csv_without_heading_row_is_all_data():
    doc = csv_document(b'01/10/2026,"SHOP","-4.00"\n02/10/2026,"CAFE","-3.40"\n', sha256="x")
    assert doc.header_refs == [] and doc.preamble_refs == [] and doc.data_refs == ["L1", "L2"]


def test_known_header_callback_wins():
    data = b"Ref,Notes\nA,B\nDate,Amount\n01/10/2026,-4.00\n"
    doc = csv_document(data, sha256="x", known=lambda cells: list(cells) == ["Date", "Amount"])
    assert doc.header_refs == ["L3"] and doc.preamble_refs == ["L1", "L2"]


def test_delimiters():
    assert sniff_delimiter("a;b;c\n1;2;3\n") == ";"
    assert sniff_delimiter("a\tb\tc\n1\t2\t3\n") == "\t"
    assert sniff_delimiter('Date,Amount\n01/10/2026,"1,234.00"\n') == ","
    assert [cells for _, _, cells in csv_records("a;b\n1;2\n")] == [["a", "b"], ["1", "2"]]


def test_preamble_stops_at_the_first_dated_amount_or_heading_row():
    lines = [
        Line(ref=f"P1L{i}", text=t)
        for i, t in enumerate(
            [
                "Alex Example",
                "1 Example Road",
                "Account number 12345678",
                "Date Description Amount",
                "01 Oct 2026 Shop 4.00",
            ],
            start=1,
        )
    ]
    assert split_preamble(lines) == (["P1L1", "P1L2", "P1L3"], ["P1L4", "P1L5"])
    assert split_preamble([Line(ref="L1", text="Little Cafe -£3.40")]) == ([], ["L1"])


def test_text_and_pages_documents():
    doc = text_document("Alex Example\n\n01 Oct 2026  Shop  4.00\n", sha256="x")
    assert [ln.ref for ln in doc.lines] == ["L1", "L3"] and doc.preamble_refs == ["L1"]
    pages = pages_document(
        [["Header", "01 Oct 2026 Shop 4.00"], ["02 Oct 2026 Cafe 3.40"]], sha256="x", kind="pdf"
    )
    assert [ln.ref for ln in pages.lines] == ["P1L1", "P1L2", "P2L1"] and pages.pages == 2
    shot = pages_document([["Little Cafe -£3.40"]], sha256="x", kind="image", preamble=False)
    assert shot.data_refs == ["P1L1"]


def test_chunks_carry_the_headings_and_the_previous_line():
    rows = ["Alex Example", "Date Description Paid out Paid in Balance"] + [
        f"{d:02d} Oct 2026   Shop   {d}.00   {900 - d}.00" for d in range(1, 10)
    ]
    doc = pages_document([rows], sha256="x", kind="pdf")
    chunks = plan_chunks(doc, rows_per_chunk=4)
    assert [c.data_refs for c in chunks] == [
        ["P1L2", "P1L3", "P1L4", "P1L5"],
        ["P1L6", "P1L7", "P1L8", "P1L9"],
        ["P1L10", "P1L11"],
    ]
    assert chunks[0].context_refs == []
    assert chunks[1].context_refs == ["P1L2", "P1L5"]
    assert [ln.ref for ln in chunks[1].lines] == ["P1L2", "P1L5", "P1L6", "P1L7", "P1L8", "P1L9"]
    csv = csv_document(
        b"Date,Amount\n" + b"".join(f"0{d}/10/2026,-{d}.00\n".encode() for d in range(1, 6)),
        sha256="x",
    )
    assert all(c.context_refs == ["L1"] for c in plan_chunks(csv, rows_per_chunk=2))
    # lines are kept and sent normalised: one space between words (sensitive.normalise)
    assert render(chunks[2].lines).splitlines()[-1] == "P1L11: 09 Oct 2026 Shop 9.00 891.00"


# --- R-M3-6: sensitive lines, running headers, robustness -----------------------------


def _split(rows):
    d = pages_document([rows], sha256="x", kind="pdf")
    return d, {ln.ref: ln.text for ln in d.lines}


def _data_texts(doc):
    by = doc.by_ref()
    return [by[r].text for r in doc.data_refs]


def test_account_lines_after_a_summary_line_are_still_withheld():
    d, _ = _split(
        [
            "Statement date 01 Oct 2026",
            "Mr Alex Example",
            "12 Marlborough Road",
            "London SW1A 1AA",
            "Opening balance on 1 Oct 2026 £1,000.00",
            "Account number 12345678 Sort code 12-34-56",
            "Date Description Paid out Paid in Balance",
            "02 Oct 2026 Shop 4.00 996.00",
        ]
    )
    assert _data_texts(d) == [
        "Date Description Paid out Paid in Balance",
        "02 Oct 2026 Shop 4.00 996.00",
    ]


def test_money_in_out_box_is_not_an_anchor_and_not_data():
    d, _ = _split(
        [
            "Mr Alex Example",
            "Your account summary Money in £1,200.00 Money out £800.00",
            "Account number 12345678 Sort code 12-34-56",
            "Date Description Paid out Paid in Balance",
            "02 Oct 2026 Shop 4.00 996.00",
        ]
    )
    assert len(d.data_refs) == 2


def test_address_with_a_month_word_is_not_a_date_row():
    d, _ = _split(["Alex Example", "Flat 3, 12 Mar Road 10.00", "Date Description"])
    assert _data_texts(d) == ["Date Description"] or _data_texts(d) == []
    assert "Flat 3, 12 Mar Road 10.00" not in _data_texts(d)
    assert not is_anchor("12 Mar Road 10.00")
    assert is_anchor("12 Mar 2026 Shop 10.00")


def test_account_type_and_card_ending_are_withheld_anywhere():
    d, _ = _split(
        [
            "Alex Example",
            "Account type: Credit card",
            "Payment type: Direct debit",
            "Date Description Amount",
            "02 Oct 2026 Shop 4.00",
            "Card ending 4242",
            "IBAN GB29 NWBK 6016 1331 9268 19",
            "Alex Example, 1 High Street, London E1 6AN",
            "03 Oct 2026 Cafe 3.00",
        ]
    )
    assert _data_texts(d) == [
        "Date Description Amount",
        "02 Oct 2026 Shop 4.00",
        "03 Oct 2026 Cafe 3.00",
    ]


def test_running_headers_never_reach_a_chunk():
    pages = [
        [
            "Alex Example",
            "Date Description Paid out Paid in Balance",
            "02 Oct 2026 Shop 4.00 996.00",
        ]
        if n == 1
        else [
            f"Alex Example  Account 12345678  Sort code 12-34-56  Page {n} of 3",
            "Alex Example Account holder statement",
            "Date Description Paid out Paid in Balance",
            f"0{n} Oct 2026 Shop 4.00 {990 - n}.00",
        ]
        for n in (1, 2, 3)
    ]
    pages[0].insert(1, "Alex Example Account holder statement")
    doc = pages_document(pages, sha256="x", kind="pdf")
    sent = "\n".join(render(c.lines) for c in plan_chunks(doc, rows_per_chunk=2))
    for secret in ("Alex Example", "12345678", "12-34-56"):
        assert secret not in sent
    assert "Date Description Paid out Paid in Balance" in sent


def test_identical_rows_are_kept():
    d = pages_document(
        [["Date Description Amount", "02 Oct 2026 Coffee 3.40", "02 Oct 2026 Coffee 3.40"]],
        sha256="x",
        kind="pdf",
    )
    assert len(d.data_refs) == 3


def test_oversized_csv_field_is_a_plain_error():
    big = b"a" * 200_000
    assert csv_document(b"Date,Desc\n01/10/2026," + big + b"\n", sha256="x").data_refs
    assert csv_document(b'Date,Desc\n01/10/2026,"' + big + b'"\n', sha256="x").data_refs
    with pytest.raises(UnreadableFile, match="value that's too long to read"):
        csv_document(b"Date,Desc\n01/10/2026," + b"a" * 1_100_000 + b"\n", sha256="x")


def test_planning_twenty_thousand_lines_is_fast():
    rows = ["Date Description Paid out Paid in Balance"] + [
        f"{1 + i % 28:02d} Oct 2026 Shop {i}.00 {900000 - i}.00" for i in range(20_000)
    ]
    doc = pages_document([rows], sha256="x", kind="pdf")
    started = time.perf_counter()
    chunks = plan_chunks(doc, rows_per_chunk=20)
    assert time.perf_counter() - started < 2.0
    assert len(chunks) == 1001


# --- R-M3-7 -----------------------------------------------------------------------------

SENSITIVE_SPELLINGS = [
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


@pytest.mark.parametrize("line", SENSITIVE_SPELLINGS)
def test_account_detail_spellings_are_withheld_after_the_anchor(line):
    d, _ = _split(
        ["Date Description Amount", "02 Oct 2026 Shop 4.00", line, "03 Oct 2026 Cafe 3.00"]
    )
    assert line not in _data_texts(d)
    assert len(d.data_refs) == 3


def test_repeated_plain_descriptions_stay_data():
    d, _ = _split(
        [
            "Date Description Amount",
            "02 Oct",
            "Salary",
            "Tesco Stores",
            "03 Oct",
            "Salary",
            "Tesco Stores",
        ]
    )
    assert _data_texts(d).count("Salary") == 2 and _data_texts(d).count("Tesco Stores") == 2


def test_repeated_page_furniture_is_withheld():
    d, _ = _split(
        ["Date Description Amount", "02 Oct 2026 Shop 4.00", "Page 1 of 3", "Continued",
         "Page 2 of 3", "Continued", "Statement period 1 Oct to 31 Oct",
         "Statement period 1 Oct to 31 Oct",
         "03 Oct 2026 Cafe 3.00"]
    )  # fmt: skip
    assert _data_texts(d) == [
        "Date Description Amount",
        "02 Oct 2026 Shop 4.00",
        "03 Oct 2026 Cafe 3.00",
    ]


def test_two_hundred_thousand_csv_rows_chunk_quickly():
    data = b"Date,Desc,Amount\n" + b"".join(
        f"01/10/2026,Shop {i},-{i}.00\n".encode() for i in range(200_000)
    )
    doc = csv_document(data, sha256="x")
    started = time.perf_counter()
    plan_chunks(doc, rows_per_chunk=20)
    assert time.perf_counter() - started < 2.0


def test_a_holder_name_repeated_at_the_top_of_each_page_is_withheld():
    pages = [
        ["Date Description Amount", "02 Oct 2026 Shop 4.00", "03 Oct 2026 Cafe 3.00"],
        ["ALEX EXAMPLE", "Date Description Amount", "04 Oct 2026 Shop 4.00"],
        ["ALEX EXAMPLE", "Date Description Amount", "05 Oct 2026 Shop 4.00"],
    ]
    doc = pages_document(pages, sha256="x", kind="pdf")
    sent = "\n".join(render(c.lines) for c in plan_chunks(doc, rows_per_chunk=2))
    assert "ALEX EXAMPLE" not in sent and "Date Description Amount" in sent


def test_mid_page_description_lines_stay_data():
    d, _ = _split(
        ["Date Description Amount", "02 Oct 2026", "Salary", "1,650.00", "03 Oct 2026",
         "Salary", "1,650.00", "Faster payment", "Faster payment"]
    )  # fmt: skip
    assert _data_texts(d).count("Salary") == 2 and _data_texts(d).count("Faster payment") == 2


def test_a_preamble_name_repeated_later_is_withheld():
    d, _ = _split(
        ["Mr Alex Example", "1 Example Road", "Date Description Amount",
         "02 Oct 2026 Shop 4.00", "Alex Example", "03 Oct 2026 Cafe 3.00"]
    )  # fmt: skip
    assert "Alex Example" not in _data_texts(d)
    d2 = pages_document([["Date Description Amount", "Alex Example", "02 Oct 2026 Shop 4.00"]],
                        sha256="x", kind="pdf", names=["Alex Example"])  # fmt: skip
    assert "Alex Example" not in _data_texts(d2)


def _page_data(doc):
    by_ref = doc.by_ref()
    return [by_ref[r].text for r in doc.data_refs]


def test_each_later_pages_header_is_held_back():
    pages = [
        ["Alex Example", "Flat 2, The Old Mill", "Date Description Amount",
         "02 Oct 2026 Shop 4.00"],
        ["Statement continued", "Alex Example", "Flat 2, The Old Mill", "Riverside",
         "Date Description Amount", "03 Oct 2026 Cafe 3.00"],
        ["Exampleton branch", "04 Oct 2026 Shop 4.00", "Card payment"],  # a dated row anchors
    ]  # fmt: skip
    doc = pages_document(pages, sha256="x", kind="pdf")
    assert _page_data(doc) == [
        "Date Description Amount",
        "02 Oct 2026 Shop 4.00",
        "Date Description Amount",
        "03 Oct 2026 Cafe 3.00",
        "04 Oct 2026 Shop 4.00",
        "Card payment",  # after the page's first row: a description line, as on page 1
    ]
    assert {"P2L1", "P2L2", "P2L3", "P2L4", "P3L1"} <= set(doc.preamble_refs)
    assert doc.held_amount_refs == []


def test_rows_above_a_later_pages_first_anchor_are_reported_not_lost():
    pages = [
        ["Date Description Amount Balance", "02 Oct 2026 Shop 4.00 996.00"],
        ["Alex Example", "Little Cafe 3.40 992.60", "03 Oct 2026 Shop 4.00 988.60"],
    ]
    doc = pages_document(pages, sha256="x", kind="pdf")
    assert "Little Cafe 3.40 992.60" not in _page_data(doc)
    assert doc.held_amount_refs == ["P2L2"]


def test_a_balance_brought_forward_above_a_later_pages_rows_is_withheld_not_reported():
    """A balance figure is read on this device and never sent; it isn't a row either."""
    pages = [
        ["Date Description Amount Balance", "02 Oct 2026 Shop 4.00 996.00"],
        ["Page 2", "Balance brought forward 996.00", "03 Oct 2026 Shop 4.00 992.00"],
    ]
    doc = pages_document(pages, sha256="x", kind="pdf")
    assert "Balance brought forward 996.00" not in _page_data(doc)
    assert doc.held_amount_refs == []


def test_text_files_have_no_pages_to_hold_back():
    doc = text_document(
        "Date Description Amount\n02 Oct 2026 Shop 4.00\nRiverside\n03 Oct 2026 Cafe 3.00\n",
        sha256="x",
    )
    assert "Riverside" in _page_data(doc)


def test_card_endings_in_a_later_pages_header_and_mid_table_are_withheld():
    pages = [
        ["Nationwide Building Society", "Alex Example", "1 Example Road", "Exampletown EX1 2MP",
         "Account number 12345678", "Card ending 4242",
         "Date Description Paid out Paid in Balance",
         "02 Oct 2026 Greenbasket Stores 42.18 957.82"],
        ["Statement continued", "Alex Example", "Card ending 4242", "Account number 12345678",
         "Date Description Paid out Paid in Balance",
         "05 Oct 2026 Little Cafe 3.40 954.42",
         "Card ending 4242",  # mid-table
         "06 Oct 2026 Northline Rail 28.90 925.52"],
    ]  # fmt: skip
    doc = pages_document(pages, sha256="x", kind="pdf", names=["Alex Example"])
    sent = "\n".join(render(c.lines) for c in plan_chunks(doc, rows_per_chunk=2))
    for secret in ("4242", "12345678", "Alex Example", "Example Road", "EX1 2MP"):
        assert secret not in sent
    assert _page_data(doc) == [
        "Date Description Paid out Paid in Balance",
        "02 Oct 2026 Greenbasket Stores 42.18 957.82",
        "Date Description Paid out Paid in Balance",
        "05 Oct 2026 Little Cafe 3.40 954.42",
        "06 Oct 2026 Northline Rail 28.90 925.52",
    ]


def test_an_anchor_like_line_first_on_a_later_page_exposes_nothing_below_it():
    page1 = ["Barclaycard", "Alex Example", "Flat 2, The Old Mill", "Riverside",
             "Exampletown EX1 2MP", "Card number ending 4242",
             "Date Description Amount", "02 Oct 2026 Greenbasket Stores 42.18"]  # fmt: skip
    page2 = ["02 Nov 2026 Minimum payment due 25.00",  # a dated amount: the page's anchor
             "Alex Example", "Flat 2, The Old Mill", "Riverside", "Exampletown EX1 2MP",
             "Date Description Amount", "05 Oct 2026 Little Cafe 3.40"]  # fmt: skip
    doc = pages_document([page1, page2], sha256="x", kind="pdf")
    data = _page_data(doc)
    for secret in ("Alex Example", "Flat 2", "Riverside", "Exampletown", "4242"):
        assert not any(secret in line for line in data), secret
    assert "05 Oct 2026 Little Cafe 3.40" in data


def test_an_unlabelled_address_printed_only_on_page_two_is_withheld():
    page1 = ["Nationwide Building Society", "Date Description Amount",
             "02 Oct 2026 Greenbasket Stores 42.18"]  # fmt: skip
    page2 = ["Mr A Example", "14 Quarry Lane", "Exampleton", "EX9 9ZZ",
             "Date Description Amount", "05 Oct 2026 Little Cafe 3.40"]  # fmt: skip
    doc = pages_document([page1, page2], sha256="x", kind="pdf")
    assert _page_data(doc) == [
        "Date Description Amount",
        "02 Oct 2026 Greenbasket Stores 42.18",
        "Date Description Amount",
        "05 Oct 2026 Little Cafe 3.40",
    ]


# --- I1 (final review): the table starts at its heading row or a run of rows ------------------

# Lines a UK statement prints above the address: a dated balance, limit or payment due, and a
# summary box merged onto an address row. None of them may start the table.
HEADER_SUMMARIES = [
    "Balance on 31/10/2026 £2,252.32",
    "Your balance at 31 Oct 2026: £2,252.32",
    "Payment due 20/11/2026  Minimum payment £25.00",
    "Payment due by 20/11/2026: minimum payment £25.00",
    "Statement date 05/11/2026   Overdraft limit £500.00",
    "Flat 3   Minimum payment £25.00",
]
ADDRESS = ["MR ALEX EXAMPLE", "Flat 3", "Example House", "Exampletown", "EX1 2MP"]


@pytest.mark.parametrize("summary", HEADER_SUMMARIES)
def test_a_dated_balance_or_payment_due_line_above_the_address_is_never_sent(summary):
    page = ["Example Bank plc", summary, *ADDRESS, "Sort code 07-12-34 Account 12345678",
            "Date Description Amount Balance",
            "01 Oct 2026 Greenbasket Stores -42.18 2,210.14"]  # fmt: skip
    doc = pages_document([page], sha256="x", kind="pdf", names=["Alex Example"])
    assert _page_data(doc) == [
        "Date Description Amount Balance",
        "01 Oct 2026 Greenbasket Stores -42.18 2,210.14",
    ]
    assert doc.held_amount_refs == []  # a balance or a due date: nothing to report


def test_the_table_starts_at_its_heading_row_not_a_lone_dated_line_above_it():
    page = ["Example Bank plc", "Last statement 05/10/2026 £1,000.00", *ADDRESS,
            "Date Description Amount", "01 Oct 2026 Shop 4.00",
            "02 Oct 2026 Cafe 3.00"]  # fmt: skip
    doc = pages_document([page], sha256="x", kind="pdf")
    assert _page_data(doc) == [
        "Date Description Amount",
        "01 Oct 2026 Shop 4.00",
        "02 Oct 2026 Cafe 3.00",
    ]
    assert doc.held_amount_refs == ["P1L2"]  # it may be a row: reported, never lost


def test_without_a_heading_row_the_table_starts_at_the_first_run_of_rows():
    page = ["Example Bank plc", "Last statement 05/10/2026 £1,000.00", *ADDRESS,
            "01 Oct 2026 Shop 4.00", "Card purchase", "02 Oct 2026 Cafe 3.00"]  # fmt: skip
    doc = pages_document([page], sha256="x", kind="pdf")
    assert _page_data(doc) == ["01 Oct 2026 Shop 4.00", "Card purchase", "02 Oct 2026 Cafe 3.00"]
    assert doc.held_amount_refs == ["P1L2"]


def test_rows_before_a_heading_printed_only_on_a_later_page_are_data():
    pages = [
        ["Example Bank plc", "01 Oct 2026 Shop 4.00", "02 Oct 2026 Cafe 3.00"],
        ["Date Description Amount", "03 Oct 2026 Bus 2.00"],
    ]
    doc = pages_document(pages, sha256="x", kind="pdf")
    assert _page_data(doc)[:2] == ["01 Oct 2026 Shop 4.00", "02 Oct 2026 Cafe 3.00"]


def test_an_address_block_without_a_street_word_is_withheld_wherever_it_is():
    """Rows three lines apart make no run, so a lone dated line starts the table; the address
    below it is still a block of short lines with a postcode or two address parts."""
    page = ["Example Bank plc", "Last statement 05/10/2026 £1,000.00", "Flat 3",
            "Example House", "Exampletown", "01 Oct 2026 Shop 4.00", "Card purchase",
            "Greenbasket", "02 Oct 2026 Cafe 3.00"]  # fmt: skip
    doc = pages_document([page], sha256="x", kind="pdf")
    data = _page_data(doc)
    for secret in ("Flat 3", "Example House", "Exampletown"):
        assert secret not in data
    assert "Card purchase" in data and "Greenbasket" in data


@pytest.mark.parametrize(
    "block",
    [
        ["Flat 3", "Example House", "Exampletown"],
        ["Example House", "Exampletown", "EX1 2MP"],
        ["The Old Mill", "Riverside", "Exampletown", "EX9 9ZZ"],
        ["Apartment 12", "Harbour Court", "Exampleton"],
    ],
)
def test_address_blocks_after_the_first_row_are_withheld(block):
    page = ["Date Description Amount", "01 Oct 2026 Shop 4.00", *block, "02 Oct 2026 Cafe 3.00"]
    doc = pages_document([page], sha256="x", kind="pdf")
    assert _page_data(doc) == [
        "Date Description Amount",
        "01 Oct 2026 Shop 4.00",
        "02 Oct 2026 Cafe 3.00",
    ]


def test_description_lines_are_not_taken_for_an_address():
    page = ["Date Description Amount", "01 Oct 2026 Shop 4.00", "Card purchase",
            "Greenbasket Stores", "02 Oct 2026 Cafe 3.00", "Contactless"]  # fmt: skip
    doc = pages_document([page], sha256="x", kind="pdf")
    assert len(_page_data(doc)) == 6


# --- I2: details inside a row are masked; nothing withheld past the table start is lost -------

TABLE_TOP = ["Date Description Amount", "01 Oct 2026 Shop 4.00"]
TABLE_END = ["30 Oct 2026 Cafe 3.00"]
# (line, what happens to it past the table start)
OUTCOMES = [
    ("03/10/2026 Transfer to A/C 87654321 -250.00", "sent masked"),
    ("FPO J SMITH 20-11-33 41234567 -250.00", "sent masked"),
    ("TFR TO SAVINGS ...5678 50.00", "sent masked"),
    ("TOTAL ENERGIES 45.00", "sent"),  # a payee, not a total
    ("Little Cafe 3.40", "sent"),
    ("Total 1,234.56", "withheld"),  # a total: not a row
    ("Money in £1,650.00    Money out £438.78", "withheld"),
    ("Balance £1,234.56", "withheld"),  # a balance: read on this device
    ("Payment due by 20/11/2026: minimum payment £25.00", "withheld"),
    ("Flat 3   Minimum payment £25.00", "withheld"),  # a balance merged onto an address row
    ("Card ending 4242", "withheld"),  # no amount: nothing to lose
    ("Alex Example", "withheld"),
]


@pytest.mark.parametrize(("line", "outcome"), OUTCOMES, ids=[o[0] for o in OUTCOMES])
def test_what_happens_to_each_line_past_the_table_start(line, outcome):
    doc = pages_document([[*TABLE_TOP, line, *TABLE_END]], sha256="x", kind="pdf",
                         names=["Alex Example"])  # fmt: skip
    ref = "P1L3"
    assert doc.held_amount_refs == []
    if outcome == "withheld":
        assert ref in doc.preamble_refs
    else:
        assert ref in doc.data_refs
        assert (ref in doc.masked) == (outcome == "sent masked")


def test_a_line_that_cant_be_masked_is_held_back_and_reported(monkeypatch):
    from tuppence.ingest import sensitive

    real = sensitive.prepare_outbound
    monkeypatch.setattr(
        sensitive,
        "prepare_outbound",
        lambda text, names=(), **near: (
            None if sensitive.classify(text, names=names) else real(text, **near)
        ),
    )
    line = "03/10/2026 Transfer to A/C 87654321 -250.00"
    doc = pages_document([[*TABLE_TOP, line, *TABLE_END]], sha256="x", kind="pdf")
    assert "P1L3" in doc.preamble_refs and doc.held_amount_refs == ["P1L3"]


def test_every_withheld_line_with_an_amount_is_reported_unless_it_is_a_balance_or_total():
    from tuppence.ingest import sensitive

    lines = [line for line, _ in OUTCOMES] + [
        "Account 12345678 sort code 12-34-56 50.00",
        "Sort code 12-34-56 Account 12345678",
        "Visa ****4242 9.99",
        "Mr A Example 12.00",
    ]
    doc = pages_document([[*TABLE_TOP, *lines, *TABLE_END]], sha256="x", kind="pdf",
                         names=["Alex Example"])  # fmt: skip
    by_ref = doc.by_ref()
    for ref in doc.preamble_refs:
        text = by_ref[ref].text
        if has_amount(text):
            assert (
                ref in doc.held_amount_refs
                or sensitive.is_masked_balance(text, names=["Alex Example"])
                or is_summary(text)
            ), text


def test_the_first_row_of_a_table_may_carry_account_details():
    page = ["Example Bank plc", "01/10/2026 FPO J SMITH 20-11-33 41234567 -250.00",
            "02/10/2026 Little Cafe -3.40"]  # fmt: skip
    doc = pages_document([page], sha256="x", kind="pdf")
    assert doc.data_refs == ["P1L2", "P1L3"] and "P1L2" in doc.masked


def test_a_screenshot_row_with_account_details_is_sent_masked():
    rows = ["5 Oct SHOP -£3.40", "6 Oct Transfer to 12-34-56 87654321 -£50.00", "7 Oct CAFE -£1.00"]
    doc = pages_document([rows], sha256="x", kind="image")
    assert doc.data_refs == ["P1L1", "P1L2", "P1L3"]
    assert "87654321" not in doc.masked["P1L2"].text and "-£50.00" in doc.masked["P1L2"].text
    assert doc.held_amount_refs == []


def test_chunks_carry_the_masked_text():
    line = "03/10/2026 Transfer to A/C 87654321 -250.00"
    doc = pages_document([[*TABLE_TOP, line, *TABLE_END]], sha256="x", kind="pdf")
    sent = "\n".join(render(c.lines) for c in plan_chunks(doc, rows_per_chunk=2))
    assert "87654321" not in sent and "-250.00" in sent and "Transfer to A/C" in sent


# --- balance and summary figures are never sent (scan follow-up) -------------------------------

MIXED_SUMMARIES = [
    "Closing balance 31/03 £1,234.56 Total money in £2,000.00",
    "Closing balance £1,234.56 Page 2",
    "Opening balance on 01/10/2026 £5,555.55 (see overleaf)",
    "Total money in £2,000.00 this period",
    "Total paid out 438.78 Total paid in 1,650.00",
    "Payments total £190.00",
    "Money in £1,650.00 Money out £438.78 Statement 3",
    "Balance carried forward 825.07",
    "01 Oct 2026 Balance brought forward 5,555.55",
    "Brought forward 1,000.00 CR",
    "Total 1,234.56",
    "Summary of your account £1,234.56",
]


@pytest.mark.parametrize("line", MIXED_SUMMARIES)
def test_a_line_with_a_balance_or_summary_phrase_and_a_figure_is_withheld(line):
    doc = pages_document([[*TABLE_TOP, line, *TABLE_END]], sha256="x", kind="pdf")
    assert "P1L3" in doc.preamble_refs and "P1L3" not in doc.data_refs
    assert doc.held_amount_refs == []  # a balance or a total: read here, not a row


PAYEE_ROWS = [
    ("02/10/2026 BALANCE TRANSFER FEE 5.00", False),
    ("02/10/2026 TOTAL FITNESS GYM 30.00 970.00", False),
    ("TOTAL ENERGIES 45.00", False),
    ("02 Oct 2026 SUMMARY PRINTING LTD 12.00", False),
    ("02/10/2026 TOTAL FITNESS GYM A/C 12345678 30.00", True),
    ("02/10/2026 BALANCE TRANSFER TO 20-11-33 41234567 50.00", True),
]


@pytest.mark.parametrize(("line", "masked"), PAYEE_ROWS, ids=[p[0] for p in PAYEE_ROWS])
def test_a_payee_row_that_only_contains_a_summary_word_is_sent(line, masked):
    doc = pages_document([[*TABLE_TOP, line, *TABLE_END]], sha256="x", kind="pdf")
    assert "P1L3" in doc.data_refs and doc.held_amount_refs == []
    assert ("P1L3" in doc.masked) == masked
    if masked:
        assert "12345678" not in doc.masked["P1L3"].text
        assert "41234567" not in doc.masked["P1L3"].text


@pytest.mark.parametrize(
    "line",
    ["02/10/2026 CASH PAID IN AT BRANCH 50.00", "Balance adjustment 12.00 3.00 9.00"],
)
def test_a_line_that_may_be_a_row_or_a_total_is_held_back_and_reported(line):
    """Can't tell: it is withheld and offered on the fix-up screen, never dropped silently."""
    doc = pages_document([[*TABLE_TOP, line, *TABLE_END]], sha256="x", kind="pdf")
    assert "P1L3" in doc.preamble_refs and doc.held_amount_refs == ["P1L3"]
