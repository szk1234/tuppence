"""Balance and summary figures are worked out on this device and never sent (coordinator probes
after 198c22d). A line made only of balance or summary words and figures is withheld, however
it is worded; a line that may be a balance or a row is held back for the person; and nothing
the text prep at 75f4474 withheld is sent now, unless it is deliberately sent masked or is a
single payee row (the control-regression guard)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

from ingest.helpers import history_missing
from tuppence.ingest.models import Line
from tuppence.ingest.textprep import pages_document, text_document

HEAD = [
    "Example Bank",
    "Statement 01/03/2026 to 31/03/2026",
    "Date Description Money out Money in Balance",
]
ROWS = ["02/03/2026 TESCO STORES 12.50 1,000.00", "03/03/2026 SALARY 2,000.00 3,000.00"]
SHOT = ["Mon 2 Mar TESCO STORES -£12.50", "Tue 3 Mar SALARY +£2,000.00"]

BALANCES = [
    "Balance 1,234.56",
    "Your balance is £1,234.56",
    "Your new balance £1,234.56",
    "Balance on 31 March £1,234.56",
    "Balance b/f 1,000.00",
    "Balance c/f 1,234.56",
    "BALANCE FORWARD 1,000.00",
    "Brought forward 1,000.00",
    "BALANCE CARRIED FORWARD 1,234.56",
    "Bal £1,234.56",
    "Current balance: £1,234.56",
    "Balance (CR) 1,234.56",
    "You owe £250.00",
    "Outstanding balance £250.00",
    "Amount owed £250.00",
    "Funds available £900.00",
    "Your account is £1,234.56 in credit",
    "Balance at close of business 1,234.56",
    "Available balance £1,184.56",
    "Credit limit £3,000.00",
    "Minimum payment £25.00",
    "Amount due £250.00",
    "Total paid in 2,000.00",
    "Money in £2,000.00 Money out £438.78",
    "Closing balance 31/03 £1,234.56 Total money in £2,000.00",
]
UNSURE = ["Savings pot £5,000.00", "02/03/2026 CREDIT 900.00"]
ROWS_STILL_SENT = [
    "Interest charged this month £12.34",
    "Net pay 2,000.00",
    "BALANCE TRANSFER FEE 5.00",
    "TOTAL FITNESS GYM 30.00",
]


def _text(line: str):
    doc = text_document("\n".join([*HEAD, ROWS[0], line, ROWS[1]]), sha256="x")
    return doc, f"L{len(HEAD) + 2}"


def _shot(line: str):
    doc = pages_document([[SHOT[0], line, SHOT[1]]], sha256="x", kind="image")
    return doc, "P1L2"


@pytest.mark.parametrize("path", [_text, _shot], ids=["text", "screenshot"])
@pytest.mark.parametrize("line", BALANCES)
def test_balance_and_summary_lines_are_never_sent(line, path):
    doc, ref = path(line)
    assert ref not in doc.data_refs and ref in doc.preamble_refs
    assert ref not in doc.held_amount_refs  # a balance, not a row


@pytest.mark.parametrize("path", [_text, _shot], ids=["text", "screenshot"])
@pytest.mark.parametrize("line", UNSURE)
def test_a_line_that_may_be_a_balance_or_a_row_is_held_back(line, path):
    doc, ref = path(line)
    assert ref not in doc.data_refs and ref in doc.held_amount_refs


@pytest.mark.parametrize("line", ROWS_STILL_SENT)
def test_rows_that_use_a_balance_word_are_still_sent(line):
    doc, ref = _text(line)
    assert ref in doc.data_refs and ref not in doc.held_amount_refs


# --- the control-regression guard: nothing withheld at 75f4474 is sent now ---------------------

BASE = "75f4474"
_SOURCE = Path(__file__).resolve().parents[2]


def _old(path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "show", f"{BASE}:{path}"],
            cwd=_SOURCE,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


@pytest.fixture(scope="module")
def old_textprep() -> types.ModuleType:
    sensitive_source = _old("src/tuppence/ingest/sensitive.py")
    textprep_source = _old("src/tuppence/ingest/textprep.py")
    if sensitive_source is None or textprep_source is None:
        history_missing(BASE)
    old_sensitive = types.ModuleType("old_sensitive")
    exec(compile(sensitive_source, "old_sensitive.py", "exec"), old_sensitive.__dict__)  # noqa: S102 - the repo's own earlier code
    sys.modules["old_sensitive"] = old_sensitive
    source = textprep_source.replace(
        "from tuppence.ingest import sensitive", "import old_sensitive as sensitive"
    )
    spec = importlib.util.spec_from_loader("old_textprep", loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, "old_textprep.py", "exec"), module.__dict__)  # noqa: S102 - as above
    return module


def _sent_on_purpose(doc, ref: str) -> bool:
    """New text prep sends a line the old one withheld only on purpose: with its account
    details masked (and nothing of a long number left: no run of six digits from the printed
    line survives, m4), or as a single payee row whose only fault was a summary word."""
    from ingest.digit_runs import survivors
    from tuppence.ingest.textprep import _SUMMARY_WORD, _single_transaction, only_summary_vocab

    text = doc.by_ref()[ref].text
    if ref in doc.masked:
        return not survivors(text, doc.masked[ref].text)
    return (
        bool(_SUMMARY_WORD.search(text))
        and _single_transaction(text, False)
        and not only_summary_vocab(text)  # a payee's name is on it
    )


def _corpus() -> list[str]:
    from ingest.test_long_numbers import M3, N3
    from ingest.test_sensitive_parity import EVERY, PLAIN, REGRESSION
    from ingest.test_textprep import MIXED_SUMMARIES, OUTCOMES

    return list(
        dict.fromkeys(
            [*EVERY, *PLAIN, *REGRESSION, *BALANCES, *UNSURE, *ROWS_STILL_SENT,
             *MIXED_SUMMARIES, *(line for line, _ in OUTCOMES), *N3, *M3]
        )
    )  # fmt: skip


def _sent_now(old_textprep, lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        rows = [*HEAD, ROWS[0], line, ROWS[1]]
        old = old_textprep.text_document("\n".join(rows), sha256="x")
        new = text_document("\n".join(rows), sha256="x")
        ref = f"L{len(HEAD) + 2}"
        if ref in old.preamble_refs and ref in new.data_refs and not _sent_on_purpose(new, ref):
            out.append(line)
    return out


def test_nothing_withheld_at_75f4474_is_sent_from_the_parity_corpus(old_textprep):
    assert _sent_now(old_textprep, _corpus()) == []


def test_the_guard_catches_a_partial_mask(old_textprep, monkeypatch):
    """m4, the mutation: with long numbers left unmasked (as at 10e530c, re-review N3), the
    lines 75f4474 withheld go out with an account number in them, and the guard reports them.
    Before m4 it let every masked line through."""
    from ingest.test_long_numbers import N3
    from tuppence.ingest import sensitive

    assert _sent_now(old_textprep, N3) == []
    monkeypatch.setattr(sensitive, "_long_runs", lambda seen: [])
    caught = _sent_now(old_textprep, N3)
    for line in (
        "03/10/2026 TO 20-11-33 / 87654321 RENT -250.00",
        "03/10/2026 TO 20-11-33, 87654321 RENT -250.00",
        "03/10/2026 TO 20-11-33 (87654321) RENT -250.00",
        "03/10/2026 TO 20-11-33:87654321 RENT -250.00",
        "03/10/2026 TO 20-11-33 ACCNO87654321 -250.00",
    ):
        assert line in caught


FIXTURE_DOCS = [
    ("pdf/current-text.pdf", "pdf"),
    ("pdf/card-text.pdf", "pdf"),
    ("pdf/current-two-page.pdf", "pdf"),
    ("pdf/card-scanned.pdf", "pdf"),
    ("image/app-screenshot.png", "image"),
]


@pytest.mark.parametrize(("relative", "kind"), FIXTURE_DOCS)
def test_nothing_withheld_at_75f4474_is_sent_from_the_fixtures(old_textprep, relative, kind):
    from ingest.helpers import fixture_pages

    rows = [list(page) for page in fixture_pages(relative, kind)]
    old = old_textprep.pages_document(rows, sha256="x", kind=kind)
    new = pages_document(rows, sha256="x", kind=kind)
    sent_now = [
        ref for ref in old.preamble_refs if ref in new.data_refs and not _sent_on_purpose(new, ref)
    ]
    assert sent_now == []


def test_the_guard_can_fail(old_textprep):
    """The control: a line the old text prep withheld, put in the new one's data, is caught."""
    lines = [*HEAD, ROWS[0], "Balance 1,234.56", ROWS[1]]
    old = old_textprep.text_document("\n".join(lines), sha256="x")
    new = text_document("\n".join(lines), sha256="x")
    ref = f"L{len(HEAD) + 2}"
    assert ref in old.preamble_refs
    new.data_refs.append(ref)
    assert not _sent_on_purpose(new, ref)
    assert Line(ref=ref, text="Balance 1,234.56") in new.lines
