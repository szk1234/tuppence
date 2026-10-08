"""R-M3-23 (a), re-review N3 and m3: inside a line sent to the AI reader, every run of six or
more digits that isn't an amount or a date is masked, however it is joined to a sort code or
glued to words, and card endings in their short forms are masked too. The guard, read by an
oracle written apart from the masking code: no run of six or more digits from the printed line
survives in anything sent."""

from __future__ import annotations

import json
import random
import re

import pytest

from ingest.digit_runs import fold, runs, survivors
from ingest.helpers import add_account, cloud_model, drain, use_local_model
from tuppence.ingest import sensitive
from tuppence.ingest.textprep import sent_lines, text_document

NAMES = ["Alex Example", "Pat Example"]
HEAD = ["Example Bank", "Statement 01/10/2026 to 31/10/2026", "Date Description Amount"]

# The reviewer's N3 probe (p_fpo): an account number after a masked sort code, joined any way.
N3 = [
    "03/10/2026 FPO 20-11-33 87654321 J SMITH -250.00",
    "03/10/2026 TO 20-11-33 / 87654321 RENT -250.00",
    "03/10/2026 TO 20-11-33 - 87654321 RENT -250.00",
    "03/10/2026 TO 20-11-33, 87654321 RENT -250.00",
    "03/10/2026 TO 20-11-33 87654321RENT -250.00",
    "03/10/2026 TO J SMITH SC 20-11-33 87654321 -250.00",
    "03/10/2026 TO 20-11-33 ACC 87654321 -250.00",
    "03/10/2026 TO 20-11-33 ACCNO87654321 -250.00",
    "03/10/2026 BGC 20-11-33 87654321 SALARY 1,250.00",
    "03/10/2026 TO 20-11-33 8765 4321 RENT -250.00",
    "03/10/2026 TO 20-11-33 (87654321) RENT -250.00",
    "03/10/2026 TO 20-11-33:87654321 RENT -250.00",
    "05/10/2026 FPO J SMITH20-11-33 41234567 -75.00",
    "07/10/2026 TO 20-11-33, 55554444 -10.00",
    "03/10/2026 PAYMENT REF 87654321 -250.00",
    "03/10/2026 TO 87654321 -250.00",
    "03/10/2026 ROLL NO 1234/5678 -250.00",
    "03/10/2026 CUSTOMER REF:ABC12345678 -250.00",
    "03/10/2026 TFR A/C87654321 -250.00",
    "03/10/2026 SC201133AC87654321 -250.00",
    "03/10/2026 TO 201133 87654321 -250.00",
    "03/10/2026 TO 20 11 33 87654321 -250.00",
    "03/10/2026 TO 20-11-33-87654321 -250.00",
    "03/10/2026 4929123412341234 -12.00",
    "03/10/2026 4929-1234-1234-1234 -12.00",
    "03/10/2026 AMAZON 0800 279 7234 -12.00",
    "03/10/2026 SHOP 123456.78 REF 99887766",
    "03/10/2026 REF 12.345678 -12.00",
]
# m3: forms that were sent at 75f4474 too.
M3 = [
    "03/10/2026 CARD ENDING4242 -12.00",
    "03/10/2026 VISA *4242 -12.00",
    "03/10/2026 VISA X4242 -12.00",
    "03/10/2026 IBANGB29NWBK60161331926819 -12.00",
    "03/10/2026 ALEX-EXAMPLE -12.00",
    "03/10/2026 ALEX_EXAMPLE -12.00",
    "03/10/2026 EXAMPLE/ALEX -12.00",
    "03/10/2026 TO ALEX EXAMPLE123 -12.00",
    "03/10/2026 TO 2⃣0⃣-1⃣1⃣-3⃣3⃣ -250.00",
    "03/10/2026 TO 20·11·33 87654321 -250.00",
    "03/10/2026 TO 20_11_33 87654321 -250.00",
    "03/10/2026 TO 20.11.33 87654321 -250.00",
]
# Coordinator probe after b8ea4d0: dotted groups, and characters a scan reads for digits.
DOTTED = [
    "03/10/2026 TO 20.11.33 87.65.43.21 250.00",
    "TO 20.11.33 87.65.43.21 250.00",
    "03/10/2026 TO 87.65.43.21 RENT -250.00",
    "03/10/2026 PAID 20.11.33 -1.00",
    "03/10/2026 SORT CODE 20.11.33 -1.00",
    "03/10/2026 REF 12.34.56.78 -1.00",
    "03/10/2026 TO 20 11 33 8765 4321 -250.00",
    "03/10/2026 TO 2 0 1 1 3 3 8 7 6 5 4 3 2 1 10.00",
]
OCR = [
    "TO 2O-11-33 876S4321 10.00",
    "03/10/2026 TO 2O-11-33 876S4321 10.00",
    "03/10/2026 FPO J SMITH2O-l1-33 4l234S67 -75.00",
    "03/10/2026 ACC 8765432I -250.00",
    "03/10/2026 TO 20-11-33 B7654321 -250.00",
    "03/10/2026 CARD 4929 I234 S678 9O12 -12.00",
    "03/10/2026 REF 12345|7890 -12.00",
    "03/10/2026 TO 2Z-11-33 87654321 -250.00",
]
# The rest of the coordinator's probe: each masks already, and stays masked.
PROBE = [
    "FPO TO 20-11-33 / 87654321 RENT 250.00", "TO 201133 87654321 250.00",
    "TO 20 11 33 8765 4321 250.00", "CARD 4242 4242 4242 4242 12.50", "REF 1234567890 BILL 20.00",
    "IBAN GB29NWBK60161331926819 50.00", "GB29 NWBK 6016 1331 9268 19 50.00",
    "acct no. 8765-4321 10.00", "a/c 87654321/201133 10.00", "ROLL NO 12345678A 10.00",
    "MANDATE 00123456 DD 10.00", "ORDER 12345678 AMAZON 1,234,567.89",
    "PHONE 07700 900123 10.00", "NI QQ123456C 10.00", "TO ２０１１３３ ８７６５４３２１ 10.00",
]  # fmt: skip
WORDS = [
    "02/10/2026 BOOTS 12.50", "02/10/2026 ZARA SO 5.00", "02/10/2026 SOLO BIZ ZOO 3.20",
    "02/10/2026 O2 MOBILE 15.00", "02/10/2026 BOSS LOOKS 7.00 1,000.00", "05.10.26 SHOP 4.00",
    "05 10 26 HOMEWARE DIRECT 43.27", "Mon 5 Oct Little Cafe -£3.40", "02/10/2026 SHOP 1234.56",
]  # fmt: skip
# Re-review 2 R2: the halves of a number joined by a separator outside the old join set, an
# account number after a label written with a comma, and card endings after punctuation.
SEPARATED = [
    "03/10/2026 TO 20-11-33 8765 - 4321 -250.00", "03/10/2026 TO 20-11-33 8765 / 4321 -250.00",
    "03/10/2026 TO 20-11-33 8765,4321 -250.00", "03/10/2026 TO 20-11-33 8765+4321 -250.00",
    "03/10/2026 TO 20-11-33 8765#4321 -250.00", "03/10/2026 TO 20-11-33 8765\\4321 -250.00",
    "03/10/2026 TO 20-11-33 8765:4321 -250.00", "03/10/2026 ACC NO 8765,4321 -250.00",
    "03/10/2026 TO 8765 - 4321 RENT -250.00", "03/10/2026 TO 8765 / 4321 RENT -250.00",
    "03/10/2026 REF 1234, 5678 -1.00", "03/10/2026 REF 123 + 456 -1.00",
]  # fmt: skip
CARD_ENDINGS = [
    "03/10/2026 CARD ENDING IN: 9012 -12.00", "03/10/2026 ENDING-9012 -12.00",
    "03/10/2026 CARD #9012 -12.00", "03/10/2026 CARD ENDING: 9012 -12.00",
    "03/10/2026 CARD ENDING # 9012 -12.00", "03/10/2026 VISA ENDING IN - 9012 -12.00",
    "03/10/2026 CARD: 9012 -12.00",
]  # fmt: skip
# R5: numbers joined by a slash and nothing else, and a sort code spelled with spaces or dots
# at the start of a line (where a date may be), with the account number joined after it.
SLASHED = [
    "03/10/2026 PAYMENT 1234/5678 -250.00", "03/10/2026 MANDATE 123/456/789 -12.00",
    "03/10/2026 REF 12/34/5678 -1.00", "PAYMENT 1234/5678 -250.00",
]  # fmt: skip
LEADING = [
    "20.11.33 87654321 RENT", "20 11 33 87654321 RENT", "20.11.33 87654321 RENT 250.00",
    "20 11 33 8765 4321 RENT 250.00", "20.11.33 8765.4321 RENT 250.00",
    "Mon 20.11.33 87654321 RENT 250.00", "03.10.26 04.10.26 87654321 RENT 12.50",
]  # fmt: skip
# R4: a row's two dates (transaction and posting) printed with dots or spaces.
TWO_DATES = [
    "03.10.26 04.10.26 TESCO STORES 12.50", "03 10 26 04 10 26 TESCO STORES 12.50",
    "03.10.2026 04.10.2026 TESCO STORES 12.50", "Mon 03.10.26 04.10.26 LITTLE CAFE 3.40",
]  # fmt: skip
M3_SECRETS = {
    M3[0]: ["4242"], M3[1]: ["4242"], M3[2]: ["4242"], M3[3]: ["60161331926819", "GB29"],
    M3[4]: ["ALEX"], M3[5]: ["ALEX"], M3[6]: ["ALEX"], M3[7]: ["ALEX"], M3[8]: ["201133"],
    M3[9]: ["201133", "87654321"], M3[10]: ["201133", "87654321"], M3[11]: ["87654321"],
}  # fmt: skip


def _sent(line: str) -> str | None:
    doc = text_document("\n".join([*HEAD, "01/10/2026 SHOP -1.00", line, "05/10/2026 CAFE -2.00"]),
                        sha256="x", names=NAMES)  # fmt: skip
    if "L5" not in doc.data_refs:
        assert "L5" in doc.held_amount_refs  # withheld, never silently
        return None
    return sent_lines(doc)["L5"].text


@pytest.mark.parametrize("line", N3 + M3)
def test_no_run_of_six_digits_from_the_line_is_sent(line):
    sent = _sent(line)
    if sent is not None:
        assert survivors(line, sent) == [], sent
        assert runs(line) == [] or "[hidden-" in sent


@pytest.mark.parametrize("line", N3)
def test_a_row_with_an_account_number_is_still_sent(line):
    """Masked, not withheld: the row is read and its details put back on this device."""
    assert _sent(line) is not None


@pytest.mark.parametrize("line", M3)
def test_the_short_and_glued_forms_are_masked(line):
    sent = _sent(line)
    if sent is not None:
        folded = fold(sent).replace(" ", "").casefold()
        assert [s for s in M3_SECRETS[line] if s.casefold() in folded] == [], sent


def test_amounts_and_dates_are_never_masked():
    for line, kept in [
        ("03/10/2026 TO 87654321 1,234.56 9,876.54", ["03/10/2026", "1,234.56", "9,876.54"]),
        ("2026-10-03 REF 123456 123456.78", ["2026-10-03", "123456.78"]),
        ("3 Oct 2026 REF 99887766 £250", ["3 Oct 2026", "£250"]),
    ]:
        out = sensitive.prepare_outbound(line)
        assert out is not None and all(k in out.text for k in kept), out


def test_a_spaced_or_dotted_date_is_left_alone_only_as_the_lines_own_date():
    """ "05 10 26" and "05.10.26" at the start of a row are its date. Anywhere else such a group
    may be a sort code, so it is masked (with any number joined to it)."""
    for line in ("05 10 26 HOMEWARE DIRECT 43.27", "05.10.26 SHOP 4.00", "05 10 26 400 12.30"):
        out = sensitive.prepare_outbound(line)
        assert out is not None and out.text == line
    for line in ("03/10/2026 TO 20.11.33 87654321 -1.00", "03/10/2026 TO 20 11 33 87654321 -1.00"):
        out = sensitive.prepare_outbound(line)
        assert out is not None and out.text == "03/10/2026 TO [hidden-a] -1.00"


def _corpus() -> list[str]:
    from ingest.test_balance_lines import BALANCES, ROWS_STILL_SENT, UNSURE
    from ingest.test_sensitive_parity import EVERY, PLAIN, REGRESSION, WITH_DETAILS

    return list(dict.fromkeys([*EVERY, *PLAIN, *REGRESSION, *WITH_DETAILS, *BALANCES, *UNSURE,
                               *ROWS_STILL_SENT, *N3, *M3, *DOTTED, *OCR, *PROBE, *SEPARATED,
                               *SLASHED, *LEADING, *TWO_DATES]))  # fmt: skip


def _fuzz(seed: int, count: int = 400) -> list[str]:
    """Rows with a random number joined to a random sort code, glued to random words, in any
    position: the guard holds for each."""
    rnd = random.Random(seed)
    joins = [" ", " / ", "/", ", ", " - ", "-", ":", "(", "", " REF ", " ACC", "·", "_", "."]
    words = ["TO", "FPO", "BGC", "RENT", "J SMITH", "SO", "TFR", "PAYMENT"]
    out = []
    for _ in range(count):
        sort = rnd.choice(["20-11-33", "201133", "20 11 33", "20.11.33", "2O-11-33", ""])
        number = "".join(rnd.choice("0123456789") for _ in range(rnd.randint(6, 16)))
        if rnd.random() < 0.15:  # a scan's misreads
            number = number[:2] + number[2:].translate(str.maketrans("051282", "OSlBZI"))
        elif rnd.random() < 0.15:  # dots between pairs
            number = ".".join(number[i : i + 2] for i in range(0, len(number), 2))
        parts = [rnd.choice(words), sort, rnd.choice(joins) + number, rnd.choice(words)]
        rnd.shuffle(parts)
        amount = rnd.choice(["-250.00", "1,250.00", "£5", "12.30 1,000.00"])
        out.append(f"0{rnd.randint(1, 9)}/10/2026 {''.join(parts)} {amount}")
    return out


_SEPARATORS = [" - ", " / ", ",", ", ", "+", " + ", "#", "\\", ":", ": ", "/", " -", "- "]


def _fuzz_split(seed: int, count: int = 400) -> list[str]:
    """R2: a number of six to twelve digits split in two, each half too short to be a long
    number alone, joined by any separator, after a sort code or a word, with or without the
    line's own date."""
    rnd = random.Random(seed)
    out = []
    for _ in range(count):
        number = "".join(rnd.choice("0123456789") for _ in range(rnd.randint(6, 10)))
        cut = rnd.randint(2, len(number) - 2)
        joined = number[:cut] + rnd.choice(_SEPARATORS) + number[cut:]
        before = rnd.choice(["TO 20-11-33 ", "TO ", "REF ", "ACC NO ", "FPO J SMITH ", ""])
        after = rnd.choice([" RENT", "", " J SMITH"])
        date = rnd.choice(["03/10/2026 ", "03.10.26 ", "03 Oct ", ""])
        out.append(f"{date}{before}{joined}{after} {rnd.choice(['-250.00', '12.30 1,000.00'])}")
    return out


@pytest.mark.parametrize("line", DOTTED + OCR + PROBE)
def test_dotted_and_scanned_numbers_are_masked(line):
    """Gap 1: a dotted group is left alone only as the line's own date or a well-formed amount.
    Gap 2: inside a mostly-digit token, O, S, l, I, |, B and Z are read as digits, so a scan's
    misreading can't split a number into short pieces that go out."""
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is not None, line  # masked, not withheld
    assert survivors(line, out.text) == [], out.text
    assert "[hidden-" in out.text


def test_ocr_misreads_are_masked_whole():
    out = sensitive.prepare_outbound("TO 2O-11-33 876S4321 10.00")
    assert out is not None and out.text == "TO [hidden-a] 10.00"


@pytest.mark.parametrize("line", WORDS)
def test_words_dates_and_amounts_are_left_as_printed(line):
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is not None and out.text == line


def _leaks(lines: list[str]) -> dict[str, tuple[str, list[str]]]:
    leaked = {}
    for line in lines:
        out = sensitive.prepare_outbound(line, names=NAMES)
        if out is not None and (bad := survivors(line, out.text)):
            leaked[line] = (out.text, bad)
    return leaked


@pytest.mark.parametrize("source", ["corpus", "fuzz", "split"])
def test_no_run_of_six_digits_survives_prepare_outbound(source):
    """The guard over every line the parity corpus holds and a fuzz of joined and split
    numbers: what `prepare_outbound` sends keeps no run of six digits from the printed line.
    A failure is reported plainly (R5): comparing a large dict made a failing run take minutes."""
    lines = {
        "corpus": _corpus,
        "fuzz": lambda: _fuzz(0) + _fuzz(1),
        "split": lambda: _fuzz_split(0) + _fuzz_split(1),
    }[source]()
    leaked = _leaks(lines)
    if leaked:
        sample = list(leaked.items())[:5]
        pytest.fail(f"{len(leaked)} of {len(lines)} lines leak a long number, e.g. {sample}")


@pytest.mark.parametrize("line", SEPARATED + SLASHED + LEADING)
def test_a_number_split_by_any_separator_is_masked_whole(line):
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is not None, line  # masked, not withheld
    assert survivors(line, out.text) == [], out.text
    assert not re.search(r"8765|4321|5678|456|789", re.sub(r"[\d,.]+\.\d{2}", "", out.text))


@pytest.mark.parametrize("line", CARD_ENDINGS)
def test_a_card_ending_after_punctuation_is_masked(line):
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is None or "9012" not in out.text, out


@pytest.mark.parametrize("line", TWO_DATES)
def test_a_rows_two_leading_dates_are_left_as_printed(line):
    """R4: "03.10.26 04.10.26 ..." (transaction and posting date) was masked as one run, so
    the row's own date was hidden."""
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is not None and out.text == line


def test_the_guard_catches_a_slash_left_out_of_the_joins(monkeypatch):
    """R5, the reviewer's blind-spot mutation: with "/" no longer joining the groups of a
    number, "PAYMENT 1234/5678" goes out whole, and the guard says so."""
    separator = sensitive._separator
    monkeypatch.setattr(sensitive, "_separator", lambda ch: ch != "/" and separator(ch))
    assert _leaks(SLASHED)


def test_the_guard_catches_a_leading_sort_code_taken_for_a_date(monkeypatch):
    """R5, the other blind spot: with any group of digits at the start of a line taken for its
    date, whatever is joined after it, "20.11.33 87654321 RENT" sends the sort code."""
    monkeypatch.setattr(sensitive, "_OWN_DATE_JOINED", 10**9)
    assert _leaks(LEADING)


def test_a_heading_with_a_long_number_is_hidden_in_a_layout_sketch():
    assert sensitive.mask("Payee 87654321") == sensitive.HIDDEN
    assert sensitive.mask("Reference") == "Reference"


STATEMENT = """Example Bank plc
Statement period 01/10/2026 to 31/10/2026
Date Description Amount
01/10/2026 Greenbasket Stores -42.18
03/10/2026 TO 20-11-33 / 87654321 RENT -250.00
05/10/2026 FPO J SMITH20-11-33 41234567 -75.00
07/10/2026 TO 20-11-33, 55554444 -10.00
"""


@pytest.mark.parametrize("mode", ["local", "cloud+pseudonymise"])
def test_the_reviewers_statement_sends_no_account_number(ingest_env, mode):
    """The reviewer's end-to-end probe (test_rr_fpo_e2e): the numbers were in the requests in
    both modes. Now none is, and the rows are stored as printed."""
    services, scripted = ingest_env
    if mode == "local":
        use_local_model(services)
    else:
        cloud_model(services, acknowledge=True)
        services.settings.set("privacy.pseudonymise", True, expected_version=0)
    account = add_account(services, "other", "current", "Probe")
    out = services.ingest.upload("fpo.txt", STATEMENT.encode())
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(out.record.id)
    sent = json.dumps(scripted.requests)
    assert sent and not [n for n in ("87654321", "41234567", "55554444") if n in sent]
    # the rows (the period on the second line is sent as a fact by design)
    assert all(not survivors(line, sent) for line in STATEMENT.splitlines()[3:])
    stored = [t.raw_description for t in services.statements.transactions(record.id)]
    if record.status == "needs_review":
        stored = [r["raw_description"] for r in record.draft["parsed"]["rows"]]
    assert any("87654321" in d for d in stored) and any("41234567" in d for d in stored)


# The reviewer's whole p_glued probe (m3): each identifier, in every spelling, is never sent.
GLUED = [
    "03/10/2026 FPO J SMITH20-11-33 41234567 -250.00", "03/10/2026 TFR A/C87654321 -250.00",
    "03/10/2026 TFR TO A/C:87654321 -250.00", "03/10/2026 TFR ACC87654321 -250.00",
    "03/10/2026 SC201133AC87654321 -250.00", "03/10/2026 TO 20-11-33/87654321 -250.00",
    "03/10/2026 TO 201133 87654321 -250.00", "03/10/2026 TO 20 11 33 87654321 -250.00",
    "03/10/2026 TO 20.11.33 87654321 -250.00", "03/10/2026 TO 20-11-33-87654321 -250.00",
    "03/10/2026 CARD ENDING4242 -12.00", "03/10/2026 CARD ENDING 4242 -12.00",
    "03/10/2026 CARD NO.4242 -12.00", "03/10/2026 VISA ****4242 -12.00",
    "03/10/2026 VISA XXXX4242 -12.00", "03/10/2026 VISA X4242 -12.00",
    "03/10/2026 VISA *4242 -12.00", "03/10/2026 IBANGB29NWBK60161331926819 -12.00",
    "03/10/2026 GB29NWBK60161331926819 -12.00", "03/10/2026 GB29 NWBK 6016 1331 9268 19 -12.00",
    "03/10/2026 4929123412341234 -12.00", "03/10/2026 4929-1234-1234-1234 -12.00",
    "03/10/2026 ALEXEXAMPLE -12.00", "03/10/2026 AlexExample -12.00",
    "03/10/2026 ALEX-EXAMPLE -12.00", "03/10/2026 ALEX_EXAMPLE -12.00",
    "03/10/2026 EXAMPLE/ALEX -12.00", "03/10/2026 A.EXAMPLE -12.00",
    "03/10/2026 MR A EXAMPLE -12.00", "03/10/2026 PAYPAL *ALEXEXAMPLE -12.00",
    "03/10/2026 TO ALEX EXAMPLE123 -12.00", "03/10/2026 TO EXAMPLEA -12.00",
    "03/10/2026 ＡＬＥＸ　ＥＸＡＭＰＬＥ -12.00", "03/10/2026 A​LEX EXAMPLE -12.00",
    "03/10/2026 ÁLEX EXÁMPLE -12.00", "03/10/2026 ÁLEX EXAMPLE -12.00",
    "03/10/2026 TO ２０－１１－３３ ８７６５４３２１ -250.00",
    "03/10/2026 TO 20​-11-33 8765­4321 -250.00",
    "03/10/2026 TO 20‐11‐33 87654321 -250.00",
    "03/10/2026 TO 2⃣0⃣-1⃣1⃣-3⃣3⃣ -250.00",
    "03/10/2026 TO 𝟐𝟎-𝟏𝟏-𝟑𝟑 𝟖𝟕𝟔𝟓𝟒𝟑𝟐𝟏 -250.00", "03/10/2026 TO ٢٠-١١-٣٣ ٨٧٦٥٤٣٢١ -250.00",
    "03/10/2026 TO 20·11·33 87654321 -250.00", "03/10/2026 TO 20_11_33 87654321 -250.00",
    "03/10/2026 PAYMENT REF 87654321 -250.00", "03/10/2026 TO 87654321 -250.00",
    "03/10/2026 ROLL NO 1234/5678 -250.00", "03/10/2026 CUSTOMER REF:ABC12345678 -250.00",
    "03/10/2026 FLAT 3 RENT -650.00", "03/10/2026 12 MILL LANE DELIVERY -6.50",
    "03/10/2026 DELIVERY EX12MP -6.50", "03/10/2026 DELIVERY EX1 2MP -6.50",
]  # fmt: skip
GLUED_SECRETS = ["201133", "87654321", "4242", "60161331926819", "6016", "4929", "alexexample",
                 "alex", "ex12mp", "flat3", "milllane", "12345678"]  # fmt: skip


@pytest.mark.parametrize("line", GLUED)
def test_every_spelling_the_reviewer_tried_is_masked(line):
    out = sensitive.prepare_outbound(line, names=NAMES)
    if out is None:
        return  # withheld (and reported by text prep): nothing of it is sent
    flat = re.sub(r"[\s\-./·_:]", "", fold(out.text)).casefold()
    printed = re.sub(r"[\s\-./·_:]", "", fold(line)).casefold()
    assert [s for s in GLUED_SECRETS if s in printed and s in flat] == [], out.text


# Re-review 2 R3: a number a line wrap splits between two lines. Each line was masked on its own,
# so the part on the next line went out ("...20-11-33 8765" / "4321 RENT").
WRAP_HEAD = [
    "Example Bank plc",
    "Statement period 01/10/2026 to 31/10/2026",
    "Date Description Amount Balance",
    "01 Oct 2026 Greenbasket Stores -42.18 957.82",
]
WRAPS = {
    "space-split account": (["03 Oct 2026 FPO J SMITH 20-11-33 8765 -250.00 707.82", "4321 RENT"],
                            ["8765", "4321"]),
    "mid-token split": (["03 Oct 2026 FPO J SMITH 20-11-33 87654 -250.00 707.82", "321 RENT"],
                        ["87654", "321"]),
    "sort code / account": (["03 Oct 2026 FPO J SMITH 20-11-33 -250.00 707.82", "87654321 RENT"],
                            ["87654321"]),
    "card 8+8": (["03 Oct 2026 CARD 4929 1234 -12.00 695.82", "5678 9012 SHOP"], ["4929", "9012"]),
    "card 12+4": (["03 Oct 2026 CARD 4929 1234 5678 -12.00 695.82", "9012 SHOP"],
                  ["4929", "9012"]),
    "ref 5+5": (["03 Oct 2026 MANDATE 12345 -12.00 695.82", "67890 GYM"], ["12345", "67890"]),
    "no amount on the first line": (["03 Oct 2026 TO 20-11-33 8765", "4321 RENT -250.00 707.82"],
                                    ["8765", "4321"]),
    "three lines": (["03 Oct 2026 REF 123 -12.00 695.82", "456 789", "012 GYM"],
                    ["123", "456", "789", "012"]),
}  # fmt: skip
_TRAILING_FIGURES = re.compile(r"(?:\s+(?:-?£?\d[\d,]*\.\d{2}|CR|DR))+$")


def _sent_wrap(lines: list[str], kind: str = "pdf") -> list[str]:
    page = [*WRAP_HEAD, *lines, "05 Oct 2026 Little Cafe -3.50 692.32"]
    doc = textprep_pages([page], sha256="x", kind=kind, names=NAMES)
    sent = sent_lines(doc)
    wanted = {f"P1L{len(WRAP_HEAD) + 1 + k}" for k in range(len(lines))}
    return [sent[r].text for r in doc.data_refs if r in wanted]


def textprep_pages(*args, **kwargs):
    from tuppence.ingest.textprep import pages_document

    return pages_document(*args, **kwargs)


@pytest.mark.parametrize("name", list(WRAPS))
def test_a_number_split_by_a_line_wrap_is_masked_in_both_parts(name):
    lines, secrets = WRAPS[name]
    sent = _sent_wrap(lines)
    assert sent  # masked, not withheld (a line without an amount may be withheld whole)
    flat = " ".join(sent)
    assert [s for s in secrets if s in flat] == [], sent
    # the oracle over the description read across the wrap
    printed = " ".join(_TRAILING_FIGURES.sub("", line) for line in lines)
    shown = " ".join(_TRAILING_FIGURES.sub("", line) for line in sent)
    assert survivors(printed, shown) == [], sent


def test_a_wrap_leaves_short_numbers_and_dates_on_either_side_alone():
    """Only a number that is long once joined across the wrap is masked: a store number and a
    continuation, a row after a row, a date at the start of the next line stay as printed."""
    for lines in (
        ["03 Oct 2026 TESCO STORES 2345 -12.50 695.82", "3 MOBILE TOP UP"],
        ["03 Oct 2026 TESCO STORES 2345 -12.50 695.82", "04 Oct 2026 B&Q 0123 -45.00 650.82"],
        ["03 Oct 2026 PAYPAL *EBAY 12345 -9.99 685.83", "PURCHASE"],
    ):
        assert _sent_wrap(lines) == lines


def _fuzz_wrap(seed: int, count: int = 200) -> list[tuple[list[str], str]]:
    """A random number of six to sixteen digits split across a wrap at a random point, with
    or without the amount and balance on the first line."""
    rnd = random.Random(seed)
    out = []
    for _ in range(count):
        number = "".join(rnd.choice("0123456789") for _ in range(rnd.randint(6, 16)))
        cut = rnd.randint(1, len(number) - 1)
        first, second = number[:cut], number[cut:]
        lead = rnd.choice(["TO 20-11-33 ", "REF ", "CARD ", "FPO J SMITH ", ""])
        figures = rnd.choice([" -250.00 707.82", " -12.00", ""])
        tail = rnd.choice([" RENT", " GYM", ""])
        last = " -1.00 706.82" if not figures else ""
        # a separator at the break (re-review 3 M2): "8765-" / "4321", "8765" / "(4321)"
        end, start, close = rnd.choice([("", "", ""), ("", "", ""), ("-", "", ""), ("", "-", ""),
                                        ("", "/", ""), ("", "(", ")"), ("/", "", "")])  # fmt: skip
        out.append(([f"03 Oct 2026 {lead}{first}{end}{figures}",
                     f"{start}{second}{close}{tail}{last}"], number))  # fmt: skip
    return out


def test_no_number_split_by_a_wrap_survives():
    leaked = []
    for lines, number in _fuzz_wrap(0) + _fuzz_wrap(1):
        sent = _sent_wrap(lines)
        printed = " ".join(_TRAILING_FIGURES.sub("", line) for line in lines)
        shown = " ".join(_TRAILING_FIGURES.sub("", line) for line in sent)
        if survivors(printed, shown) or number[-4:] in " ".join(sent):
            leaked.append((lines, sent))
    if leaked:
        pytest.fail(f"{len(leaked)} wrapped numbers leak, e.g. {leaked[:3]}")


# The coordinator's probe after 5a1b63a (p3): separators outside any list, and a card ending
# spaced a digit at a time after its label. The join is structural now: one to three characters
# of punctuation, symbols or spaces.
COORDINATOR = [
    "ACC 8765 _ 4321 10.00", "ACC 8765~4321 10.00", "ACC 8765 | 4321 10.00",
    "ACC 8765 · 4321 10.00", "ACC 8765 ; 4321 10.00", "ACC 8765 — 4321 10.00",
    "ACC 8765 - 4321 10.00", "ACC 8765 / 4321 10.00", "ACC NO 8765,4321 10.00",
    "ACC 8765+4321 10.00", "ACC 8765#4321 10.00", "ACC 8765\\4321 10.00", "ACC 8765:4321 10.00",
    "TO 8765 ~~4321 RENT 10.00", "TO 8765'4321 RENT 10.00", "TO 8765() 4321 RENT 10.00",
]  # fmt: skip
LABELLED = [
    "CARD ENDING IN 9 0 1 2 5.00", "CARD ENDING 9 0 1 2 5.00", "CARD NO 9 0 1 2 5.00",
    "A/C 9 0 1 2 5.00", "ACC 9-0-1-2 5.00", "ENDING IN: 9012 5.00", "CARD #9012 5.00",
    "03/10/2026 CARD 9012 TESCO -5.00", "03/10/2026 NO. 9012 -5.00",
]  # fmt: skip


@pytest.mark.parametrize("line", COORDINATOR)
def test_any_short_separator_joins_a_number(line):
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is not None and "8765" not in out.text and "4321" not in out.text, out
    assert survivors(line, out.text) == []


@pytest.mark.parametrize("line", LABELLED)
def test_the_digits_after_a_card_or_account_label_are_masked_however_spaced(line):
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is not None, line
    assert not re.search(r"9\D{0,3}0\D{0,3}1\D{0,3}2", out.text), out.text


def test_one_rule_for_a_sort_code_shaped_date_at_the_start_whatever_its_separator():
    """A leading date with a two-digit year is the row's own only when no number of four or
    more digits is joined after it: "20/11/33 87654321" is a sort code and an account number,
    like "20.11.33 87654321" and "20-11-33 87654321". Alone, or before words, it is the date."""
    for sep in "/.- ":
        joined = sensitive.prepare_outbound(f"20{sep}11{sep}33 87654321 RENT 10.00")
        assert joined is not None and joined.text == "[hidden-a] RENT 10.00", (sep, joined)
    for line in ("20/11/33 RENT 10.00", "20.11.33 RENT 10.00", "20 11 33 RENT 10.00"):
        out = sensitive.prepare_outbound(line)
        assert out is not None and out.text == line
    for line in (
        "03/10/2026 87654321 RENT 10.00",
        "2026-10-03 87654321 RENT 10.00",
        "02/10/26 20-11-33 87654321 10.00",
    ):
        out = sensitive.prepare_outbound(line)
        assert out is not None and "87654321" not in out.text
        assert out.text.startswith(line.split(" 87654321")[0].split(" 20-11-33")[0]), out.text
    # A named date's four-digit "year" with a number joined to it is part of the number
    # (R-M3-25 (5)): the row's date is "03 Oct", the rest is restored on this device.
    out = sensitive.prepare_outbound("03 Oct 2026 87654321 RENT 10.00")
    assert out is not None and out.text == "03 Oct [hidden-a] RENT 10.00"


def _separators() -> list[str]:
    """Every punctuation, symbol and space character that is itself once the line is folded
    (NFKC: a character that folds to letters or digits is not a separator any more), and the
    invisible characters dropped before reading."""
    import sys
    import unicodedata

    chars = []
    for code in range(sys.maxunicode + 1):
        ch = chr(code)
        if unicodedata.category(ch)[0] not in "PSZ":
            continue
        folded = unicodedata.normalize("NFKC", ch)
        if folded == ch or (folded.strip() == "" and folded != ""):
            chars.append(ch)
    return [*chars, "​", "‌", "‍", "⁠", "﻿"]


def _is_money(joined: str) -> bool:
    """The test's own reading of a money token: a currency sign before digits, or digits with a
    point and exactly two decimals after them."""
    import unicodedata

    for k, ch in enumerate(joined):
        if unicodedata.category(ch) == "Sc" and re.match(r"\s?\d", joined[k + 1 :]):
            return True
    return re.search(r"(?<!\d)\d+\.\d{2}(?!\d)", joined) is not None


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_two_groups_joined_by_any_short_separator_are_masked(seed):
    """The coordinator's property: two groups of two to six digits, six or more together, with
    one to three punctuation, symbol, space or invisible characters between them. No digit of
    either survives, wherever the pair is, unless it is a money token."""
    rnd = random.Random(seed)
    separators = _separators()
    leaked = []
    for _ in range(1500):
        a = "".join(rnd.choice("0123456789") for _ in range(rnd.randint(2, 6)))
        b = "".join(rnd.choice("0123456789") for _ in range(rnd.randint(max(2, 6 - len(a)), 6)))
        sep = "".join(rnd.choice(separators) for _ in range(rnd.randint(1, 3)))
        joined = f"{a}{sep}{b}"
        if _is_money(joined):
            continue
        forms = ["03/10/2026 TO {} RENT -12.00", "TO {} RENT -12.00", "03 Oct 2026 REF {} -1.00",
                 "{} RENT -12.00"]  # fmt: skip
        line = rnd.choice(forms).format(joined)
        out = sensitive.prepare_outbound(line, names=NAMES)
        if out is None:
            continue  # withheld whole: nothing of it is sent
        rest = out.text.removeprefix("03/10/2026").removeprefix("03 Oct 2026")
        rest = re.sub(r"-\d+\.\d{2}$", "", rest)
        if any(ch.isdigit() for ch in rest):
            leaked.append((line, out.text))
    if leaked:
        pytest.fail(f"{len(leaked)} joined pairs leak a digit, e.g. {leaked[:5]}")


# Re-review 3 M1 (R-M3-25 (5)): a named date took the group of four digits after it for its
# year, whatever the group was, so "02 Oct 8765 4321" sent an account number whole. A named date
# takes a four-digit group as its year only when it is 1990-2099 and no other group of digits is
# joined to it (a second date may follow). Both variants: spaced, and glued or dashed.
NAMED_YEAR = [
    "02 Oct 8765 4321 RENT 250.00", "02 Oct 8765-4321 RENT 250.00", "02 Oct 8765 4321 250.00",
    "2nd Oct 8765 4321 J SMITH 250.00", "Mon 2 Oct 8765 4321 -£250.00",
    "02-Oct-8765-4321 RENT 250.00", "02Oct8765 4321 RENT 250.00",
    "02 Oct 2026 8765 4321 RENT 250.00", "02 OCT 2011 3387 6543 21 250.00",
    "02 Oct 2011-33 87654321 250.00", "02 Oct 1234 5678 RENT 1.00",
]  # fmt: skip
NAMED_KEPT = [
    "02 Oct 2026 TESCO STORES 12.50", "02 Oct 2026 03 Oct 2026 TESCO STORES 12.50",
    "2 Oct 2026 TESCO STORES 2345 12.50", "02-Oct-2026 TESCO 12.50", "02Oct2026 TESCO 12.50",
    "02 Oct 1999 TESCO 12.50", "Mon 2 Oct 2026 LITTLE CAFE -£3.40", "02 Oct 2026 12.50",
]  # fmt: skip


@pytest.mark.parametrize("line", NAMED_YEAR)
def test_a_named_dates_year_is_never_a_group_of_an_account_number(line):
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is not None
    assert "8765" not in out.text and "4321" not in out.text and "5678" not in out.text, out
    assert "6543" not in out.text and "87654321" not in out.text, out
    assert survivors(line, out.text) == [], out.text


@pytest.mark.parametrize("line", NAMED_KEPT)
def test_a_named_dates_real_year_is_kept(line):
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is not None and out.text == line


def test_the_oracle_reads_a_named_dates_year_the_same_way():
    """The guard's own reading (`digit_runs`), written apart: the account number is a run."""
    assert runs("02 Oct 8765 4321 RENT 250.00") == ["87654321"]
    assert runs("02-Oct-8765-4321 RENT 250.00") == ["87654321"]
    assert runs("02 Oct 2026 8765 4321 RENT 250.00") == ["202687654321"]
    assert runs("02 Oct 2026 03 Oct 2026 TESCO 12.50") == []
    assert runs("02 Oct 2026 TESCO 12.50") == []


def test_the_guard_catches_a_named_dates_year_read_from_any_group(monkeypatch):
    """The control: with any group of four digits after a named month read as its year, the
    account number goes out and the guard says so."""
    monkeypatch.setattr(sensitive, "_named_year", lambda view, year, part: True)
    leaked = [line for line in NAMED_YEAR[:7] if survivors(line, _outbound(line))]
    assert len(leaked) >= 5


def _outbound(line: str) -> str:
    out = sensitive.prepare_outbound(line, names=NAMES)
    return "" if out is None else out.text


# Re-review 3 M3 (R-M3-25 (6)): card-ending label forms. "_" is a word character, so
# "ENDING_9012" had no word boundary; "ENDING WITH 9012" had a word between.
CARD_LABEL_FORMS = [
    "03/10/2026 CARD ENDING_9012 -10.00", "03/10/2026 CARD ENDING WITH 9012 -10.00",
    "03/10/2026 CARD ENDING WITH: 9012 -10.00", "03/10/2026 ENDING_IN_9012 -10.00",
    "03/10/2026 CARD_9012 TESCO -10.00", "03/10/2026 VISA ENDING WITH 9012 -10.00",
]  # fmt: skip


@pytest.mark.parametrize("line", CARD_LABEL_FORMS)
def test_a_card_ending_after_an_underscore_or_with_is_masked(line):
    out = sensitive.prepare_outbound(line, names=NAMES)
    assert out is None or "9012" not in out.text, out
    assert sensitive.classify(line, names=NAMES), line


# Re-review 3 M2 (R-M3-25 (6)): a number split by a line wrap with its separator at the break,
# or a card label at the end of the line above. The reader got the last four digits.
WRAP_SEPARATORS = {
    "separator at the end": (["03 Oct 2026 FPO J SMITH 20-11-33 8765- -250.00 707.82", "4321 RENT"],
                             ["8765", "4321"]),
    "separator at the start": (["03 Oct 2026 FPO J SMITH 20-11-33 8765 -250.00 707.82",
                                "-4321 RENT"], ["8765", "4321"]),
    "slash at the start": (["03 Oct 2026 FPO J SMITH 20-11-33 8765 -250.00 707.82", "/4321 RENT"],
                           ["8765", "4321"]),
    "bracketed": (["03 Oct 2026 FPO J SMITH 20-11-33 8765 -250.00 707.82", "(4321) RENT"],
                  ["8765", "4321"]),
    "card label at the end": (["03 Oct 2026 PAYMENT CARD ENDING -25.00 425.00", "9012 SHOP"],
                              ["9012"]),
    "no amount on the first line, separator": (["03 Oct 2026 TO 20-11-33 8765-",
                                                "4321 RENT -250.00 707.82"], ["8765", "4321"]),
    "no amount on the first line, card label": (["03 Oct 2026 PAYMENT CARD ENDING",
                                                 "9012 SHOP -25.00 425.00"], ["9012"]),
}  # fmt: skip


@pytest.mark.parametrize("name", list(WRAP_SEPARATORS))
def test_a_wrap_with_its_separator_or_label_at_the_break_is_masked(name):
    lines, secrets = WRAP_SEPARATORS[name]
    sent = _sent_wrap(lines)
    flat = " ".join(sent)
    assert [s for s in secrets if s in flat] == [], sent
