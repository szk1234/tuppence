from datetime import date

from tuppence.ingest.check import (
    amount_renderings,
    balance_verified,
    base_ref,
    check_document,
    check_rows,
    check_statement,
)
from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement, SkippedLine

HEADER = Line(ref="L1", text="Date,Description,Paid out,Paid in,Balance")


def doc(*lines: str, header: bool = True) -> Document:
    body = [Line(ref=f"L{i}", text=t) for i, t in enumerate(lines, start=2)]
    return Document(
        kind="csv",
        sha256="x",
        lines=[HEADER, *body] if header else body,
        header_refs=["L1"] if header else [],
        data_refs=[ln.ref for ln in body],
    )


def row(ref: str, day: int, pence: int, text: str, **kw) -> ParsedRow:
    return ParsedRow(
        ref=ref,
        date=date(2026, 10, day),
        amount_pence=pence,
        amount_text=text,
        raw_description=kw.pop("desc", "Shop"),
        **kw,
    )


def statement(
    *rows: ParsedRow, skipped=(), perspective="household", opening=None, closing=None
) -> ParsedStatement:
    return ParsedStatement(
        importer="test",
        perspective=perspective,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
        rows=list(rows),
        skipped=list(skipped),
        opening_balance_pence=opening,
        closing_balance_pence=closing,
    )


def test_renderings_cover_short_long_and_grouped_forms():
    assert amount_renderings(-123450) == {"1234.5", "1234.50", "1,234.5", "1,234.50"}
    assert amount_renderings(500) == {"5", "5.00"}
    assert base_ref("L12#fee") == "L12"


def test_a_clean_file_passes():
    d = doc("01/10/2026,Shop,42.18,,957.82", "17/10/2026,Acme Payroll,,1650.00,2607.82")
    p = statement(
        row("L2", 1, -4218, "42.18", sign_from="Paid out", balance_after_pence=95782),
        row("L3", 17, 165000, "1650.00", sign_from="Paid in", balance_after_pence=260782),
        opening=100000,
        closing=260782,
    )
    assert check_document(d, p) == []
    assert balance_verified(p, [], "full")


def test_amount_must_be_on_its_line_and_the_same_size():
    d = doc("01/10/2026,Shop,-42.18")
    errors = check_rows(
        d.lines,
        all_lines=d.lines,
        context_refs=["L1"],
        data_refs=d.data_refs,
        parsed=statement(row("L2", 1, -4281, "-42.81")),
    )
    assert 'L2: amount_text "-42.81" not found on line' in errors
    errors = check_rows(
        d.lines,
        all_lines=d.lines,
        context_refs=["L1"],
        data_refs=d.data_refs,
        parsed=statement(row("L2", 1, -4200, "-42.18")),
    )
    assert 'L2: amount_text "-42.18" is 42.18, amount is -42.00' in errors


def test_printed_minus_must_be_kept_on_a_current_account():
    d = doc("01/10/2026,Shop,-42.18")
    errors = check_document(d, statement(row("L2", 1, 4218, "-42.18")))
    assert "L2: sign mismatch (line shows -42.18, amount is 42.18)" in errors


def test_unsigned_money_out_needs_a_label_from_the_headings():
    d = doc("01/10/2026,Shop,42.18,,957.82")
    assert "L2: sign mismatch (line shows 42.18, amount is -42.18)" in check_document(
        d, statement(row("L2", 1, -4218, "42.18"))
    )
    assert check_document(d, statement(row("L2", 1, -4218, "42.18", sign_from="Paid out"))) == []
    wrong = check_document(d, statement(row("L2", 1, -4218, "42.18", sign_from="Paid in")))
    assert 'L2: sign mismatch (sign_from "Paid in" requires positive, amount is -42.18)' in wrong
    missing = check_document(d, statement(row("L2", 1, -4218, "42.18", sign_from="Withdrawn")))
    assert 'L2: sign_from "Withdrawn" not on header' in missing


def test_card_prints_purchases_positive_and_credits_with_cr():
    d = doc(
        "02/10/2026,Greenbasket,64.20",
        "12/10/2026,Refund,6.15 CR",
        "28/10/2026,Payment,-150.00",
        header=False,
    )
    good = statement(
        row("L2", 2, -6420, "64.20"),
        row("L3", 12, 615, "6.15 CR"),
        row("L4", 28, 15000, "-150.00"),
        perspective="card",
        opening=84216,
        closing=84216 + 6420 - 615 - 15000,
    )
    assert check_document(d, good) == []
    bad = statement(
        row("L2", 2, 6420, "64.20"),
        row("L3", 12, -615, "6.15 CR"),
        row("L4", 28, -15000, "-150.00"),
        perspective="card",
    )
    errors = check_document(d, bad)
    assert "L2: sign mismatch (card statement shows 64.20, amount is 64.20)" in errors
    assert "L3: sign mismatch (card statement shows 6.15 CR, amount is -6.15)" in errors
    assert "L4: sign mismatch (card statement shows -150.00, amount is -150.00)" in errors


def test_every_data_line_exactly_once():
    d = doc("01/10/2026,A,-1.00", "02/10/2026,B,-2.00", "03/10/2026,C,-3.00", "04/10/2026,D,-4.00")
    p = statement(
        row("L2", 1, -100, "-1.00"),
        row("L2", 1, -100, "-1.00"),
        row("L1", 1, -100, "-1.00"),
        row("L9", 1, -100, "-1.00"),
        skipped=[SkippedLine(ref="L4", reason="")],
    )
    errors = check_document(d, p)
    assert "L2: duplicate ref" in errors
    assert "L1: header line included" in errors
    assert "unexpected ref: L9" in errors
    assert "missing refs: L3, L5" in errors
    assert "L4: skipped without reason" in errors


def test_one_line_may_give_two_rows():
    d = doc("20/10/2026,Northline Rail,-28.90,0.50", header=False)
    p = statement(row("L2#fee", 20, -50, "0.50"), row("L2", 20, -2890, "-28.90"))
    assert check_document(d, p) == []


def test_dates_must_sit_inside_the_period_give_or_take_three_days():
    d = doc("05/11/2026,Shop,-4.00", "03/11/2026,Shop,-3.00", header=False)
    errors = check_document(
        d,
        statement(
            row("L2", 1, -400, "-4.00").model_copy(update={"date": date(2026, 11, 5)}),
            row("L3", 1, -300, "-3.00").model_copy(update={"date": date(2026, 11, 3)}),
        ),
    )
    assert errors == ["L2: date 2026-11-05 outside 2026-10-01..2026-10-31 (±3 days)"]


def test_period_must_make_sense():
    p = statement().model_copy(
        update={"period_start": date(2026, 11, 1), "period_end": date(2026, 10, 1)}
    )
    assert "period_start 2026-11-01 is after period_end 2026-10-01" in check_rows(
        [], all_lines=[], context_refs=[], data_refs=[], parsed=p
    )
    p = statement().model_copy(update={"period_start": None})
    assert "statement period_start missing" in check_rows(
        [], all_lines=[], context_refs=[], data_refs=[], parsed=p
    )


def test_balances_household_and_card():
    p = statement(row("L2", 1, -4218, "-42.18"), opening=100000, closing=95700)
    assert check_statement(p) == [
        "balance mismatch: opening 1000.00 + sum -42.18 = 957.82, closing 957.00"
    ]
    card = statement(row("L2", 1, -4218, "42.18"), perspective="card", opening=84216, closing=88434)
    assert check_statement(card) == []
    assert not balance_verified(statement(row("L2", 1, -4218, "-42.18")), [], "full")


def test_running_balance_must_be_continuous():
    p = statement(
        row("L2", 1, -4218, "-42.18", balance_after_pence=95782),
        row("L3", 3, -4820, "-48.20", balance_after_pence=90980),
        opening=100000,
    )
    assert check_statement(p) == [
        "L3: running balance mismatch (previous 957.82 + amount -48.20 = 909.62, got 909.80)"
    ]


def test_screenshots_only_need_the_amount_on_the_line():
    d = Document(
        kind="image",
        sha256="x",
        lines=[Line(ref="P1L1", text="Mon 5 Oct   Little Cafe   £3.40")],
        data_refs=["P1L1", "P1L2"],
    )
    p = ParsedStatement(
        importer="ai-read", rows=[row("P1L1", 5, -340, "£3.40")]
    )  # sign not checked, no period
    assert check_document(d, p, level="screenshot") == []
    wrong = ParsedStatement(importer="ai-read", rows=[row("P1L1", 5, -430, "£4.30")])
    assert check_document(d, wrong, level="screenshot") == [
        'P1L1: amount_text "£4.30" not found on line'
    ]
    assert not balance_verified(p, [], "screenshot")


def test_rows_edited_by_the_person_skip_the_line_checks():
    d = doc("01/10/2026,Shop,-42.18")
    p = statement(row("L2", 1, -4300, "-42.18", edited=True))
    assert check_document(d, p) == []


def test_statement_level_date_check_when_asked():
    p = statement(row("L2", 1, -100, "-1.00").model_copy(update={"date": date(2026, 12, 1)}))
    assert check_statement(p) == []
    assert check_statement(p, dates=True) == [
        "L2: date 2026-12-01 outside 2026-10-01..2026-10-31 (±3 days)"
    ]


def test_page_labels_come_from_the_same_page():
    lines = [
        Line(ref="P1L1", text="Date Description Paid out Paid in Balance"),
        Line(ref="P1L2", text="01 Oct 2026   Shop   42.18   957.82"),
        Line(ref="P2L1", text="02 Oct 2026   Cafe   3.40   954.42"),
    ]
    d = Document(kind="pdf", sha256="x", lines=lines, data_refs=["P1L1", "P1L2", "P2L1"])
    p = statement(
        row("P1L2", 1, -4218, "42.18", sign_from="Paid out"),
        row("P2L1", 2, -340, "3.40", sign_from="Paid out"),
        skipped=[SkippedLine(ref="P1L1", reason="column headings")],
    )
    assert check_document(d, p) == ['P2L1: sign_from "Paid out" not on page']


# --- R-M3-6: NaN amounts and UK sign notations -----------------------------------------


def test_non_finite_amount_text_is_a_row_error_not_a_crash():
    for text in ("NaN", "Infinity", "-Infinity", "sNaN"):
        d = doc(f"01/10/2026,Shop,{text}")
        errors = check_document(d, statement(row("L2", 1, -1230, text)))
        assert f'L2: amount_text "{text}" is not a number' in errors


NOTATIONS = [
    # (printed figure, household amount, card amount)
    ("(12.30)", -1230, 1230),
    ("12.30-", -1230, 1230),
    ("12.30 DR", -1230, -1230),
    ("12.30,DR", -1230, -1230),
    ("12.30 CR", 1230, 1230),
    ("12.30,CR", 1230, 1230),
]


def test_uk_sign_notations_in_both_perspectives():
    for printed, household, card in NOTATIONS:
        d = doc(f"02/10/2026,Shop,{printed},100.00", header=False)
        for perspective, amount in (("household", household), ("card", card)):
            ok = statement(row("L2", 2, amount, printed), perspective=perspective)
            assert check_document(d, ok) == [], (printed, perspective)
            bad = statement(row("L2", 2, -amount, printed), perspective=perspective)
            assert any("sign mismatch" in e for e in check_document(d, bad)), (printed, perspective)


def test_dr_and_cr_are_sign_labels():
    p = statement(
        row("L2", 2, -1230, "12.30", sign_from="DR"), row("L3", 3, 500, "5.00", sign_from="CR")
    )
    d2 = doc("02/10/2026,Shop,12.30,DR", "03/10/2026,Refund,5.00,CR")
    d2.lines[0] = Line(ref="L1", text="Date,Description,Amount,DR,CR")
    assert check_document(d2, p) == []
    wrong = statement(row("L2", 2, 1230, "12.30", sign_from="DR"))
    assert any("requires negative" in e for e in check_document(d2, wrong))


def test_only_split_out_fee_rows_skip_the_plain_charge_sign_check():
    d = doc("01/10/2026,Monthly fee,5.00,,995.00")
    # A row merely typed "fee" (from a CSV Type column or the reader) is still sign-checked.
    typed = check_document(d, statement(row("L2", 1, -500, "5.00", bank_type="fee")))
    assert "L2: sign mismatch (line shows 5.00, amount is -5.00)" in typed
    # Split-out fee rows ("L2#fee") are covered by test_one_line_may_give_two_rows.


def test_an_absurd_amount_is_reported():
    big = row("L2", 1, -100_000_000_001, "1,000,000,000.01")
    d = doc("01/10/2026,Shop,1000000000.01,,0.00")
    errors = check_rows(
        d.lines,
        all_lines=d.lines,
        context_refs=d.header_refs,
        data_refs=d.data_refs,
        parsed=statement(big),
    )
    assert any("larger than any real transaction" in e for e in errors)
