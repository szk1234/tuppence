"""Re-review 4 I1 (R-M3-26): the holders' names a statement prints are masked in its rows wherever
the name is printed. Since R-M3-25 (3), a page without a heading row can start its table above the
holder's name and address ("Example Bank plc", two rows, "MR ALEX EXAMPLE", an address, more
rows), and the names were read only above the table start: with no household names set, or only
a first name, "FASTER PAYMENT FROM ALEX EXAMPLE" went out as printed. Names are read from every
holder line and every address block now, before any row is masked.

The guard has its own reading: no word of a printed holder's name ("Alex", "Example") is in
anything sent. A differential against ce1c72a (the commit before R-M3-25) checks that no line
shows more of a name now than it did then."""

from __future__ import annotations

import re

import pytest

from ingest.helpers import old_text_prep
from tuppence.ingest import textprep
from tuppence.ingest.textprep import pages_document, sent_lines, text_document

ROWS_A = ["06/11/2026 GREENBASKET STORES 42.18", "07/11/2026 LITTLE CAFE 3.00"]
NAMED_ROWS = [
    "08/11/2026 FASTER PAYMENT FROM ALEX EXAMPLE 50.00",
    "09/11/2026 TFR TO A EXAMPLE SAVINGS 20.00",
    "10/11/2026 TFR TO EXAMPLE A 20.00",
    "11/11/2026 STANDING ORDER A EXAMPLE 20.00",
    "12/11/2026 PAYPAL *ALEXEXAMPLE 5.00",
]
ADDRESS = ["10 Example Road", "Exampletown", "EX1 1AA"]
# The reviewer's p_printed, p_printed2, p_printed3 and p_printed4 layouts.
LAYOUTS = {
    "R8 titled": ["Example Card plc", "05/11/2026 Interest charged £3.21",
                  "05/11/2026 Payment received £250.00", "MR ALEX EXAMPLE", "Rose Cottage",
                  "Exampletown", *ROWS_A, *NAMED_ROWS],
    "R8 untitled": ["Example Card plc", "05/11/2026 Interest charged £3.21",
                    "05/11/2026 Payment received £250.00", "Alex Example", *ADDRESS, *ROWS_A,
                    *NAMED_ROWS],
    "R8 statement for": ["Example Bank plc", "01/11/2026 Opening fee £3.21",
                         "02/11/2026 Payment received £250.00", "Statement for Alex Example",
                         *ADDRESS, *ROWS_A, *NAMED_ROWS],
    "R8 joint titled": ["Example Bank plc", "02/11/2026 CASH PAID IN AT BRANCH 50.00",
                        "02/11/2026 CREDIT 900.00", "MR ALEX EXAMPLE & MRS PAT EXAMPLE",
                        "Rose Cottage", "Exampletown", *ROWS_A, *NAMED_ROWS,
                        "13/11/2026 STANDING ORDER P EXAMPLE 20.00"],
    "R8 with a heading row later": ["Example Card plc", "05/11/2026 Interest charged £3.21",
                                    "05/11/2026 Payment received £250.00", "Alex Example",
                                    *ADDRESS, "Date Description Amount", *ROWS_A, *NAMED_ROWS],
    "headed": ["Example Bank plc", "Alex Example", *ADDRESS, "Date Description Amount", *ROWS_A,
               *NAMED_ROWS],
    "headingless": ["Example Bank plc", "Alex Example", *ADDRESS, *ROWS_A, *NAMED_ROWS],
    "rows then address": ["Example Bank plc", *ROWS_A, "MR ALEX EXAMPLE", *ADDRESS, *NAMED_ROWS],
    "rows then untitled address": ["Example Bank plc", *ROWS_A, "Alex Example", *ADDRESS,
                                   *NAMED_ROWS],
    "rows, address, own-name continuation": [
        "Example Bank plc", "01/10/2026 GREENBASKET STORES 42.18 957.82",
        "02/10/2026 LITTLE CAFE 3.00 954.82", "Alex Example", *ADDRESS,
        "03/10/2026 TRANSFER TO SAVINGS 50.00 904.82", "ALEX EXAMPLE",
        "04/10/2026 NORTHLINE RAIL 10.00 894.82", *NAMED_ROWS],
    "address on a later page": ["Example Bank plc", *ROWS_A, *NAMED_ROWS[:2], "Alex Example",
                                *ADDRESS, *NAMED_ROWS[2:]],
}  # fmt: skip
_NAME_WORDS = re.compile(r"alex|example|\bpat\b", re.IGNORECASE)
HOUSEHOLDS = {"no names": (), "first name only": ("Alex",)}


def _docs(lines: list[str], names: tuple[str, ...]):
    half = len(lines) // 2
    return {
        "text": text_document("\n".join(lines), sha256="x", names=names),
        "pdf": pages_document([lines], sha256="x", kind="pdf", names=names),
        "two pages": pages_document(
            [lines[:half], lines[half:]], sha256="x", kind="pdf", names=names
        ),  # fmt: skip
    }


def _shown(doc) -> list[str]:
    sent = sent_lines(doc)
    return [sent[r].text for r in doc.data_refs]


@pytest.mark.parametrize("household", list(HOUSEHOLDS))
@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_no_word_of_a_printed_holders_name_is_sent(layout, household):
    for path, doc in _docs(LAYOUTS[layout], HOUSEHOLDS[household]).items():
        leaked = [text for text in _shown(doc) if _NAME_WORDS.search(text)]
        assert leaked == [], (path, leaked)


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_every_row_is_still_sent_or_reported(layout):
    """Masking the name takes nothing else: each row is sent, with its amount, or reported."""
    lines = LAYOUTS[layout]
    for path, doc in _docs(lines, ()).items():
        shown = set(doc.data_refs) | set(doc.held_amount_refs)
        rows = [line for line in doc.lines if line.text in [*ROWS_A, *NAMED_ROWS]]
        assert all(line.ref in shown for line in rows), path
        sent = sent_lines(doc)
        for line in rows:
            if line.ref in doc.data_refs:
                amount = line.text.split()[-1]
                assert sent[line.ref].text.endswith(amount), (path, sent[line.ref].text)


def test_a_screenshot_reads_the_names_its_address_block_prints():
    shot = ["Transactions", "Mon 5 Oct Little Cafe -£3.40", "Alex Example", "10 Example Road",
            "Exampletown EX1 1AA", "Tue 6 Oct Faster payment from Alex Example +£50.00",
            "Wed 7 Oct PayPal *AlexExample -£5.00"]  # fmt: skip
    doc = pages_document([shot], sha256="x", kind="image")
    assert [text for text in _shown(doc) if _NAME_WORDS.search(text)] == []
    assert len(doc.data_refs) == 3


def test_a_place_after_a_row_is_no_holders_name():
    """Over-masking kept small: a name is read from a holder's line (a title, "Statement for")
    or an address block that is an address beyond doubt (a postcode, a house number and street,
    or three lines or more), never from a place or a merchant printed on its own line."""
    lines = ["Example Bank plc", "Date Description Amount", "02/10/2026 COSTA COFFEE 3.00",
             "COSTA COFFEE", "NOTTING HILL", "03/10/2026 COSTA COFFEE NOTTING HILL 3.00",
             "04/10/2026 TFL NOTTING HILL GATE 2.80", "NOTTING HILL GATE",
             "05/10/2026 TFL NOTTING HILL GATE 2.80"]  # fmt: skip
    doc = text_document("\n".join(lines), sha256="x")
    sent = _shown(doc)
    for row in (lines[2], lines[5], lines[6], lines[8]):
        assert row in sent


# --- the differential against ce1c72a: no line shows more of a name than it did -------------

BASE = "ce1c72a"


@pytest.fixture(scope="module")
def before():
    return old_text_prep(BASE)


def _forms(text: str, forms: list[str]) -> list[str]:
    """Each printed form of a name in `text` (word-bounded, any case), as often as it is there."""
    folded = text.casefold()
    return [f for f in forms for _ in re.finditer(rf"(?<![a-z]){re.escape(f)}(?![a-z])", folded)]


def _more_names(module, build, forms: list[str]) -> list[tuple[str, str | None, str]]:
    """Lines sent now that show a form of a name the statement prints (or the household's) more
    often than ce1c72a sent it: a line ce1c72a withheld shows none now."""
    old, new = build(module), build(textprep)
    then = {r: module.sent_lines(old)[r].text for r in old.data_refs}
    now = {r: textprep.sent_lines(new)[r].text for r in new.data_refs}
    out = []
    for ref, text in now.items():
        extra = _forms(text, forms)
        for form in _forms(then.get(ref) or "", forms):
            if form in extra:
                extra.remove(form)
        if extra:
            out.append((new.by_ref()[ref].text, then.get(ref), text))
    return out


ALEX = ["alex", "alexexample", "alex example", "a example", "example a", "example alex"]
PAT = ["pat", "pat example", "p example", "example p", "example pat"]


def _corpus() -> list[tuple[list[list[str]], str, tuple[str, ...], list[str]]]:
    """Every layout here, the table-start pages, and the rule-(e) property test's generated
    statements (whose holders and addresses may follow the first rows), as text, PDF pages and
    screenshots, with and without household names; each with the forms of the names it prints."""
    import random

    from ingest import test_rule_e_accounting as accounting
    from ingest import test_table_start as table

    pages: list[tuple[list[list[str]], str, tuple[str, ...], list[str]]] = []
    for lines in LAYOUTS.values():
        for names in HOUSEHOLDS.values():
            for kind in ("text", "pdf", "image"):
                pages.append(([lines], kind, names, [*ALEX, *PAT]))
    for page in [*table.HEADED.values(), *table.UNHEADED.values(), *table.NO_HEADING.values()]:
        pages += [([page], "pdf", (), ALEX), ([page], "image", (), ALEX)]
    rnd = random.Random(7)
    for _ in range(80):
        lines, names, named = accounting._statement_and_names(rnd)
        forms = [f.casefold() for f in named]
        pages += [([lines], "text", names, forms), ([lines], "pdf", names, forms)]
    return pages


def _build(pages: list[list[str]], kind: str, names: tuple[str, ...]):
    def build(module):
        if kind == "text":
            return module.text_document("\n".join(pages[0]), sha256="x", names=names)
        return module.pages_document(pages, sha256="x", kind=kind, names=names)

    return build


def test_no_line_shows_more_of_a_name_than_at_ce1c72a(before):
    more = []
    for pages, kind, names, forms in _corpus():
        more += _more_names(before, _build(pages, kind, names), forms)
    if more:
        pytest.fail(f"{len(more)} lines show more of a name than at {BASE}, e.g. {more[:5]}")


def test_the_name_guards_can_fail(before, monkeypatch):
    """The control: with names read only above the table start again, as at a11567f, the
    reviewer's layouts send the holder's name, and both guards say so."""
    monkeypatch.setattr(textprep, "_printed_holder_lines", lambda texts, addresses: set())
    lines = LAYOUTS["rows then address"]
    assert any(_NAME_WORDS.search(text) for text in _shown(_docs(lines, ())["text"]))
    assert _more_names(before, _build([lines], "text", ()), ALEX)
