"""The control-regression guard for this round (coordinator, after 1116112): nothing text prep
withheld or masked at 8bc2649 is sent now with more of it showing, unless on purpose.

Every line of the parity, balance, summary, long-number, rule-(e) and table-start corpora, the
reviewer's and the coordinator's probes, and the fixture statements goes through text prep as it
was at 8bc2649 and as it is now. What is sent now may show more than it did only:

- the line's own date or dates at its start (R4: "03.10.26 04.10.26 TESCO", read by the test
  oracle's own rule), or
- a line withheld at 8bc2649 that is sent now with no account or identity detail and no long
  number in it (R1: rows worded in balance words, "02/10/2026 INTEREST ON CREDIT BALANCE";
  numbers masked by the structural join rule, "TO [hidden-a]").

Anything else that shows more now fails."""

from __future__ import annotations

import collections
import importlib.util
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from ingest.digit_runs import fold, own_dates, survivors
from tuppence.ingest import sensitive, textprep

BASE = "8bc2649"
_SOURCE = Path(__file__).resolve().parents[2]
NAMES = ["Alex Example", "Pat Example"]
HEAD = ["Example Bank", "Statement 01/03/2026 to 31/03/2026",
        "Date Description Money out Money in Balance"]  # fmt: skip
ROWS = ["02/03/2026 TESCO STORES 12.50 1,000.00", "03/03/2026 SALARY 2,000.00 3,000.00"]
SHOT = ["Mon 2 Mar TESCO STORES -£12.50", "Tue 3 Mar SALARY +£2,000.00"]
_PLACEHOLDER = re.compile(r"\[hidden-[a-z]+\]")
_TOKEN = re.compile(r"\d+|[^\W\d_]+")

# The reviewer's probes (p_overmask, p_adv_mask, p_twodates, p_slash) and the coordinator's (p3,
# p4), line for line.
PROBES = [
    "03/10/2026 BIG PURCHASE 1,234,567.89",
    "03/10/2026 BIG PURCHASE -1,234,567.89",
    "03/10/2026 HOUSE 1234567.89",
    "03/10/2026 SALARY 1,234.56 CR",
    "03/10/2026 SHOP 1,234.56DR",
    "03/10/2026 SHOP (1,234.56)",
    "03/10/2026 SHOP 1,234.56-",
    "03/10/2026 EURO SHOP £1.234,56",
    "03/10/2026 EURO SHOP €1.234,56",
    "03/10/2026 SHOP £123456",
    "03/10/2026 SHOP £1,234,567",
    "03/10/2026 SHOP GBP 123456.78",
    "03/10/2026 FX 123.45 EUR 106.78",
    "03/10/2026 SHOP 999,999.99 1,000,000.00",
    "03/10/2026 SHOP 12.50 123,456.78",
    "03/10/2026 SHOP 12.50 123456.78",
    "03/10/2026 SHOP 12.50 1234567.00 CR",
    "03/10/2026 SHOP 100000.00",
    "03/10/2026 SHOP 1 234 567.89",
    "03.10.2026 SHOP 12.50",
    "03.10.26 SHOP 12.50",
    "03 10 26 SHOP 12.50",
    "03 10 2026 SHOP 12.50",
    "3.10.26 SHOP 12.50",
    "2026-10-03 SHOP 12.50",
    "03-10-2026 SHOP 12.50",
    "03-10-26 SHOP 12.50",
    "Mon 03.10.26 SHOP 12.50",
    "03Oct26 SHOP 12.50",
    "03/10/26 SHOP 12.50",
    "031026 SHOP 12.50",
    "20261003 SHOP 12.50",
    "03.10.26 03.10.26 SHOP 12.50",
    "03/10/2026 03/10/2026 SHOP 12.50",
    "03 Oct 03 Oct SHOP 12.50",
    "03.10.26 SHOP 12.50 1,000.00",
    "03 10 26 SHOP 1234 12.50",
    "03.10.26 123456 SHOP 12.50",
    "03/10/2026 CARD PAYMENT TO SHOP ON 01.10.26 12.50",
    "03/10/2026 CARD PAYMENT TO SHOP ON 01 10 26 12.50",
    "03/10/2026 SHOP ON 01-10-26 12.50",
    "03/10/2026 SHOP ON 01/10/26 12.50",
    "03/10/2026 SHOP ON 01.10.2026 12.50",
    "03/10/2026 SHOP 01OCT26 12.50",
    "03/10/2026 SHOP VALUE DATE 2026-10-01 12.50",
    "03/10/2026 7-ELEVEN 3.20",
    "03/10/2026 3 MOBILE 15.00",
    "03/10/2026 B&Q 0123 45.00",
    "03/10/2026 PAYPAL *EBAY 12345 9.99",
    "03/10/2026 TESCO STORES 2345 12.50",
    "03/10/2026 SAINSBURYS S/MKTS 0789 12.50",
    "03/10/2026 O2 UK 25.00",
    "03/10/2026 AMAZON.CO.UK*AB1CD2EF3 9.99",
    "03/10/2026 TFL TRAVEL CH 2.80",
    "03/10/2026 SHELL 1234567 45.00",
    "03/10/2026 PRET A MANGER 0016 4.50",
    "03/10/2026 COSTA 43021 3.10",
    "03/10/2026 NETFLIX.COM 866-579-7172 10.99",
    "03/10/2026 SPOTIFY P1A2B3C4D5 11.99",
    "03/10/2026 UBER *TRIP HELP.UBER.COM 12.40",
    "03/10/2026 WH SMITH 1001 3.00",
    "03/10/2026 BOOTS 1234 5.00",
    "03/10/2026 ZARA 6.00",
    "03/10/2026 SO 3.00",
    "03/10/2026 IKEA LTD 17.00",
    "03/10/2026 MCDONALDS 1234567 5.00",
    "03/10/2026 DVLA VEHICLE TAX 20.00",
    "03/10/2026 COUNCIL TAX 123.00",
    "03/10/2026 HMRC 123456789 50.00",
    "03/10/2026 ASDA 4567 1.00",
    "03/10/2026 365 RETAIL 2.00",
    "03/10/2026 99P STORES 0.99",
    "03/10/2026 1ST CHOICE 5.00",
    "03/10/2026 BOOTS 1O23 5.00",
    "03/10/2026 TO BOSS 5.00",
    "03/10/2026 SALE 50% OFF 5.00",
    "03/10/2026 PARKING 2HRS 3.00",
    "03/10/2026 FLIGHT BA0123 120.00",
    "03/10/2026 TICKET 2x 10.00 20.00",
    "03/10/2026 BILL 01/2026 30.00",
    "03/10/2026 RENT OCT 2026 650.00",
    "03/10/2026 INV 2026/10/03 30.00",
    "03/10/2026 TO 20-11 33.87654321 -250.00",
    "03/10/2026 TO 20 11-33/8765 4321 -250.00",
    "03/10/2026 TO 2011-33 8765.4321 -250.00",
    "03/10/2026 TO 20-11-33 8765-43-21 -250.00",
    "03/10/2026 TO 20-11-33 8765 - 4321 -250.00",
    "03/10/2026 TO 20-11-33 8765 / 4321 -250.00",
    "03/10/2026 TO 20-11-33 8765,4321 -250.00",
    "03/10/2026 TO 20-11-33 8765+4321 -250.00",
    "03/10/2026 TO 20-11-33 8765#4321 -250.00",
    "03/10/2026 TO 20-11-33 8765\\4321 -250.00",
    "03/10/2026 TO 20-11-33 8765*4321 -250.00",
    "03/10/2026 TO 20-11-33 8765:4321 -250.00",
    "03/10/2026 ACC NO 8765,4321 -250.00",
    "03/10/2026 TO 20 - 11 - 33 87654321 -250.00",
    "03/10/2026 TO 20/11/33 87654321 -250.00",
    "03/10/2026 TO 20.11.2033 87654321 -250.00",
    "03/10/2026 TO 87654321 20-11-33 -250.00",
    "03/10/2026 VISA 4929 1234 5678 9012 -12.00",
    "03/10/2026 VISA 4929-1234 5678.9012 -12.00",
    "03/10/2026 AMEX 3782 822463 10005 -12.00",
    "03/10/2026 RENT O7654321 -250.00",
    "03/10/2026 SMITHS876S4321 -250.00",
    "03/10/2026 TO 2O-ll-33 B765 432l -250.00",
    "03/10/2026 BOSS 876S432I -250.00",
    "03/10/2026 SO 87654321 -250.00",
    "03/10/2026 SO87654321 -250.00",
    "03/10/2026 ACCTB76S432l -250.00",
    "03/10/2026 CARD ENDING 42S2 -12.00",
    "03/10/2026 CARD ENDING 4Z42 -12.00",
    "03/10/2026 VISA ****42A2 -12.00",
    "03/10/2026 TO 20-11-33 8765432110.00 1,000.00",
    "03/10/2026 TO 20-11-33 87654321.00 -250.00",
    "03/10/2026 TO 20-11-33 £87654321 -250.00",
    "03/10/2026 TO 20-11-33 87654321-250.00",
    "03/10/2026 TO 87654321/1 -250.00",
    "03/10/2026 TO ACCOUNT 8765 4321 -250.00",
    "03/10/2026 CARD 4929 **** **** 9012 -12.00",
    "03/10/2026 CARD 4929XXXXXXXX9012 -12.00",
    "03/10/2026 CARD 492912******9012 -12.00",
    "03/10/2026 CARD ...9012 -12.00",
    "03/10/2026 CARD #9012 -12.00",
    "03/10/2026 CARD NO 9012 -12.00",
    "03/10/2026 CARD ENDING IN: 9012 -12.00",
    "03/10/2026 ENDING-9012 -12.00",
    "03/10/2026 CARD (9012) -12.00",
    "03/10/2026 ALEX+EXAMPLE -12.00",
    "03/10/2026 ALEX*EXAMPLE -12.00",
    "03/10/2026 ALEX&EXAMPLE -12.00",
    "03/10/2026 ALEX.EXAMPLE@MAIL.COM -12.00",
    "ACC 8765 - 4321 10.00",
    "ACC 8765 / 4321 10.00",
    "ACC NO 8765,4321 10.00",
    "ACC 8765+4321 10.00",
    "ACC 8765#4321 10.00",
    "ACC 8765\\4321 10.00",
    "ACC 8765:4321 10.00",
    "ACC 8765 _ 4321 10.00",
    "ACC 8765~4321 10.00",
    "ACC 8765 | 4321 10.00",
    "ACC 8765 · 4321 10.00",
    "ACC 8765 — 4321 10.00",
    "ACC 8765 ; 4321 10.00",
    "ENDING IN: 9012 5.00",
    "ENDING-9012 5.00",
    "CARD #9012 5.00",
    "CARD NO. ...9012 5.00",
    "CARD ENDING IN 9 0 1 2 5.00",
    "20-11-33 87654321 RENT 10.00",
    "20/11/33 87654321 RENT 10.00",
    "20.11.33 87654321 RENT 10.00",
    "02/10/26 20-11-33 87654321 10.00",
    "02/10/26 03/10/26 20-11-33 87654321 10.00",
    "02/10/26 03/10/26 04/10/26 87654321 10.00",
    "20-11-33-87654321 10.00",
    "201133-87654321 10.00",
    "02.10.26 03.10.26 TESCO 10.00",
    "031026 TESCO 10.00",
    "2026-10-03 TESCO 10.00",
    "TO 20 11 33 / 8765 4321 10.00",
    "TO 2011 3387 6543 21 10.00",
    "SORT 20-11-33 ACC 8765 4321 10.00",
    "SORT CODE 20-11-33 10.00",
    "SC 201133 10.00",
    "ACCOUNT 87654321 10.00",
    "J SMITH 10.00",
    "MR JOHN SMITH 10.00",
    "FLAT 3 EXAMPLE HOUSE 10.00",
    "AB12 3CD 10.00",
    "EX1 1AA DELIVERY 10.00",
    "IBAN GB29 NWBK 6016 1331 9268 19",
    "CARD 4242424242424242",
    "NI QQ 12 34 56 C 10.00",
    "ROLL NUMBER ABC/123 10.00",
    "BUILDING SOC ROLL 1234-567 10.00",
    "MEMBER NO 12345 10.00",
    "PAYEE REF 12-34-56 10.00",
    "TO 12-34-56 10.00",
    "FROM 12 34 56 10.00",
    "12-34-56 10.00",
    "TEL 07700900123 10.00",
    "EMAIL alex@example.com 10.00",
    "alex.example@example.co.uk 10.00",
    "DOB 01/02/1980 10.00",
    "PASSPORT 123456789 10.00",
    "POSTCODE SW1A 1AA 10.00",
    "03.10.26 04.10.26 TESCO STORES 12.50",
    "03 10 26 04 10 26 TESCO STORES 12.50",
    "03/10/26 04/10/26 TESCO STORES 12.50",
    "03.10.2026 04.10.2026 TESCO STORES 12.50",
    "03 Oct 04 Oct TESCO STORES 12.50",
    "03.10.26 TESCO 04.10.26 12.50",
    "03/10/2026 PAYMENT 1234/5678 -250.00",
    "03/10/2026 MANDATE 123/456/789 -12.00",
    "FPO J SMITH",
    "20 11 33 87654321 RENT",
    "20.11.33 87654321 RENT",
]


def _old(path: str) -> str | None:
    try:
        out = subprocess.run(["git", "show", f"{BASE}:{path}"], cwd=_SOURCE, capture_output=True,
                             text=True, timeout=60, check=True)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


@pytest.fixture(scope="module")
def old_textprep() -> types.ModuleType:
    sensitive_source = _old("src/tuppence/ingest/sensitive.py")
    textprep_source = _old("src/tuppence/ingest/textprep.py")
    if sensitive_source is None or textprep_source is None:
        pytest.skip(f"{BASE} isn't in this checkout's history")
    old_sensitive = types.ModuleType("sensitive_8bc2649")
    exec(compile(sensitive_source, "sensitive_8bc2649.py", "exec"), old_sensitive.__dict__)  # noqa: S102 - the repo's own earlier code
    sys.modules["sensitive_8bc2649"] = old_sensitive
    source = textprep_source.replace(
        "from tuppence.ingest import sensitive", "import sensitive_8bc2649 as sensitive"
    )
    spec = importlib.util.spec_from_loader("textprep_8bc2649", loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["textprep_8bc2649"] = module  # its dataclasses look their module up
    exec(compile(source, "textprep_8bc2649.py", "exec"), module.__dict__)  # noqa: S102 - as above
    return module


def _tokens(text: str) -> collections.Counter[str]:
    return collections.Counter(t.casefold() for t in _TOKEN.findall(_PLACEHOLDER.sub(" ", text)))


def _shown(module: types.ModuleType, doc) -> dict[str, str]:
    sent = module.sent_lines(doc)
    return {ref: sent[ref].text for ref in doc.data_refs}


def _on_purpose(
    printed: str, before: str | None, now: str, more: collections.Counter[str], strict: bool
) -> bool:
    folded = fold(printed)
    own = own_dates(folded)
    if own is not None:
        dates = _tokens(folded[own[0] : own[1]])
        if not (more - dates):
            return True  # only the row's own date or dates
    if strict:
        return False
    detail = sensitive.classify(now, names=NAMES) - sensitive.LABEL_CLASSES
    return before is None and not detail and not survivors(printed, now)


def _regressions(old_textprep, build, *, strict: bool = False) -> list[tuple[str, str | None, str]]:
    """(printed, sent at BASE or None, sent now) for every line that shows more now. `strict`:
    only the row's own dates may show more (address lines are never sent on purpose)."""
    old, new = build(old_textprep), build(textprep)
    before, now = _shown(old_textprep, old), _shown(textprep, new)
    printed = new.by_ref()
    out = []
    for ref, text in now.items():
        more = _tokens(text) - _tokens(before.get(ref) or "")
        if more and not _on_purpose(printed[ref].text, before.get(ref), text, more, strict):
            out.append((printed[ref].text, before.get(ref), text))
    return out


def _corpus() -> list[str]:
    from ingest import test_balance_lines as balances
    from ingest import test_long_numbers as numbers
    from ingest import test_rule_e_descriptions as rule_e
    from ingest import test_sensitive_parity as parity
    from ingest import test_textprep as prep
    from ingest import test_withheld_reported as reported

    lines = [
        *parity.EVERY, *parity.PLAIN, *parity.REGRESSION, *parity.WITH_DETAILS,
        *parity.SPAN_CASES, *balances.BALANCES, *balances.UNSURE, *balances.ROWS_STILL_SENT,
        *prep.MIXED_SUMMARIES, *(line for line, _ in prep.OUTCOMES), *reported.HELD_TEXT,
        *reported.HELD_SHOT, *reported.SENT, *reported.BALANCE_WORDS, *reported.CARD_SENTENCES,
        *reported.ROWS_OF_SUMMARY_WORDS, *numbers.N3, *numbers.M3, *numbers.DOTTED,
        *numbers.OCR, *numbers.PROBE, *numbers.WORDS, *numbers.GLUED, *numbers.SEPARATED,
        *numbers.CARD_ENDINGS, *numbers.SLASHED, *numbers.LEADING, *numbers.TWO_DATES,
        *numbers.COORDINATOR, *numbers.LABELLED, *PROBES,
        *(form.format(d=d) for form in rule_e.TABLE_FORMS.values() for d in rule_e.DESCRIPTIONS),
        *(form.format(d=d) for form in rule_e.SCREENSHOT_FORMS.values()
          for d in rule_e.DESCRIPTIONS),
    ]  # fmt: skip
    return list(dict.fromkeys(lines))


def _interleaved(lines: list[str], rows: list[str]) -> list[str]:
    """Each line between two rows, so no two corpus lines touch (no block, wrap or pair)."""
    out = [rows[0]]
    for line in lines:
        out += [line, rows[len(out) // 2 % 2]]
    return out


def test_nothing_shows_more_than_at_8bc2649_in_a_statement(old_textprep):
    text = "\n".join([*HEAD, *_interleaved(_corpus(), ROWS)])
    lost = _regressions(old_textprep, lambda m: m.text_document(text, sha256="x", names=NAMES))
    if lost:
        pytest.fail(f"{len(lost)} lines show more than at {BASE}, e.g. {lost[:5]}")


def test_nothing_shows_more_than_at_8bc2649_in_a_screenshot(old_textprep):
    page = _interleaved(_corpus(), SHOT)
    lost = _regressions(
        old_textprep,
        lambda m: m.pages_document([page], sha256="x", kind="image", names=NAMES),
    )
    if lost:
        pytest.fail(f"{len(lost)} lines show more than at {BASE}, e.g. {lost[:5]}")


def _pages() -> list[tuple[str, list[list[str]]]]:
    from ingest import test_long_numbers as numbers
    from ingest import test_table_start as table

    named = [*table.HEADED.items(), *table.UNHEADED.items(), *table.NO_HEADING.items()]
    pages = [(name, [page]) for name, page in named]
    for wording in table.WORDINGS:
        for heading in (True, False):
            top = [
                "Example Bank plc",
                wording,
                *table.ADDRESS,
                "Sort code 07-12-34 Account 12345678",
            ]
            pages.append((wording, [[*top, *([table.HEADING] if heading else []), *table.ROWS]]))
    for name, (lines, _) in numbers.WRAPS.items():
        pages.append((name, [[*numbers.WRAP_HEAD, *lines, "05 Oct 2026 Little Cafe -3.50 692.32"]]))
    return pages


def test_no_page_shows_more_than_at_8bc2649(old_textprep):
    lost = []
    for _, pages in _pages():
        lost += _regressions(
            old_textprep,
            lambda m, p=pages: m.pages_document(p, sha256="x", kind="pdf", names=NAMES),
        )
    assert lost == []


FIXTURE_DOCS = [("pdf/current-text.pdf", "pdf"), ("pdf/card-text.pdf", "pdf"),
                ("pdf/current-two-page.pdf", "pdf"), ("pdf/card-scanned.pdf", "pdf"),
                ("image/app-screenshot.png", "image")]  # fmt: skip


@pytest.mark.parametrize(("relative", "kind"), FIXTURE_DOCS)
def test_no_fixture_shows_more_than_at_8bc2649(old_textprep, fixtures, relative, kind):
    from tuppence.ingest.extract import ExtractLimits, extract_document

    doc = extract_document(fixtures / relative, kind, sha256="x", limits=ExtractLimits())
    pages: dict[str, list[str]] = {}
    for line in doc.lines:
        pages.setdefault(line.ref.split("L", 1)[0], []).append(line.text)
    rows = list(pages.values())
    lost = _regressions(
        old_textprep, lambda m: m.pages_document(rows, sha256="x", kind=kind, names=NAMES)
    )
    assert lost == []


def test_the_guard_can_fail(old_textprep, monkeypatch):
    """The control: with long numbers left unmasked, the reviewer's lines show more now."""
    from ingest.test_long_numbers import SEPARATED

    monkeypatch.setattr(sensitive, "_long_runs", lambda seen: [])
    text = "\n".join([*HEAD, *_interleaved(SEPARATED, ROWS)])
    lost = _regressions(old_textprep, lambda m: m.text_document(text, sha256="x", names=NAMES))
    assert len(lost) >= 5


def _address_pages() -> list[list[str]]:
    """Address lines in every position: between rows, after the last row, in the header with
    and without a heading row, with and without a name above them."""
    from ingest import test_table_start as table

    blocks = [*table.ADDRESS_LINES.values(), *table.ADDRESS_LAYOUTS]
    rows = [table.ROW_A, table.ROW_B]
    after = [table.ROW_C, table.ROW_D]
    pages = []
    for block in blocks:
        for name in table.NAMES_ABOVE.values():
            lines = [*name, *block]
            pages += [
                [*rows, *lines, *after],
                [*rows, *lines],
                ["Example Bank plc", *lines, *rows],
                ["Example Bank plc", *lines, "Date Description Paid out Paid in Balance", *rows],
                [*rows, lines[-1], *after],  # one line of it on its own
            ]
    return pages


def test_no_address_line_shows_more_than_at_8bc2649(old_textprep):
    """No address line, in any position, is sent now that wasn't at 8bc2649 (strict: no line
    may show more than its own dates). The coordinator's "Apt 2B / 10/12 High St." layout was
    sent at 8bc2649 too, so this guard can't see it; `test_table_start` asserts it absolutely."""
    lost = []
    for page in _address_pages():
        text = "\n".join(page)
        lost += _regressions(
            old_textprep,
            lambda m, t=text: m.text_document(t, sha256="x", names=NAMES),
            strict=True,
        )
        lost += _regressions(
            old_textprep,
            lambda m, p=page: m.pages_document([p], sha256="x", kind="image", names=NAMES),
            strict=True,
        )
    assert lost == []


def test_the_address_guard_can_fail(old_textprep, monkeypatch):
    """The control: with no address block found, address lines 8bc2649 withheld are sent."""
    monkeypatch.setattr(textprep, "_address_blocks", lambda texts, holders=None, single=True: set())
    lost = []
    for page in _address_pages()[:10]:
        text = "\n".join(page)
        lost += _regressions(
            old_textprep, lambda m, t=text: m.text_document(t, sha256="x"), strict=True
        )
    assert lost
