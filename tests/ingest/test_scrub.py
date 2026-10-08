"""G6 (amended): `scrub` is the text the understanding specialists may put in a prompt. It is
built on `prepare_outbound`, the one builder of outbound statement text: a line it can't send
is <HIDDEN>, each detail it masks is <HIDDEN>, and every other run of four or more digits that
isn't an amount or a date it kept is <NUM>. Household names stay (the Pseudonymise setting
governs them); words, dates and amounts are kept."""

from __future__ import annotations

import functools
import re

import pytest

from ingest.digit_runs import _MONEY, _NUMBER, fold, survivors
from tuppence.ingest import sensitive
from tuppence.ingest.models import MaskedLine
from tuppence.ingest.sensitive import HIDDEN, prepare_outbound, scrub

# Each line, and the details in it that must not survive.
DETAILS = [
    ("SORT 20-00-00 ACC 12345678", ["20-00-00", "200000", "12345678"]),
    ("TFR TO SAVINGS 40-11-62 31926819", ["40-11-62", "401162", "31926819"]),
    ("CARD 4929123412341234 GREENBASKET", ["4929123412341234"]),
    ("CARD 4929 1234 5678 9012 GREENBASKET", ["4929", "1234", "5678", "9012"]),
    ("Card ending 4242 Greenbasket", ["4242"]),
    ("VISA X4242 LITTLE CAFE", ["4242"]),
    ("TFR TO SAVINGS ...5678", ["5678"]),
    ("IBAN GB29NWBK60161331926819", ["GB29", "NWBK", "60161331926819"]),
    ("GB29 NWBK 6016 1331 9268 19", ["GB29", "6016", "1331", "9268"]),
    ("FLORIST EXAMPLETOWN EX1 2MP", ["EX1", "2MP"]),
    ("Sort code 12-34-56", ["12-34-56", "123456"]),
    ("Payment to 12 34 56 87654321", ["87654321", "12 34 56"]),
    ("AMAZON 0800 279 7234", ["0800", "7234"]),
    ("Rent 10 Example Road", ["10 Example Road"]),
]


@pytest.mark.parametrize("text, secrets", DETAILS, ids=[t for t, _ in DETAILS])
def test_no_account_or_card_detail_survives(text, secrets):
    out = scrub(text)
    assert not any(ch.isdigit() for ch in out), out  # none of their digits, in any form
    for secret in secrets:
        assert secret.replace(" ", "") not in out.replace(" ", ""), out
    assert HIDDEN in out


@pytest.mark.parametrize(
    "text, expected",
    [
        ("LITTLE CAFE", "LITTLE CAFE"),
        ("GREENBASKET STORES 0873", "GREENBASKET STORES <NUM>"),
        ("REF 12345 STREAMLY", "REF <NUM> STREAMLY"),
        ("O2 MOBILE", "O2 MOBILE"),
        ("7-ELEVEN 2026", "7-ELEVEN <NUM>"),
        ("01/10/2026 LITTLE CAFE 3.40", "01/10/2026 LITTLE CAFE 3.40"),
        ("PAID ON 01 OCT 2026", "PAID ON 01 OCT 2026"),
        ("CAFE 1500.00 ON 3 Oct 2026", "CAFE 1500.00 ON 3 Oct 2026"),
        ("CAFE 1,234.56", "CAFE 1,234.56"),
        ("CAFE £250", "CAFE £250"),
        ("TRANSFER FROM ALEX EXAMPLE", "TRANSFER FROM ALEX EXAMPLE"),
        ("Groceries", "Groceries"),
        ("  Little   Cafe ", "Little Cafe"),
        ("", ""),
    ],
)
def test_words_dates_amounts_and_names_are_kept(text, expected):
    assert scrub(text) == expected


def test_what_prepare_outbound_cannot_send_is_hidden_whole():
    assert prepare_outbound("Available balance £1,184.56") is None
    assert scrub("Available balance £1,184.56") == HIDDEN


def test_scrub_is_built_on_prepare_outbound(monkeypatch):
    """One builder of outbound text: what `prepare_outbound` withholds or masks, `scrub` hides,
    whatever the line says."""
    monkeypatch.setattr(sensitive, "prepare_outbound", lambda text, **kw: None)
    assert scrub("LITTLE CAFE") == HIDDEN
    masked = MaskedLine(text="LITTLE [hidden-a] CAFE", hidden={"[hidden-a]": "X"})
    monkeypatch.setattr(sensitive, "prepare_outbound", lambda text, **kw: masked)
    assert scrub("LITTLE X CAFE") == f"LITTLE {HIDDEN} CAFE"


# --- the differential: nothing prepare_outbound masks shows in scrub's output ---------------


@functools.cache
def _guard_corpus() -> list[str]:
    """Every masking case in M3's guard corpora: the control-regression corpus (parity, balance,
    long-number, rule (e), text prep and withheld-line cases and the reviewers' probes), the
    identifiers and Unicode spellings placed inside a row, and the joined and split numbers the
    long-number guard fuzzes."""
    from ingest import test_long_numbers as numbers
    from ingest import test_sensitive_parity as parity
    from ingest.test_differential_8bc2649 import _corpus

    inside = [
        f"02 Oct 2026 Payment {text} 12.00"
        for text in [*parity.IDENTIFIERS, *parity.ODD, *parity.WITH_DETAILS]
    ]
    fuzzed = [*numbers._fuzz(0), *numbers._fuzz(1), *numbers._fuzz_split(0)]
    return list(dict.fromkeys([*_corpus(), *inside, *fuzzed, *(t for t, _ in DETAILS)]))


def _groups(text: str) -> list[str]:
    """The digits of each number in `text` (groups joined by short separators), money taken
    out: the guard's own reading (digit_runs), not the masking code's."""
    plain = _MONEY.sub(lambda m: "x" * len(m.group(0)), fold(text))
    return [re.sub(r"\D", "", m.group(0)) for m in _NUMBER.finditer(plain)]


def _shows(line: str, out: str) -> list[str]:
    masked = prepare_outbound(line)
    if masked is None:
        return [] if out == HIDDEN else [f"withheld line sent: {out}"]
    shown = _groups(out)
    problems = [f"long number {r}" for r in survivors(line, out)]
    for detail in masked.hidden.values():
        if re.search(r"[A-Za-z]", detail) and detail in out:  # a name, postcode or IBAN
            problems.append(f"detail {detail!r}")
        for group in _groups(detail):
            if len(group) >= 3 and any(group in s for s in shown):
                problems.append(f"digits {group} of {detail!r}")
    if "[hidden-" in out:
        problems.append("placeholder left")
    return problems


def test_nothing_prepare_outbound_masks_shows_in_scrub():
    lines = _guard_corpus()
    assert len(lines) > 1500
    leaks = {line: (scrub(line), bad) for line in lines if (bad := _shows(line, scrub(line)))}
    if leaks:
        sample = list(leaks.items())[:5]
        pytest.fail(f"{len(leaks)} of {len(lines)} lines show more than prepare_outbound, {sample}")


# Amounts and dates, read apart from the masking code (a numeric, ISO or named-month date).
_DATES = re.compile(
    r"\b\d{1,2}[/.\- ]\d{1,2}[/.\- ]\d{2,4}\b|\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?[ -]?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\.?(?:[ -]?\d{4})?\b",
    re.IGNORECASE,
)


def _unexplained_runs(out: str) -> list[str]:
    """Runs of four or more digits in `out` that are not part of an amount or a date."""
    covered = [False] * len(out)
    for pattern in (_MONEY, _DATES):
        for m in pattern.finditer(out):
            covered[m.start() : m.end()] = [True] * (m.end() - m.start())
    return [
        m.group(0) for m in re.finditer(r"\d{4,}", out) if not all(covered[m.start() : m.end()])
    ]


def test_every_other_run_of_four_digits_is_a_num():
    """Left in scrub's output: amounts and dates prepare_outbound kept, nothing else."""
    bad = {}
    for line in _guard_corpus():
        out = scrub(line)
        if runs := _unexplained_runs(out):
            bad[line] = (out, runs)
    assert bad == {}


def test_the_differential_catches_a_scrub_with_its_own_regex_set():
    """The guard is live: a scrub that only blanked runs of four digits (a separate rule set,
    which G6 forbids) shows sort codes, card endings and split numbers."""

    def naive(text: str) -> str:
        return re.sub(r"\d{4,}", "<NUM>", sensitive.normalise(text))

    assert _shows("SORT 20-00-00 ACC 12345678", naive("SORT 20-00-00 ACC 12345678"))
    caught = [line for line in _guard_corpus() if _shows(line, naive(line))]
    assert len(caught) > 200
