"""Resource caps: huge pages, page floods, word floods, deadlines, image metadata."""

import io
import time

import pytest
from PIL import Image

from ingest import hostile_pdfs
from tuppence.ingest import extract as extract_module
from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.imaging import MAX_PIXELS, MAX_VISION_SIDE, render_scale
from tuppence.ingest.sandbox import SandboxFailed, SandboxTimeout

LIMITS = ExtractLimits(max_pages=50, timeout_s=120, memory_mb=2048)


class CaptureVision:
    def __init__(self):
        self.sent = []

    def transcribe(self, image, media_type):
        self.sent.append((media_type, image))
        return ["Mon 5 Oct   Little Cafe   -£3.40"]


def test_render_scale_keeps_every_page_within_the_pixel_cap():
    for side in (595, 842, 3600, 14400):
        scale = render_scale(side, side, 200)
        assert side * scale * side * scale <= MAX_PIXELS
    assert render_scale(595, 842, 200) == pytest.approx(200 / 72)  # normal pages unchanged


def test_a_page_with_a_huge_media_box_is_rendered_within_the_cap(tmp_path):
    pdf = hostile_pdfs.big_page(tmp_path / "big.pdf", 3600)
    started = time.monotonic()
    doc = extract_document(pdf, "pdf", sha256="x", limits=LIMITS)
    assert time.monotonic() - started < 60
    assert doc.warnings == ["No text was found in this PDF."]
    vision = CaptureVision()
    extract_document(pdf, "pdf", sha256="x", limits=LIMITS, vision=vision)
    media_type, png = vision.sent[0]
    with Image.open(io.BytesIO(png)) as image:
        assert media_type == "image/jpeg" and max(image.size) <= MAX_VISION_SIDE


def test_a_pdf_with_a_huge_page_tree_is_refused_quickly(tmp_path):
    pdf = hostile_pdfs.many_pages(tmp_path / "many.pdf", 150_000)
    started = time.monotonic()
    with pytest.raises(SandboxFailed, match="far more pages than Tuppence reads"):
        extract_document(pdf, "pdf", sha256="x", limits=LIMITS)
    assert time.monotonic() - started < 20


def test_a_word_flood_page_is_refused_quickly(tmp_path):
    pdf = hostile_pdfs.word_flood(tmp_path / "flood.pdf", 100_000)
    started = time.monotonic()
    with pytest.raises(SandboxFailed, match="has too much text to read safely"):
        extract_document(pdf, "pdf", sha256="x", limits=LIMITS)
    assert time.monotonic() - started < 60


def test_a_page_with_too_many_words_is_refused(fixtures, monkeypatch):
    from tuppence.ingest import pdftext
    from tuppence.ingest.refusals import PdfPageTooManyWords

    monkeypatch.setattr(pdftext, "MAX_WORDS_PER_PAGE", 50)
    with pytest.raises(PdfPageTooManyWords):
        pdftext.pdf_pages(str(fixtures / "pdf" / "card-text.pdf"), 50, True)


def test_a_password_protected_pdf_says_so(tmp_path):
    from reportlab.pdfgen import canvas

    locked = tmp_path / "locked.pdf"
    pen = canvas.Canvas(str(locked), encrypt="probe-password")
    pen.drawString(72, 720, "SYNTHETIC TEST STATEMENT - NOT A REAL DOCUMENT")
    pen.save()
    with pytest.raises(
        SandboxFailed,
        match="This PDF is password-protected. Remove the password and upload it again.",
    ):
        extract_document(locked, "pdf", sha256="x", limits=LIMITS)


def test_one_overall_deadline_covers_every_vision_call(fixtures, monkeypatch):
    offset = [0.0]

    class Clock:
        @staticmethod
        def monotonic():
            return time.monotonic() + offset[0]

    monkeypatch.setattr(extract_module, "time", Clock)

    class SlowVision(CaptureVision):
        def transcribe(self, image, media_type):
            offset[0] += 1000  # each model call "takes" far longer than the whole budget
            return super().transcribe(image, media_type)

    vision = SlowVision()
    with pytest.raises(SandboxTimeout, match="longer than 120 seconds"):
        extract_document(
            fixtures / "pdf" / "card-scanned.pdf", "pdf", sha256="x", limits=LIMITS, vision=vision
        )
    assert len(vision.sent) == 1  # the second page was never sent


def test_blank_back_pages_do_not_lower_the_scan_confidence(fixtures, tmp_path):
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(fixtures / "pdf" / "card-text.pdf")[0].render(scale=2).to_pil()
    blank = Image.new("RGB", page.size, "white")
    duplex = tmp_path / "duplex.pdf"
    page.save(duplex, "PDF", resolution=144, save_all=True, append_images=[blank])
    doc = extract_document(duplex, "pdf", sha256="x", limits=LIMITS)
    assert doc.ocr_pages == [1, 2] and doc.ocr_confidence > 0.9
    assert doc.warnings == []


def _jpeg_with_gps(path, size=(400, 200), orientation=None):
    exif = Image.Exif()
    exif[0x010F] = "ProbeCam"
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}  # invented
    if orientation:
        exif[0x0112] = orientation
    Image.new("RGB", size, "white").save(path, "JPEG", exif=exif.tobytes())
    return path


def test_vision_receives_no_photo_metadata(tmp_path):
    jpg = _jpeg_with_gps(tmp_path / "photo.jpg")
    vision = CaptureVision()
    extract_document(jpg, "image", sha256="x", limits=LIMITS, vision=vision)
    media_type, sent = vision.sent[0]
    assert media_type == "image/jpeg" and b"ProbeCam" not in sent and b"Exif" not in sent
    with Image.open(io.BytesIO(sent)) as image:
        assert not image.getexif() and not image.getexif().get_ifd(0x8825)


def test_vision_receives_the_image_upright_and_without_a_colour_profile(tmp_path):
    jpg = _jpeg_with_gps(tmp_path / "turned.jpg", orientation=6)  # stored sideways
    vision = CaptureVision()
    extract_document(jpg, "image", sha256="x", limits=LIMITS, vision=vision)
    with Image.open(io.BytesIO(vision.sent[0][1])) as image:
        assert image.size == (200, 400)
    png = tmp_path / "profiled.png"
    Image.new("RGB", (50, 50), "white").save(png, "PNG", icc_profile=b"x" * 200)
    extract_document(png, "image", sha256="x", limits=LIMITS, vision=vision)
    with Image.open(io.BytesIO(vision.sent[1][1])) as image:
        assert "icc_profile" not in image.info


def test_vision_images_are_downscaled(tmp_path):
    big = tmp_path / "wide.png"
    Image.new("L", (5000, 3000), 255).save(big, "PNG")
    vision = CaptureVision()
    extract_document(big, "image", sha256="x", limits=LIMITS, vision=vision)
    with Image.open(io.BytesIO(vision.sent[0][1])) as image:
        assert max(image.size) == MAX_VISION_SIDE


def test_images_over_the_pixel_cap_are_refused_on_both_paths(tmp_path):
    huge = tmp_path / "huge.png"
    Image.new("L", (9500, 9500), 255).save(huge, "PNG")
    with pytest.raises(SandboxFailed, match="too large to read"):
        extract_document(huge, "image", sha256="x", limits=LIMITS)
    vision = CaptureVision()
    with pytest.raises(SandboxFailed, match="too large to read"):
        extract_document(huge, "image", sha256="x", limits=LIMITS, vision=vision)
    assert vision.sent == []


def test_photo_orientation_is_applied_before_ocr(fixtures, tmp_path):
    shot = Image.open(fixtures / "image" / "app-screenshot.png").convert("RGB")
    sideways = tmp_path / "sideways.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # displayed after turning 90 degrees clockwise
    shot.rotate(90, expand=True).save(sideways, "JPEG", quality=95, exif=exif.tobytes())
    doc = extract_document(sideways, "image", sha256="x", limits=LIMITS)
    assert any("Little Cafe" in line.text for line in doc.lines)


def test_dense_pages_do_not_pile_up_in_memory(tmp_path):
    """Dense pages: pdfplumber used to keep every page's objects (about 80 MB a page)."""
    pdf = hostile_pdfs.word_flood(tmp_path / "dense.pdf", 20_000, pages=12)
    started = time.monotonic()
    doc = extract_document(
        pdf, "pdf", sha256="x", limits=ExtractLimits(max_pages=50, timeout_s=120, memory_mb=600)
    )
    assert doc.pages == 12 and time.monotonic() - started < 60


def _noisy_scan(path, pages, sigma=6):
    import numpy as np
    import pypdfium2 as pdfium

    source = pdfium.PdfDocument("tests/fixtures/statements/pdf/card-text.pdf")[0]
    page = np.asarray(source.render(scale=150 / 72).to_pil().convert("RGB"), dtype=np.int16)
    rng = np.random.default_rng(1)
    noise = rng.normal(0, sigma, page.shape).astype(np.int16)
    images = [
        Image.fromarray(np.clip(np.roll(noise, 7 * i, axis=0) + page, 0, 255).astype("uint8"))
        for i in range(pages)
    ]
    images[0].save(path, "PDF", resolution=150, save_all=True, append_images=images[1:])
    return path


def test_a_long_noisy_scan_is_read_by_vision_page_by_page(tmp_path):
    pdf = _noisy_scan(tmp_path / "scan30.pdf", 30)
    vision = CaptureVision()
    doc = extract_document(pdf, "pdf", sha256="x", limits=LIMITS, vision=vision)
    assert doc.ocr_pages == list(range(1, 31)) and len(vision.sent) == 30
    assert all(m == "image/jpeg" and len(png) < 2_000_000 for m, png in vision.sent)
    with Image.open(io.BytesIO(vision.sent[0][1])) as image:
        assert max(image.size) <= MAX_VISION_SIDE and not image.getexif()


def _camt_nested(depth):
    return (
        b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">'
        + b"<a>" * depth
        + b"</a>" * depth
        + b"</Document>"
    )


def test_a_deeply_nested_camt_file_is_refused_quickly_and_cheaply(tmp_path):
    import resource

    bomb = tmp_path / "nested.xml"
    bomb.write_bytes(_camt_nested(7_000_000))  # about 42 MB of open tags
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.monotonic()
    with pytest.raises(SandboxFailed, match="nested too deeply"):
        extract_document(bomb, "camt053", sha256="x", limits=LIMITS)
    assert time.monotonic() - started < 30
    grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024  # Linux: KiB
    assert grown_mb < 150


def test_a_camt_file_with_too_many_elements_is_refused(tmp_path):
    flat = tmp_path / "flat.xml"
    flat.write_bytes(
        b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">'
        + b"<a/>" * 1_100_000
        + b"</Document>"
    )
    with pytest.raises(SandboxFailed, match="too large to read safely"):
        extract_document(flat, "camt053", sha256="x", limits=LIMITS)


def test_camt_is_parsed_in_the_sandbox_and_matches_the_in_process_reader(fixtures):
    from tuppence.ingest.extract import read_camt
    from tuppence.ingest.importers import camt

    path = fixtures / "camt" / "statement.xml"
    facts = read_camt(path, LIMITS)
    assert camt.parsed_from_facts(facts) == camt.parse_camt(path.read_bytes())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.pop("iban"),
        lambda f: f.update(extra=1),
        lambda f: f.update(opening_pence="5"),
        lambda f: f.update(period_start="not-a-date"),
        lambda f: f["entries"].append({"ref": "L1"}),
        lambda f: f.update(entries="x"),
    ],
)
def test_camt_facts_are_validated(mutate):
    from tuppence.ingest.results import BadReply, parse_camt_facts

    facts = {
        "iban": "GB00SYNT1",
        "bic": "",
        "currency": "GBP",
        "period_start": "2026-10-01",
        "period_end": "",
        "opening_pence": 100,
        "closing_pence": None,
        "entries": [],
    }
    assert parse_camt_facts(dict(facts, entries=[])).iban == "GB00SYNT1"
    mutate(facts)
    with pytest.raises(BadReply):
        parse_camt_facts(facts)
