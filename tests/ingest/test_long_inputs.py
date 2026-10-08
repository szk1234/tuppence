"""Re-review N1 and N2: the parse and check steps take time in proportion to what they read.

Structured files (CSV, OFX, QIF) reach Check with their lines at full length, and a text file
may hold any number of withheld lines between rows or above the table. Each reviewer probe is
here, at a size where the quadratic code took seconds or minutes."""

from __future__ import annotations

import datetime as dt
import time

from ingest.helpers import add_account, drain
from tuppence.ingest import balances, check, textprep
from tuppence.ingest.balances import repair_signs
from tuppence.ingest.check import check_document
from tuppence.ingest.importers.ofx import ofx_document, parse_ofx
from tuppence.ingest.models import ParsedRow, ParsedStatement

BOUND = 3.0  # seconds: linear code takes a fraction of this, the old code minutes


def _took(fn, *args, **kwargs) -> float:
    started = time.perf_counter()
    fn(*args, **kwargs)
    return time.perf_counter() - started


def test_figures_reads_a_long_run_of_comma_groups_quickly():
    """N1: `_grouped_start` rescanned the remaining groups for each start (64 KB took 6 s)."""
    for line in ("000," * 64_000 + "12", "1," * 128_000 + "12", ",".join(["1234"] * 50_000)):
        assert _took(check.figures, line) < BOUND


def test_figures_reads_a_long_line_of_many_figures_quickly():
    """Each figure looked at the whole line before it (`_at_cell_edge` sliced the text)."""
    for line in ("1 " * 250_000, "1.00 " * 200_000, "£1 " * 250_000, ",1," * 250_000):
        assert _took(check.figures, line) < BOUND


def test_sign_checks_on_a_long_line_with_many_figures_are_quick():
    line = "02/10/2026 Shop " + "5.00 DR " * 100_000
    doc = textprep.text_document("x", sha256="x")
    doc.lines = [doc.lines[0].model_copy(update={"text": line, "ref": "L1"})]
    doc.data_refs = ["L1"]
    for perspective in ("household", "card"):
        parsed = ParsedStatement(
            importer="ai-read",
            perspective=perspective,
            period_start=dt.date(2026, 10, 1),
            period_end=dt.date(2026, 10, 31),
            rows=[_row("L1", -500, "5.00")],
        )
        assert _took(check_document, doc, parsed) < BOUND


def test_ofx_with_a_long_comma_memo_is_checked_quickly(fixtures):
    """The reviewer's OFX probe: a 32 KB MEMO took 3 s to check, 0.00 s at 75f4474."""
    base = (fixtures / "ofx" / "current.ofx").read_text()
    text = base.replace("<MEMO>OCT WAGES", "<MEMO>" + "000," * 64_000 + "12")
    doc, parsed = ofx_document(text, sha256="x"), parse_ofx(text)
    assert _took(check_document, doc, parsed) < BOUND


def test_a_csv_row_with_a_long_comma_description_imports_quickly(ingest_env, fixtures):
    """The reviewer's Monzo probe: one 32 KB description took 3.2 s end to end."""
    services, _ = ingest_env
    account = add_account(services, "monzo", "current", "Monzo")
    head, *rows = (fixtures / "csv" / "monzo.csv").read_text().split("\n", 1)
    long = "000," * 64_000 + "12"
    row = (
        f'tx_syn_999,02/10/2026,01:15:00,Card payment,"{long}",,Groceries,-5.00,GBP,-5.00,GBP,'
        ",,,,,-5.00,\n"
    )
    started = time.perf_counter()
    out = services.ingest.upload("long.csv", (head + "\n" + rows[0] + row).encode())
    drain(services)
    record = services.statements.get(out.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(out.record.id)
    assert time.perf_counter() - started < 4 * BOUND
    assert record.status in ("imported", "needs_review")


def _row(ref: str, pence: int, text: str, balance: int | None = None) -> ParsedRow:
    return ParsedRow(
        ref=ref,
        date=dt.date(2026, 10, 2),
        amount_pence=pence,
        amount_text=text,
        raw_description="Shop",
        balance_after_pence=balance,
    )


def test_sign_repair_with_many_withheld_lines_between_rows_is_quick():
    """N2: each withheld line between rows rebuilt the whole line index (20k took 33 s)."""
    text = (
        "Date Description Amount\n02/10/2026 Shop -4.00\n"
        + "Sort code 12-34-56\n" * 20_000
        + "03/10/2026 Cafe -3.00\n"
    )
    doc = textprep.text_document(text, sha256="x")
    parsed = ParsedStatement(importer="ai-read", rows=[_row(doc.data_refs[1], -400, "-4.00")])
    assert _took(repair_signs, doc, parsed, opening=None, level="full") < BOUND


def test_sign_repair_of_many_rows_between_printed_balances_is_quick():
    """Each repaired row looked for its column label in the whole page again."""
    rows = [f"0{1 + i % 9}/10/2026 Shop {i} 1.00 {1000 + i}.00" for i in range(20_000)]
    doc = textprep.pages_document(
        [["Date Description Paid out Paid in Balance", *rows]], sha256="x", kind="pdf"
    )
    data = [r for r in doc.data_refs if r != "P1L1"]
    parsed = ParsedStatement(
        importer="ai-read",
        rows=[
            _row(ref, -100, "1.00", (1000 + i) * 100) for i, ref in enumerate(data)
        ],  # read the wrong way round: each is repaired from the balances either side
    )
    assert _took(repair_signs, doc, parsed, opening=99_900, level="full") < BOUND


def test_local_balances_with_many_label_lines_above_the_table_is_quick():
    """N2: each label line looked for the line below it with a linear scan (20k took 10 s)."""
    text = (
        "Example Bank\n"
        + "Opening balance\n" * 20_000
        + "Date Description Amount\n02/10/2026 Shop -4.00\n03/10/2026 Cafe -3.00\n"
    )
    doc = textprep.text_document(text, sha256="x")
    assert _took(balances.local_balances, doc, perspective="household") < BOUND


def test_text_with_long_comma_lines_is_checked_quickly():
    """N2 (also): the model's balances were looked for on every data line with the quadratic
    figure reader (0.14 s a line); all of a statement's checks are now quick on such lines."""
    from tuppence.ingest.verify import Basis, verify

    line = "02/10/2026 X " + "000," * 2_400 + "12 -4.00"
    doc = textprep.text_document("Date Description Amount\n" + (line + "\n") * 500, sha256="x")
    parsed = ParsedStatement(
        importer="ai-read",
        closing_balance_pence=123_456,
        rows=[_row(ref, -400, "-4.00") for ref in doc.data_refs[1:]],
    )
    assert _took(verify, doc, parsed, Basis(account_kind="current"), level="full") < BOUND


def test_planning_duplicates_of_one_amount_on_one_day_is_quick():
    """(d): rows a crafted file makes all alike (one amount, one day) can't make dedupe slow,
    whether their descriptions match the stored rows or not."""
    from tuppence.ingest.dedupe import Existing, assign_fingerprints, plan_dedupe

    day = dt.date(2026, 10, 2)
    for stored_text in ("SHOP", "OTHER PLACE"):
        rows = [_row(f"L{i}", -500, "5.00") for i in range(20_000)]
        existing = [Existing(f"t{i}", day, -500, stored_text, f"fp{i}") for i in range(20_000)]
        fps = [fp for fp, _ in assign_fingerprints(rows, "a")]
        started = time.perf_counter()
        plan = plan_dedupe(rows, fps, existing, window=(day, day))
        assert time.perf_counter() - started < BOUND
        assert len(plan.similar) == (20_000 if stored_text == "SHOP" else 0)
