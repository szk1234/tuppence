import pytest

from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.sandbox import SandboxFailed

LIMITS = ExtractLimits(max_pages=50, timeout_s=180, memory_mb=2048)


def test_text_pdf_rows_come_from_word_positions(fixtures):
    doc = extract_document(fixtures / "pdf" / "card-text.pdf", "pdf", sha256="x", limits=LIMITS)
    texts = [ln.text for ln in doc.lines]
    assert "29 Sep 2026   Greenbasket Stores   42.18" in texts
    assert "02 Oct 2026   Payment received - thank you   150.00 CR" in texts
    assert doc.pages == 2 and doc.ocr_pages == [] and doc.warnings == []
    preamble = {doc.by_ref()[r].text for r in doc.preamble_refs}
    assert {"Alex Example", "1 Example Road", "EX1 2MP", "Previous balance   £842.16"} <= preamble
    assert doc.by_ref()[doc.data_refs[0]].text == "Date   Description   Amount"


def test_scanned_pdf_is_read_on_this_device(fixtures):
    doc = extract_document(fixtures / "pdf" / "card-scanned.pdf", "pdf", sha256="x", limits=LIMITS)
    assert doc.ocr_pages == [1, 2] and doc.ocr_confidence > 0.9
    text = "\n".join(ln.text for ln in doc.lines)
    for amount in ("42.18", "150.00 CR", "61.30", "6.15 CR", "55.12", "4.80"):
        assert amount in text
    assert "Barclaycard is a trading name of Barclays Bank UK PLC." in text


def test_screenshot_is_read_on_this_device(fixtures):
    doc = extract_document(
        fixtures / "image" / "app-screenshot.png", "image", sha256="x", limits=LIMITS
    )
    texts = [ln.text for ln in doc.lines]
    assert (
        "Mon 5 Oct   Little Cafe   -£3.40" in texts
        and "Wed 7 Oct   Acme Payroll Ltd   +£250.00" in texts
    )
    assert doc.preamble_refs == [] and doc.ocr_pages == [1]


def test_page_limit_is_reported(fixtures):
    doc = extract_document(
        fixtures / "pdf" / "card-text.pdf",
        "pdf",
        sha256="x",
        limits=ExtractLimits(max_pages=1, timeout_s=60, memory_mb=2048),
    )
    assert doc.warnings == ["Only the first 1 of 2 pages were read."]


def test_damaged_pdf_fails_cleanly(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\nthis is not really a pdf")
    with pytest.raises(SandboxFailed, match="couldn't be read"):
        extract_document(bad, "pdf", sha256="x", limits=LIMITS)


def test_text_formats_are_read_in_process(fixtures):
    assert (
        extract_document(fixtures / "ofx" / "current.ofx", "ofx", sha256="x", limits=LIMITS).meta[
            "bankid"
        ]
        == "400000"
    )
    assert (
        extract_document(fixtures / "qif" / "bank.qif", "qif", sha256="x", limits=LIMITS).kind
        == "qif"
    )
    assert (
        extract_document(
            fixtures / "camt" / "statement.xml", "camt053", sha256="x", limits=LIMITS
        ).meta["bic"]
        == "NAIAGB21"
    )
    assert extract_document(
        fixtures / "csv" / "monzo.csv", "csv", sha256="x", limits=LIMITS
    ).header_refs == ["L1"]


class FakeVision:
    def __init__(self):
        self.seen = []

    def transcribe(self, image, media_type):
        self.seen.append((media_type, image[:8]))
        return ["Mon 5 Oct   Little Cafe   -£3.40"]


def test_vision_model_can_replace_ocr(fixtures):
    vision = FakeVision()
    doc = extract_document(
        fixtures / "pdf" / "card-scanned.pdf", "pdf", sha256="x", limits=LIMITS, vision=vision
    )
    assert [m for m, _ in vision.seen] == ["image/png", "image/png"] and vision.seen[0][
        1
    ] == b"\x89PNG\r\n\x1a\n"
    assert doc.ocr_pages == [1, 2] and "read by your AI vision model" in doc.warnings[0]
    shot = extract_document(
        fixtures / "image" / "app-screenshot.png", "image", sha256="x", limits=LIMITS, vision=vision
    )
    assert [ln.text for ln in shot.lines] == ["Mon 5 Oct   Little Cafe   -£3.40"]
