"""R-M3-23 (a), re-review N3 and m3: inside a line sent to the AI reader, every run of six or
more digits that isn't an amount or a date is masked, however it is joined to a sort code or
glued to words, and card endings in their short forms are masked too. The guard, read by an
oracle written apart from the masking code: no run of six or more digits from the printed line
survives in anything sent."""

from __future__ import annotations

import json
import random

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


def test_a_group_that_reads_as_a_date_ends_a_run():
    """ "05 10 26" and "20.11.33" read as dates (a real day and month), so they are left as
    printed; an account number after one is masked on its own."""
    plain = sensitive.prepare_outbound("05 10 26 HOMEWARE DIRECT 43.27")
    assert plain is not None and plain.text == "05 10 26 HOMEWARE DIRECT 43.27"
    for line in ("03/10/2026 TO 20.11.33 87654321 -1.00", "03/10/2026 TO 20 11 33 87654321 -1.00"):
        out = sensitive.prepare_outbound(line)
        assert out is not None and "8765" not in out.text and "[hidden-a]" in out.text


def _corpus() -> list[str]:
    from ingest.test_balance_lines import BALANCES, ROWS_STILL_SENT, UNSURE
    from ingest.test_sensitive_parity import EVERY, PLAIN, REGRESSION, WITH_DETAILS

    return list(dict.fromkeys([*EVERY, *PLAIN, *REGRESSION, *WITH_DETAILS, *BALANCES, *UNSURE,
                               *ROWS_STILL_SENT, *N3, *M3]))  # fmt: skip


def _fuzz(seed: int, count: int = 400) -> list[str]:
    """Rows with a random number joined to a random sort code, glued to random words, in any
    position: the guard holds for each."""
    rnd = random.Random(seed)
    joins = [" ", " / ", "/", ", ", " - ", "-", ":", "(", "", " REF ", " ACC", "·", "_"]
    words = ["TO", "FPO", "BGC", "RENT", "J SMITH", "SO", "TFR", "PAYMENT"]
    out = []
    for _ in range(count):
        sort = rnd.choice(["20-11-33", "201133", "20 11 33", "20.11.33", ""])
        number = "".join(rnd.choice("0123456789") for _ in range(rnd.randint(6, 16)))
        parts = [rnd.choice(words), sort, rnd.choice(joins) + number, rnd.choice(words)]
        rnd.shuffle(parts)
        amount = rnd.choice(["-250.00", "1,250.00", "£5", "12.30 1,000.00"])
        out.append(f"0{rnd.randint(1, 9)}/10/2026 {''.join(parts)} {amount}")
    return out


@pytest.mark.parametrize("source", ["corpus", "fuzz"])
def test_no_run_of_six_digits_survives_prepare_outbound(source):
    """The guard over every line the parity corpus holds and a fuzz of joined numbers: what
    `prepare_outbound` sends keeps no run of six digits from the printed line."""
    lines = _corpus() if source == "corpus" else _fuzz(0) + _fuzz(1)
    leaked = {}
    for line in lines:
        out = sensitive.prepare_outbound(line, names=NAMES)
        if out is not None and (bad := survivors(line, out.text)):
            leaked[line] = (out.text, bad)
    assert leaked == {}


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
    assert all(not survivors(line, sent) for line in STATEMENT.splitlines())
    stored = [t.raw_description for t in services.statements.transactions(record.id)]
    if record.status == "needs_review":
        stored = [r["raw_description"] for r in record.draft["parsed"]["rows"]]
    assert any("87654321" in d for d in stored) and any("41234567" in d for d in stored)
