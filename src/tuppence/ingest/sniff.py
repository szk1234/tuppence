"""What kind of file is this? Decided from its bytes, never from its name (spec §14.2)."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from tuppence.core.errors import UserFacing
from tuppence.ingest.models import FileKind
from tuppence.ingest.textnum import decode_text

EXTENSIONS: dict[str, str] = {
    "csv": "csv",
    "text": "txt",
    "ofx": "ofx",
    "qif": "qif",
    "camt053": "xml",
    "xlsx": "xlsx",
    "pdf": "pdf",
}
_HEIC_BRANDS = (
    b"ftypheic",
    b"ftypheix",
    b"ftypheim",
    b"ftypheis",
    b"ftyphevc",
    b"ftypmif1",
    b"ftypmsf1",
)


class UploadRejected(UserFacing, ValueError):
    """A file Tuppence won't read. The message is shown to the person as is."""


@dataclass(frozen=True)
class Sniffed:
    kind: FileKind
    ext: str  # used for the stored file name


def sniff(head: bytes) -> Sniffed:
    """`head` is the first 64 KiB of the file."""
    if not head:
        raise UploadRejected("This file is empty.")
    if head.startswith(b"%PDF-"):
        return Sniffed("pdf", "pdf")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Sniffed("image", "png")
    if head.startswith(b"\xff\xd8\xff"):
        return Sniffed("image", "jpg")
    if head[4:12] in _HEIC_BRANDS:
        raise UploadRejected(
            "HEIC photos (the iPhone camera format) can't be read yet. "
            "Take a screenshot instead, or "
            "set Settings › Camera › Formats to Most Compatible, or export the photo as JPEG."
        )
    if head.startswith(b"PK\x03\x04"):
        return Sniffed("xlsx", "xlsx")  # confirmed as a workbook by check_zip()
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise UploadRejected(
            "Old Excel .xls files can't be read. "
            "Open it and save it as .xlsx or CSV, then upload that."
        )
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UploadRejected(
            "This doesn't look like a statement file. "
            "Upload a CSV, OFX, QIF, XLSX, PDF or a screenshot."
        )
    text = decode_text(head).lstrip()
    upper = text[:4096].upper()
    if upper.startswith("OFXHEADER") or "<OFX>" in upper:
        return Sniffed("ofx", "ofx")
    if upper.startswith("!TYPE:") or upper.startswith("!OPTION:") or upper.startswith("!ACCOUNT"):
        return Sniffed("qif", "qif")
    if upper.startswith("<?XML") or upper.startswith("<DOCUMENT"):
        if "CAMT.053" in upper:
            return Sniffed("camt053", "xml")
        raise UploadRejected("This XML file isn't a CAMT.053 bank statement.")
    lines = [ln for ln in text.split("\n")[:40] if ln.strip()][:20]
    if any(sum(1 for ln in lines if ln.count(d) >= 2) >= 2 for d in (",", ";", "\t", "|")):
        return Sniffed("csv", "csv")  # a preamble line or two may have fewer separators
    return Sniffed("text", "txt")


def check_zip(
    path: Path, *, max_entries: int = 2000, max_total_mb: int = 100, max_ratio: int = 200
) -> None:
    """Refuse zip bombs and zips that aren't Excel workbooks, before anything unpacks them."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile:
        raise UploadRejected("This spreadsheet is damaged and can't be opened.") from None
    names = {i.filename for i in infos}
    if "xl/workbook.xml" not in names:
        raise UploadRejected("ZIP files can't be read. Upload the statement files themselves.")
    total = sum(i.file_size for i in infos)
    packed = max(1, sum(i.compress_size for i in infos))
    if len(infos) > max_entries or total > max_total_mb * 1024 * 1024 or total / packed > max_ratio:
        raise UploadRejected(
            "This spreadsheet unpacks to far more data than a bank statement would, "
            "so it wasn't opened."
        )
