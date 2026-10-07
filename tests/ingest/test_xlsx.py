from datetime import datetime

import openpyxl

from tuppence.ingest.check import check_document
from tuppence.ingest.importers.csv_layout import parse_with_layout
from tuppence.ingest.importers.xlsx import cell_text, xlsx_records
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack
from tuppence.ingest.textprep import table_document


def make_workbook(path):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Example Bank statement export"])
    sheet.append([])
    sheet.append(["Date", "Description", "Money in", "Money out", "Balance"])
    sheet.append([datetime(2026, 10, 1), "GREENBASKET STORES", None, 42.18, 957.82])
    sheet.append([datetime(2026, 10, 17), "ACME PAYROLL LTD", 1650, None, 2607.82])
    sheet.append([datetime(2026, 10, 20), "NORTHLINE RAIL", None, 28.9, 2578.92])
    book.save(path)


def test_cell_text():
    assert cell_text(None) == "" and cell_text(42.1) == "42.10" and cell_text(1650) == "1650"
    assert (
        cell_text(datetime(2026, 10, 1, 9, 30)) == "01/10/2026"
        and cell_text("  two   words ") == "two words"
    )


def test_workbook_reads_like_a_csv(tmp_path):
    path = tmp_path / "statement.xlsx"
    make_workbook(path)
    records = xlsx_records(str(path))
    assert records[3] == (
        4,
        "01/10/2026, GREENBASKET STORES, , 42.18, 957.82",
        ["01/10/2026", "GREENBASKET STORES", "", "42.18", "957.82"],
    )
    registry = LayoutRegistry(load_bank_pack())
    doc = table_document(records, sha256="x", kind="xlsx", known=registry.is_known_header)
    assert (doc.preamble_refs, doc.header_refs, doc.data_refs) == (
        ["L1"],
        ["L3"],
        ["L4", "L5", "L6"],
    )
    layout = registry.match(doc)
    assert layout.id == "santander"
    parsed = parse_with_layout(doc, layout).parsed
    assert check_document(doc, parsed) == []
    assert [r.amount_pence for r in parsed.rows] == [-4218, 165000, -2890]
    assert parsed.opening_balance_pence == 100000


def test_zip_bomb_and_non_workbooks_are_refused(tmp_path, monkeypatch):
    import zipfile

    import pytest

    from tuppence.ingest.importers import xlsx

    junk = tmp_path / "junk.xlsx"
    junk.write_bytes(b"not a zip")
    with pytest.raises(xlsx.WorkbookRejected, match="Excel workbook"):
        xlsx.xlsx_records(str(junk))
    bomb = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/big.xml", b"0" * 4096)
    monkeypatch.setattr(xlsx, "MAX_UNPACKED_BYTES", 1000)
    with pytest.raises(xlsx.WorkbookRejected, match="too large"):
        xlsx.xlsx_records(str(bomb))
