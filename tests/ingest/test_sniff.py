import io
import zipfile

import pytest

from tuppence.ingest.sniff import UploadRejected, check_zip, sniff

CAMT = (
    b'<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">'
    b"<BkToCstmrStmt/></Document>"
)


@pytest.mark.parametrize(
    "head,kind,ext",
    [
        (b"%PDF-1.7\n", "pdf", "pdf"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "image", "png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image", "jpg"),
        (b"PK\x03\x04\x14\x00", "xlsx", "xlsx"),
        (b"OFXHEADER:100\nDATA:OFXSGML\n", "ofx", "ofx"),
        (b'<?xml version="1.0"?>\n<?OFX OFXHEADER="200"?>\n<OFX>', "ofx", "ofx"),
        (b"!Type:Bank\nD01/10/2026\n", "qif", "qif"),
        (CAMT, "camt053", "xml"),
        (b"Date,Description,Amount\n01/10/2026,Shop,-4.00\n", "csv", "csv"),
        (
            b'"Account Name:","Flex ****45678"\n\n'
            b"Date,Description,Paid out,Paid in\n01/10/2026,Shop,4.00,\n",
            "csv",
            "csv",
        ),
        (b"Date;Description;Amount\n01/10/2026;Shop;-4,00\n", "csv", "csv"),
        (b"Statement for October\n01 Oct 2026 Shop 4.00\n", "text", "txt"),
    ],
)
def test_kinds_come_from_the_bytes(head, kind, ext):
    found = sniff(head)
    assert (found.kind, found.ext) == (kind, ext)


@pytest.mark.parametrize(
    "head,words",
    [
        (b"", "empty"),
        (b"MZ\x90\x00\x03\x00\x00\x00\x04\x00", "doesn't look like a statement"),
        (b"\x7fELF\x02\x01\x01\x00\x00", "doesn't look like a statement"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00", "HEIC"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".xls"),
        (b'<?xml version="1.0"?><html></html>', "isn't a CAMT.053"),
    ],
)
def test_refused_files_get_a_plain_reason(head, words):
    with pytest.raises(UploadRejected) as exc:
        sniff(head)
    assert words in str(exc.value)


def _zip(tmp_path, entries):
    path = tmp_path / "book.xlsx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    path.write_bytes(buf.getvalue())
    return path


def test_zip_bomb_is_refused_before_unpacking(tmp_path):
    path = _zip(
        tmp_path,
        {"xl/workbook.xml": "<workbook/>", "xl/worksheets/sheet1.xml": "0" * (5 * 1024 * 1024)},
    )
    assert path.stat().st_size < 100_000
    with pytest.raises(UploadRejected, match="unpacks to far more data"):
        check_zip(path)


def test_zip_that_is_not_a_workbook_is_refused(tmp_path):
    with pytest.raises(UploadRejected, match="ZIP files can't be read"):
        check_zip(_zip(tmp_path, {"statement.csv": "Date,Amount\n"}))


def test_damaged_zip_is_refused(tmp_path):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(UploadRejected, match="damaged"):
        check_zip(path)


def test_small_workbook_passes(tmp_path):
    check_zip(
        _zip(
            tmp_path, {"xl/workbook.xml": "<workbook/>", "xl/worksheets/sheet1.xml": "<sheetData/>"}
        )
    )
