import codecs
import time

import pytest

from tuppence.ingest.models import Line
from tuppence.ingest.textprep import (
    UnreadableFile,
    csv_document,
    csv_records,
    is_anchor,
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
    assert render(chunks[2].lines).splitlines()[-1] == "P1L11: 09 Oct 2026   Shop   9.00   891.00"


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
    assert time.perf_counter() - started < 1.0
    assert len(chunks) == 1001
