import codecs

from tuppence.ingest.models import Line
from tuppence.ingest.textprep import (
    csv_document,
    csv_records,
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
