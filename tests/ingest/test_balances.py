import pytest

from ingest.statements import HEAD, TABLE
from tuppence.ingest.balances import local_balances, repair_signs
from tuppence.ingest.check import check_statement
from tuppence.ingest.models import ParsedRow, ParsedStatement, SkippedLine
from tuppence.ingest.textprep import pages_document


def doc_with(*summary: str, rows=("01/10/2026 SHOP 5.00 95.00",)):
    return pages_document([[*HEAD, *summary, TABLE, *rows]], sha256="x", kind="pdf")


@pytest.mark.parametrize(
    ("lines", "perspective", "opening", "closing"),
    [
        (["Opening balance £1,000.00", "Closing balance £1,857.82"], "household", 100000, 185782),
        (
            ["Opening balance on 01/10/2026 £1,000.00", "Closing balance on 31/10/2026 £1,857.82"],
            "household",
            100000,
            185782,
        ),
        (
            [
                "Opening balance (on 1 Oct 2026) £1,000.00",
                "Closing balance at 31 October 2026 1,857.82",
            ],
            "household",
            100000,
            185782,
        ),
        (
            ["Opening balance 1,000.00 CR", "Closing balance 1,857.82 CR"],
            "household",
            100000,
            185782,
        ),
        (["Opening balance (100.00)", "Closing balance 20.00 DR"], "household", -10000, -2000),
        (["Opening balance 100.00-", "Closing balance -20.00"], "household", -10000, -2000),
        (
            ["Balance brought forward £500.00", "Balance carried forward £450.00"],
            "household",
            50000,
            45000,
        ),
        (["Balance b/f £500.00", "Balance c/f £450.00"], "household", 50000, 45000),
        (["BALANCE FORWARD 500.00", "Closing balance 450.00"], "household", 50000, 45000),
        (["Previous balance £842.16", "New balance £909.85"], "card", 84216, 90985),
        (["Previous balance 100.00 CR", "New balance 20.00 CR"], "card", -10000, -2000),
        (["Previous balance (100.00)", "New balance 20.00 DR"], "card", -10000, 2000),
        (["Opening balance £1,000.00   Closing balance £1,857.82"], "household", 100000, 185782),
        # overdrawn markers after the figure
        (["Opening balance 100.00 D", "Closing balance 65.00 D"], "household", -10000, -6500),
        (["Opening balance 100.00D", "Closing balance 65.00OD"], "household", -10000, -6500),
        (["Opening balance 100.00 OD", "Closing balance 65.00 O/D"], "household", -10000, -6500),
        (
            ["Opening balance 100.00 overdrawn", "Closing balance £65.00 (overdrawn)"],
            "household",
            -10000,
            -6500,
        ),
        (
            ["Opening balance 100.00 in credit", "Closing balance 65.00 Dr."],
            "household",
            10000,
            -6500,
        ),
        (
            ["Opening balance 1,000.00 Money in 900.00", "Closing balance 1,857.82 on 31/10/2026"],
            "household",
            100000,
            185782,
        ),
        # currency codes, no thousands separator, a footnote marker (R-M3-17)
        (
            ["Opening balance GBP 1,000.00", "Closing balance 1,857.82 GBP"],
            "household",
            100000,
            185782,
        ),
        (["Opening balance 1000.00", "Closing balance £1857.82"], "household", 100000, 185782),
        (
            ["Opening balance: GBP 1000.00 CR", "Closing balance 65.00 GBP D"],
            "household",
            100000,
            -6500,
        ),
        (["Opening balance 1,000.00*", "Closing balance 1,857.82 *"], "household", 100000, 185782),
        # the label on one line and its figure on the next
        (
            ["Opening balance", "£1,000.00", "Closing balance", "1,857.82"],
            "household",
            100000,
            185782,
        ),
        (["Opening balance on 1 Oct 2026", "100.00 D"], "household", -10000, None),
        # a row of labels above a row of figures is not a pair
        (
            [
                "Opening balance   Money in   Money out   Closing balance",
                "1,000.00 900.00 42.18 1,857.82",
            ],
            "household",
            None,
            None,
        ),
        # a word after the figure that isn't understood leaves the figure unread
        (["Opening balance 100.00 XQ", "Closing balance 65.00 Dx"], "household", None, None),
        (["Previous balance 100.00 D", "New balance 20.00 in credit"], "card", None, -2000),
    ],
)
def test_balance_phrasings_and_signs(lines, perspective, opening, closing):
    found = local_balances(doc_with(*lines), perspective=perspective)
    assert (found.opening, found.closing) == (opening, closing)


def test_conflicting_or_cluttered_figures_are_left_to_the_fallback():
    conflicting = doc_with("Opening balance £1,000.00", "Opening balance £900.00")
    assert local_balances(conflicting, perspective="household").opening is None
    cluttered = doc_with("Closing balance Money in 900.00 120.00")
    assert local_balances(cluttered, perspective="household").closing is None


def row(ref, pence, text, after, **kw):
    from datetime import date

    return ParsedRow(
        ref=ref,
        date=date(2026, 10, 1),
        amount_pence=pence,
        amount_text=text,
        raw_description="x",
        balance_after_pence=after,
        **kw,
    )


def statement(*rows, skipped=()):
    return ParsedStatement(importer="t", rows=list(rows), skipped=list(skipped))


def test_sign_comes_from_the_balance_difference():
    doc = pages_document(
        [[TABLE, "01/10/2026 ACME 900.00 1,900.00", "03/10/2026 SHOP 42.18 1,857.82"]],
        sha256="x",
        kind="pdf",
    )
    parsed = statement(row("P1L2", -90000, "900.00", 190000), row("P1L3", -4218, "42.18", 185782))
    result = repair_signs(doc, parsed, opening=100000, level="full")
    assert result.errors == [] and result.repaired == ["P1L2"]
    assert parsed.rows[0].amount_pence == 90000 and parsed.rows[0].sign_from == "Paid in"
    assert parsed.rows[1].amount_pence == -4218


def test_a_printed_sign_is_never_overruled():
    doc = pages_document([[TABLE, "01/10/2026 ACME -900.00 1,900.00"]], sha256="x", kind="pdf")
    parsed = statement(row("P1L2", -90000, "-900.00", 190000))
    assert repair_signs(doc, parsed, opening=100000, level="full").repaired == []
    assert parsed.rows[0].amount_pence == -90000


def test_a_brought_forward_line_gives_the_balance_before_the_first_row():
    doc = pages_document(
        [[TABLE, "Balance brought forward 1,000.00", "01/10/2026 ACME 900.00 1,900.00"]],
        sha256="x",
        kind="pdf",
    )
    parsed = statement(
        row("P1L3", -90000, "900.00", 190000), skipped=[SkippedLine(ref="P1L2", reason="bf")]
    )
    result = repair_signs(doc, parsed, opening=None, level="full")
    assert result.repaired == ["P1L3"] and parsed.rows[0].amount_pence == 90000


def test_an_unprovable_first_row_is_reported():
    doc = pages_document([[TABLE, "01/10/2026 ACME 900.00 1,900.00"]], sha256="x", kind="pdf")
    parsed = statement(row("P1L2", 90000, "900.00", 190000))
    result = repair_signs(doc, parsed, opening=None, level="full")
    assert result.repaired == [] and "can't tell whether" in result.errors[0]
    assert repair_signs(doc, parsed, opening=None, level="screenshot").errors == []


def day_end_doc(*rows):
    return pages_document([[TABLE, *rows]], sha256="x", kind="pdf")


def test_the_models_sign_is_never_carried_across_a_row_without_a_balance():
    # N1: the balance is printed once a day. The model has TO SAVINGS the wrong way round and
    # FROM SAVINGS right; carrying its sign forward would "repair" the right row.
    doc = day_end_doc(
        "01/10/2026 TO SAVINGS 50.00",
        "01/10/2026 FROM SAVINGS 50.00 1,000.00",
        "02/10/2026 SHOP 10.00 990.00",
    )
    parsed = statement(
        row("P1L2", 5000, "50.00", None),
        row("P1L3", 5000, "50.00", 100000),
        row("P1L4", -1000, "10.00", 99000),
    )
    result = repair_signs(doc, parsed, opening=100000, level="full")
    assert result.repaired == []
    assert [r.amount_pence for r in parsed.rows] == [5000, 5000, -1000]
    assert any("can't tell whether" in e and "P1L2" in e and "P1L3" in e for e in result.errors)


def test_rows_that_add_up_only_one_way_are_proved_by_a_later_balance():
    doc = day_end_doc("01/10/2026 SHOP 42.18", "01/10/2026 CAFE 3.40 954.42")
    parsed = statement(row("P1L2", -4218, "42.18", None), row("P1L3", -340, "3.40", 95442))
    result = repair_signs(doc, parsed, opening=100000, level="full")
    assert (result.repaired, result.errors) == ([], [])


def test_a_wrong_sign_between_balances_that_are_not_adjacent_is_left_to_check():
    doc = day_end_doc("01/10/2026 SHOP 42.18", "01/10/2026 CAFE 3.40 954.42")
    parsed = statement(row("P1L2", 4218, "42.18", None), row("P1L3", -340, "3.40", 95442))
    result = repair_signs(doc, parsed, opening=100000, level="full")
    assert result.repaired == [] and parsed.rows[0].amount_pence == 4218
    parsed.opening_balance_pence = 100000
    assert any("running balance mismatch" in e for e in check_statement(parsed))


def test_rows_after_the_last_balance_are_settled_by_the_closing_balance():
    doc = day_end_doc("01/10/2026 SHOP 10.00 990.00", "02/10/2026 CAFE 5.00")
    parsed = statement(row("P1L2", -1000, "10.00", 99000), row("P1L3", -500, "5.00", None))
    assert repair_signs(doc, parsed, opening=100000, closing=98500, level="full").errors == []
    unsure = repair_signs(doc, parsed, opening=100000, closing=None, level="full")
    assert any("can't tell whether" in e and "P1L3" in e for e in unsure.errors)


def test_a_statement_without_running_balances_is_settled_from_opening_to_closing():
    lines = [f"0{i}/10/2026 SHOP {i} {i}.00" for i in range(1, 4)]
    doc = day_end_doc(*lines)
    parsed = statement(*(row(f"P1L{i + 1}", -100 * i, f"{i}.00", None) for i in range(1, 4)))
    # -1 -2 -3 = -6 is the only way to get there (1+2-3 = 0, ...)
    assert repair_signs(doc, parsed, opening=1000, closing=400, level="full").errors == []
    # 0 can be reached as +1 +2 -3 or -1 -2 +3: which rows went out can't be told
    assert repair_signs(doc, parsed, opening=1000, closing=1000, level="full").errors


def test_too_many_rows_between_balances_are_reported_not_guessed():
    lines = [f"01/10/2026 SHOP {i} 1.00" for i in range(20)]
    doc = day_end_doc(*lines)
    parsed = statement(*(row(f"P1L{i + 2}", -100, "1.00", None) for i in range(20)))
    result = repair_signs(doc, parsed, opening=10000, closing=8000, level="full")
    assert result.errors and "can't tell whether" in result.errors[0]
    assert len(result.errors[0]) < 200  # the refs are summarised


def test_a_signed_row_needs_no_proof():
    doc = day_end_doc("01/10/2026 SHOP -10.00", "02/10/2026 CAFE -5.00")
    parsed = statement(row("P1L2", -1000, "-10.00", None), row("P1L3", -500, "-5.00", None))
    assert repair_signs(doc, parsed, opening=None, level="full").errors == []


def test_an_overdrawn_brought_forward_line_starts_the_balances():
    doc = pages_document(
        [[TABLE, "Balance brought forward 100.00 D", "01/10/2026 ACME 50.00 50.00 D"]],
        sha256="x",
        kind="pdf",
    )
    parsed = statement(
        row("P1L3", -5000, "50.00", -5000), skipped=[SkippedLine(ref="P1L2", reason="bf")]
    )
    result = repair_signs(doc, parsed, opening=None, level="full")
    assert result.repaired == ["P1L3"] and parsed.rows[0].amount_pence == 5000


def test_the_read_prompt_says_how_overdrawn_balances_are_marked():
    from tuppence.ingest.prompts import load_prompt

    prompt = load_prompt("read")
    assert all(marker in prompt for marker in ("105.00 D", "OD", "overdrawn"))


def test_a_balance_line_after_the_table_is_withheld_and_read_here():
    page = [
        "Card statement",
        "Statement for 29 Sep 2026 to 28 Oct 2026",
        "Date Description Amount",
        "30 Sep 2026 Greenbasket Stores 30.00",
        "Previous balance 100.00",
        "New balance 130.00",
    ]
    doc = pages_document([page], sha256="x", kind="pdf")
    texts = [line.text for line in doc.lines if line.ref in doc.data_refs]
    assert "New balance 130.00" not in texts and "Previous balance 100.00" not in texts
    found = local_balances(doc, perspective="card")
    assert (found.opening, found.closing) == (10000, 13000)


def test_a_dated_opening_balance_row_in_the_table_starts_the_balances():
    """m1: the model skips "01/10/2026 Opening balance 1,000.00"; it is read here."""
    doc = pages_document(
        [[TABLE, "01/10/2026 Opening balance 1,000.00", "01/10/2026 ACME 900.00 1,900.00"]],
        sha256="x",
        kind="pdf",
    )
    parsed = statement(
        row("P1L3", -90000, "900.00", 190000), skipped=[SkippedLine(ref="P1L2", reason="ob")]
    )
    result = repair_signs(doc, parsed, opening=None, level="full")
    assert result.errors == [] and result.repaired == ["P1L3"]
    assert parsed.rows[0].amount_pence == 90000


def test_reading_balances_takes_linear_time_on_long_gaps():
    import time

    doc = doc_with("Opening balance (" + " " * 20_000 + "x", "(" + " " * 20_000 + "x")
    start = time.monotonic()
    local_balances(doc, perspective="household")
    assert time.monotonic() - start < 2


def test_a_wrapped_rows_amount_above_a_balance_label_is_reported_not_lost(ingest_env):
    """N6 p1: a PDF row wrapped onto two lines, then a "Balance" label at the foot of the page.
    The figure may be the balance, so it is held back, and the statement needs a look."""
    from ingest.helpers import parse_pages

    services, scripted = ingest_env
    page = [
        *HEAD,
        "Opening balance 1,000.00",
        TABLE,
        "01/10/2026 GREENBASKET STORES 3.40 996.60",
        "02/10/2026 LITTLE CAFE",
        "12.80",
        "Balance",
    ]
    out, doc = parse_pages(services, [page])
    by_ref = doc.by_ref()
    assert [by_ref[r].text for r in doc.held_amount_refs] == ["12.80"]
    assert any("held back" in e for e in out.errors)


def test_a_b_f_line_between_rows_is_a_printed_balance_for_sign_repair():
    doc = pages_document(
        [[TABLE, "Balance b/f 1,000.00", "01/10/2026 ACME 900.00 1,900.00"]],
        sha256="x",
        kind="pdf",
    )
    parsed = statement(row("P1L3", -90000, "900.00", 190000))
    result = repair_signs(doc, parsed, opening=None, level="full")
    assert result.repaired == ["P1L3"] and parsed.rows[0].amount_pence == 90000
