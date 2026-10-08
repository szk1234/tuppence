"""Untrusted text is prepared in the app process, so every pattern that reads it must take time
in proportion to the line (I5): no line, however it is built, can hold a worker for long.

Every regular expression in the ingest modules is run over 1 MB lines built to make a
backtracking pattern slow (long runs of digits, commas, spaces, mask characters, labels and
figures). Lines longer than the cap never reach the line classifiers at all; they are left out
and reported. Text preparation also stops at the extraction deadline."""

from __future__ import annotations

import re
import time
from types import ModuleType

import pytest

from tuppence.ingest import (
    balances,
    check,
    identify,
    mapping,
    parse,
    reader,
    registry,
    sensitive,
    textnum,
    textprep,
)
from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.importers import csv_layout, ofx, qif
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack
from tuppence.ingest.sandbox import SandboxTimeout

MB = 1_000_000
BOUND = 3.0  # seconds for one pattern over one 1 MB line: generous, linear takes milliseconds

MODULES: list[ModuleType] = [
    textprep,
    sensitive,
    identify,
    balances,
    check,
    textnum,
    mapping,
    parse,
    reader,
    registry,
    ofx,
    qif,
    csv_layout,
]


def _repeat(unit: str, size: int) -> str:
    return (unit * (size // len(unit) + 1))[:size]


# (head, repeated unit, tail): each run is one that makes some backtracking pattern slow.
SHAPES = {
    "digits": ("", "1", ""),
    "thousands": ("1", ",000", ""),
    "digit-commas": ("", "1,", ""),
    "spaces": ("", " ", "x"),
    "tabs": ("", "\t", "x"),
    "spaced digits": ("", "1 ", ""),
    "dashed digits": ("", "12-", ""),
    "dotted digits": ("", "1.", ""),
    "pounds": ("", "£1 ", ""),
    "letters and digits": ("", "AB12", ""),
    "iban groups": ("GB29 ", "1234 ", ""),
    "bullets": ("", "•", "1234"),
    "dots": ("", ".", "1234"),
    "stars": ("", "*", ""),
    "x mask": ("", "x", ""),
    "x mask groups": ("", "xxxx-", ""),
    "balance words": ("", "Balance ", ""),
    "balance figures": ("", "balance £1,234.56 ", ""),
    "dates": ("", "01/01/2026 ", ""),
    "named dates": ("", "1 Jan ", ""),
    "titles": ("", "Mr ", ""),
    "street-like": ("", "1 a ", ""),
    "slashes": ("", "1/", ""),
    "account words": ("", "acc ", "1"),
    "a/c numbers": ("", "a/c 1 ", ""),
    "ending gap": ("ending", " ", "x"),
    "account gap": ("account", " ", "x"),
    "bracket gap": ("(", " ", "x"),
    "minus": ("", "-", ""),
    "zeros": ("", "0", ""),
    "pound signs": ("", "£", ""),
    "credit markers": ("", "1.00 CR ", ""),
    "capitals": ("", "A", ""),
    "words": ("", "Aa ", ""),
    "decimals": ("", "1.00.", ""),
    "sort-code groups": ("", "12 34 56 ", ""),
    "colons": ("", ": ", ""),
    "ordinals": ("", "1st ", ""),
    "grouped figures": ("", "1,234 ", ""),
    "signs": ("", "+ ", ""),
    "currency codes": ("", "GBP ", ""),
    "column words": ("", "paid out ", ""),
    "figure pairs": ("", "1.00 1.00 ", ""),
    "dashed dates": ("", "1-1-2026-", ""),
    "bars": ("", "| ", ""),
    "unicode minus": ("", "−1", ""),
    "open brackets": ("", "(1", ""),
    "card groups": ("", "1234 ", ""),
    "flats": ("", "Flat 1 ", ""),
    "dated label gap": ("Balance", " on", " £1.00"),
    "due words": ("", "Payment due ", ""),
    "en dashes": ("", "–", ""),
    "nbsp thousands": ("1", " 000", ""),
    "space thousands": ("1", " 000", ""),
    "placeholders": ("", "[ID1] ", ""),
}


def adversarial(shape: str, size: int = MB) -> str:
    head, unit, tail = SHAPES[shape]
    return head + _repeat(unit, size) + tail


def patterns() -> list[tuple[str, re.Pattern[str]]]:
    found: list[tuple[str, re.Pattern[str]]] = []
    for module in MODULES:
        for name, value in vars(module).items():
            if isinstance(value, re.Pattern):
                found.append((f"{module.__name__.rsplit('.', 1)[-1]}.{name}", value))
    return found


def _scan(name: str, pattern: re.Pattern[str], size: int, bound: float) -> None:
    for shape in SHAPES:
        line = adversarial(shape, size)
        started = time.perf_counter()
        for _ in pattern.finditer(line):
            pass
        took = time.perf_counter() - started
        assert took < bound, f"{name} took {took:.1f}s on {shape} ({size} characters)"


@pytest.mark.parametrize(("name", "pattern"), patterns(), ids=[n for n, _ in patterns()])
def test_every_ingest_pattern_is_linear_on_long_lines(name, pattern):
    """64 KB lines: a pattern that is quadratic on any shape takes seconds here, a linear one a
    few milliseconds. (The 1 MB run below is in the slow suite.)"""
    _scan(name, pattern, 64_000, 1.0)


@pytest.mark.slow
@pytest.mark.parametrize(("name", "pattern"), patterns(), ids=[n for n, _ in patterns()])
def test_every_ingest_pattern_is_linear_on_1mb_lines(name, pattern):
    _scan(name, pattern, MB, BOUND)


def test_the_review_probes_are_fast():
    """The two reported: comma groups for the money token, a long mask run for identify."""
    started = time.perf_counter()
    textprep.has_amount("1" + ",000" * (MB // 4))
    pack = load_bank_pack()
    doc = textprep.text_document("Statement\n" + "x" * MB + "\n", sha256="x")
    identify.identify(doc, pack=pack, registry=LayoutRegistry(pack), key=b"k")
    assert time.perf_counter() - started < BOUND


def _time(fn, *args, **kwargs):
    started = time.perf_counter()
    fn(*args, **kwargs)
    return time.perf_counter() - started


def _line_functions(shape: str, cell_size: int, bound: float) -> None:
    line = adversarial(shape, textprep.MAX_LINE_CHARS)
    cell = adversarial(shape, cell_size)
    for fn, value in (
        (sensitive.classify, line),
        (textprep.is_heading, line),
        (textprep.has_amount, line),
        (identify.header_facts, line),
        (mapping.cell_token, cell),
        (sensitive.classify, cell),
        (textnum.parse_money, cell),
        (textnum.parse_date, cell),
    ):
        assert _time(fn, value) < bound, (fn.__name__, shape)


@pytest.mark.parametrize("shape", list(SHAPES))
def test_line_level_functions_are_fast_on_long_lines(shape):
    """The functions that read one line or one cell: lines at the longest one may be read,
    cells of 64 KB (the 1 MB a CSV cell may be is in the slow suite)."""
    _line_functions(shape, 64_000, 1.0)


@pytest.mark.slow
@pytest.mark.parametrize("shape", list(SHAPES))
def test_line_level_functions_are_fast_on_1mb_cells(shape):
    _line_functions(shape, MB, BOUND)


@pytest.mark.parametrize("shape", ["thousands", "x mask", "spaces", "balance figures", "dates"])
def test_documents_with_megabyte_lines_are_prepared_quickly(shape):
    line = adversarial(shape)
    started = time.perf_counter()
    doc = textprep.text_document(f"Statement\n{line}\n02/10/2026 Shop 4.00\n", sha256="x")
    textprep.pages_document([["Statement", line, "02 Oct 2026 Shop 4.00"]], sha256="x", kind="pdf")
    textprep.pages_document([["5 Oct Shop -£3.40", line]], sha256="x", kind="image")
    pack = load_bank_pack()
    identify.identify(doc, pack=pack, registry=LayoutRegistry(pack), key=b"k")
    balances.local_balances(doc, perspective="household")
    assert time.perf_counter() - started < 2 * BOUND


def test_a_line_over_the_cap_is_left_out_and_reported():
    long = "03/10/2026 Shop " + "x" * textprep.MAX_LINE_CHARS + " 4.00"
    doc = textprep.text_document(
        f"Date Description Amount\n02/10/2026 Cafe 3.00\n{long}\n04/10/2026 Bus 2.00\n",
        sha256="x",
    )
    by_ref = doc.by_ref()
    assert "L3" not in doc.data_refs and "L3" in doc.preamble_refs
    assert doc.too_long_refs == ["L3"] and "L3" in doc.held_amount_refs
    assert len(by_ref["L3"].text) <= textprep.MAX_LINE_CHARS + 1  # kept short
    assert parse.too_long_message(doc) is not None
    assert parse.held_back_message(doc) is None  # it isn't held back for account details


def test_a_long_line_on_a_page_or_screenshot_is_left_out_and_reported():
    long = "x" * (textprep.MAX_LINE_CHARS + 1)
    pdf = textprep.pages_document(
        [["Date Description Amount", "02 Oct 2026 Shop 4.00", long]], sha256="x", kind="pdf"
    )
    shot = textprep.pages_document([["5 Oct Shop -£3.40", long]], sha256="x", kind="image")
    for doc in (pdf, shot):
        assert doc.too_long_refs == [doc.lines[-1].ref]
        assert doc.lines[-1].ref not in doc.data_refs


def test_text_preparation_stops_at_the_deadline():
    calls = []

    def deadline() -> None:
        calls.append(1)
        if len(calls) > 2:
            raise SandboxTimeout("Reading this file took too long, so it was stopped.")

    text = "Date Description Amount\n" + "02/10/2026 Shop 4.00\n" * 50_000
    with pytest.raises(SandboxTimeout):
        textprep.text_document(text, sha256="x", deadline=deadline)
    calls.clear()
    with pytest.raises(SandboxTimeout):
        textprep.csv_document(text.replace(" ", ",").encode(), sha256="x", deadline=deadline)


def test_extracting_a_text_file_uses_the_overall_deadline(tmp_path, monkeypatch):
    path = tmp_path / "big.txt"
    path.write_text("Date Description Amount\n" + "02/10/2026 Shop 4.00\n" * 50_000)
    now = [0.0]

    def clock() -> float:
        now[0] += 1.0  # every look at the clock is a second later
        return now[0]

    monkeypatch.setattr("tuppence.ingest.extract.time.monotonic", clock)
    with pytest.raises(SandboxTimeout, match="took longer than"):
        extract_document(path, "text", sha256="x", limits=ExtractLimits(timeout_s=30))
