"""Check can't be talked round (scan follow-up on check.py): figures are whole printed money
tokens read one way everywhere; a row's balance must be the balance printed on its own line;
nothing the model chooses (a ref, a type, a description, a balance it reports) can switch a
check off; and every failed check ends the statement in needs_review."""

import datetime as dt
import json
import re

import pytest

from ingest.helpers import add_account, drain, parse_pages, use_local_model
from tuppence.ingest.check import balance_printed, check_document, check_rows, figures
from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement


def _doc(*texts: str) -> Document:
    lines = [Line(ref=f"L{i}", text=t) for i, t in enumerate(texts, start=1)]
    return Document(kind="text", sha256="x", lines=lines, data_refs=[ln.ref for ln in lines])


def _row(ref="L1", pence=-5000, text="50.00", **kw) -> ParsedRow:
    return ParsedRow(
        ref=ref,
        date=dt.date(2026, 10, 2),
        amount_pence=pence,
        amount_text=text,
        raw_description="Shop",
        **kw,
    )


def _statement(*rows: ParsedRow, importer="ai-read", **kw) -> ParsedStatement:
    return ParsedStatement(
        importer=importer,
        period_start=dt.date(2026, 10, 1),
        period_end=dt.date(2026, 10, 31),
        rows=list(rows),
        **kw,
    )


# --- (a) whole money tokens, one reading -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("02/10/2026 Shop 150.00", [15000]),
        ("02/10/2026 Shop £1,250.00", [125000]),
        ("02/10/2026 Shop 50.00.2026", []),  # a date-like run, not a figure
        ("02/10/2026 Sort 12.34.56", []),
        ("02/10/2026,12.30,Shop", [1230]),  # a CSV field after a date
        ("2026,123.45", [12345]),
        ("Paid 12,345,678.00 in", [1234567800]),
        ("x 1,2,3.00", [300]),
        ("£250 and 3.40", [25000, 340]),  # a whole pound figure needs its £
        ("Ref 250 Shop", []),
        ("01/10/2026,900,Shop", [900 * 100]),  # or a whole CSV cell
    ],
)
def test_figures_are_whole_money_tokens(text, found):
    assert [f.pence for f in figures(text)] == found


@pytest.mark.parametrize(
    "line",
    ["02/10/2026 Shop 150.00", "02/10/2026 Shop £1,250.00", "02/10/2026 Shop 50.00.2026 1.00"],
)
def test_an_amount_is_evidenced_only_by_the_whole_figure(line):
    doc = _doc(line)
    errors = check_document(doc, _statement(_row(text="50.00")))
    assert 'L1: amount_text "50.00" not found on line' in errors


def test_amount_and_balance_read_signs_and_separators_the_same_way():
    for printed, pence in (
        ("(1,234.56)", -123456),
        ("1,234.56-", -123456),
        ("1,234.56 DR", -123456),
        ("-£1,234.56", -123456),
        ("1,234.56 CR", 123456),
    ):
        line = f"02/10/2026 Shop 10.00 {printed}"
        row = _row(pence=-1000, text="10.00", balance_after_pence=pence)
        assert balance_printed(row, line, "household"), printed
        amount = _row(pence=pence, text=printed)
        assert not [
            e
            for e in check_document(_doc(f"02/10/2026 Shop {printed}"), _statement(amount))
            if "amount_text" in e
        ]


@pytest.mark.parametrize(
    ("line", "balance"),
    [
        ("02/10/2026 Shop 10.00 150.00", 5000),
        ("02/10/2026 Shop 10.00 £1,250.00", 25000),
        ("02/10/2026 Shop 10.00 50.00.2026", 5000),
    ],
)
def test_a_balance_is_evidenced_only_by_the_whole_figure(line, balance):
    assert not balance_printed(
        _row(pence=-1000, text="10.00", balance_after_pence=balance), line, "household"
    )


# --- (b) the balance is the line's own balance figure -----------------------------------------


def test_a_rows_own_amount_is_not_its_balance():
    row = _row(pence=-5000, text="50.00", balance_after_pence=5000)
    assert not balance_printed(row, "02/10/2026 Shop 50.00", "household")
    assert balance_printed(row, "02/10/2026 Shop 50.00 50.00", "household")


def test_the_balance_is_the_last_figure_on_the_line():
    row = _row(pence=-1000, text="10.00", balance_after_pence=95000)
    assert balance_printed(row, "02/10/2026 Shop 10.00 950.00", "household")
    assert not balance_printed(row, "02/10/2026 Ref 950.00 Shop 10.00", "household")
    assert not balance_printed(row, "02/10/2026 Shop 10.00 950.00 12.00", "household")


def test_a_balance_printed_only_on_another_line_is_dropped(ingest_env):
    """The model copies the running balance from the line above: it isn't this row's."""
    services, scripted = ingest_env

    def copy_from_above(body):
        user = body["messages"][-1]["content"]
        rows = []
        for ref, text in re.findall(r"^(D\d+): (.*)$", user, re.MULTILINE):
            if "Shop" in text:
                rows.append(
                    {
                        "ref": ref,
                        "date": "2026-10-02",
                        "amount": -10.0,
                        "amount_text": "10.00",
                        "sign_from": None,
                        "raw_desc": "Shop",
                        "merchant": None,
                        "bank_category": None,
                        "bank_type": None,
                        "running_balance": 990.0,
                    }
                )
            elif "Cafe" in text:
                rows.append(
                    {
                        "ref": ref,
                        "date": "2026-10-03",
                        "amount": -5.0,
                        "amount_text": "5.00",
                        "sign_from": None,
                        "raw_desc": "Cafe",
                        "merchant": None,
                        "bank_category": None,
                        "bank_type": None,
                        "running_balance": 990.0,
                    }
                )
        return json.dumps(
            {
                "statement": {
                    "period_start": None,
                    "period_end": None,
                    "opening_balance": None,
                    "closing_balance": None,
                    "currency": "GBP",
                },
                "transactions": rows,
                "skipped": [],
            }
        )

    scripted.replies = [copy_from_above] * 3
    out, _ = parse_pages(
        services,
        [
            [
                "Date Description Amount Balance",
                "02 Oct 2026 Shop 10.00 990.00",
                "03 Oct 2026 Cafe 5.00",
            ]
        ],
    )
    balances = {r.raw_description: r.balance_after_pence for r in out.parsed.rows}
    assert balances == {"Shop": 99000, "Cafe": None}


# --- (c) nothing the model chooses switches a check off ---------------------------------------


def test_the_fee_shortcut_is_only_for_an_importers_own_fee_rows():
    line = "01/10/2026 Monthly fee 5.00 995.00"
    for importer in ("ai-read", "test"):
        row = _row(ref="L1#fee", pence=-500, text="5.00")
        errors = check_document(_doc(line), _statement(row, importer=importer))
        assert any("sign mismatch" in e for e in errors), importer
    fee = _row(ref="L1#fee", pence=-500, text="5.00")
    plain = _row(ref="L1", pence=-500, text="5.00", sign_from=None)
    errors = check_document(_doc(line), _statement(fee, plain, importer="csv:example"))
    assert not any("L1#fee: sign mismatch" in e for e in errors)


@pytest.mark.parametrize("ref", ["D2#fee", "P1L2#fee", "P1L2", "p1l2", "D2 ", "D02", "L2"])
def test_the_reader_accepts_only_the_ids_it_was_given(ingest_env, ref):
    """A ref the model makes up, however close to a real one, covers no line: it is reported,
    and the line it names is still missing."""
    services, scripted = ingest_env

    def answer(body):
        user = body["messages"][-1]["content"]
        rows = []
        for alias, text in re.findall(r"^(D\d+): (.*)$", user, re.MULTILINE):
            if "Shop" in text:
                rows.append(
                    {
                        "ref": ref if alias == "D2" else alias,
                        "date": "2026-10-02",
                        "amount": 5.0,
                        "amount_text": "5.00",
                        "sign_from": None,
                        "raw_desc": "Monthly fee",
                        "merchant": None,
                        "bank_category": None,
                        "bank_type": "fee",
                        "running_balance": None,
                    }
                )
        skipped = [{"ref": "D1", "reason": "headings"}]
        return json.dumps(
            {
                "statement": {
                    "period_start": None,
                    "period_end": None,
                    "opening_balance": None,
                    "closing_balance": None,
                    "currency": "GBP",
                },
                "transactions": rows,
                "skipped": skipped,
            }
        )

    scripted.replies = [answer] * 3
    out, _ = parse_pages(services, [["Date Description Amount", "02 Oct 2026 Shop 5.00"]])
    assert any("unexpected ref" in e for e in out.errors), out.errors
    assert any("missing refs" in e for e in out.errors), out.errors


def test_a_model_opening_or_closing_balance_never_verifies_a_statement(ingest_env):
    """No summary box: the model reports an opening and closing balance that add up. Printed
    nowhere it was sent, they are dropped, so nothing is verified."""
    services, scripted = ingest_env

    def answer(body):
        user = body["messages"][-1]["content"]
        rows = [
            {
                "ref": ref,
                "date": "2026-10-02",
                "amount": -5.0,
                "amount_text": "5.00",
                "sign_from": None,
                "raw_desc": "Shop",
                "merchant": None,
                "bank_category": None,
                "bank_type": None,
                "running_balance": None,
            }
            for ref, text in re.findall(r"^(D\d+): (.*)$", user, re.MULTILINE)
            if "Shop" in text
        ]
        skipped = [{"ref": "D1", "reason": "headings"}]
        return json.dumps(
            {
                "statement": {
                    "period_start": "2026-10-01",
                    "period_end": "2026-10-31",
                    "opening_balance": 100.0,
                    "closing_balance": 95.0,
                    "currency": "GBP",
                },
                "transactions": rows,
                "skipped": skipped,
            }
        )

    scripted.replies = [answer] * 3
    out, _ = parse_pages(services, [["Date Description Amount", "02 Oct 2026 Shop -5.00"]])
    assert out.parsed.opening_balance_pence is None and out.parsed.closing_balance_pence is None


def test_a_zero_amount_is_reported():
    errors = check_document(_doc("02/10/2026 Shop 0.00"), _statement(_row(pence=0, text="0.00")))
    assert "L1: amount is zero" in errors


# --- (d) every failed check ends in needs_review ----------------------------------------------


@pytest.mark.parametrize(
    "mistake",
    ["unexpected ref", "wrong amount", "wrong sign", "zero", "duplicate", "skip without reason"],
)
def test_every_failed_check_ends_in_needs_review(ingest_env, mistake):
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")

    def answer(body):
        user = body["messages"][-1]["content"].split("Your previous answer")[0]
        rows, skipped = [], []
        for ref, text in re.findall(r"^(D\d+): (.*)$", user, re.MULTILINE):
            if "Shop" not in text:
                skipped.append({"ref": ref, "reason": "" if mistake.startswith("skip") else "x"})
                continue
            row = {
                "ref": ref,
                "date": "2026-10-02",
                "amount": -5.0,
                "amount_text": "-5.00",
                "sign_from": None,
                "raw_desc": "Shop",
                "merchant": None,
                "bank_category": None,
                "bank_type": None,
                "running_balance": None,
            }
            if mistake == "unexpected ref":
                row["ref"] = f"{ref}#fee"
            elif mistake == "wrong amount":
                row["amount"], row["amount_text"] = -0.5, "-0.50"
            elif mistake == "wrong sign":
                row["amount"] = 5.0
            elif mistake == "zero":
                row["amount"], row["amount_text"] = 0.0, "0.00"
            rows.append(row)
            if mistake == "duplicate":
                rows.append(dict(row))
        return json.dumps(
            {
                "statement": {
                    "period_start": "2026-10-01",
                    "period_end": "2026-10-31",
                    "opening_balance": None,
                    "closing_balance": None,
                    "currency": "GBP",
                },
                "transactions": rows,
                "skipped": skipped,
            }
        )

    scripted.replies = [answer] * 3
    text = "Example Bank plc\nDate Description Amount\n02/10/2026 Shop -5.00 0.00\n"
    outcome = services.ingest.upload(f"{mistake}.txt", text.encode())
    drain(services)
    record = services.statements.get(outcome.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(outcome.record.id)
    assert record.status == "needs_review", (mistake, record.status, record.check_errors)
    assert services.statements.transactions(record.id) == []


def test_check_rows_never_trusts_a_skipped_line_with_no_ref():
    doc = _doc("02/10/2026 Shop 5.00")
    from tuppence.ingest.models import SkippedLine

    parsed = _statement(skipped=[SkippedLine(ref="", reason="nothing")])
    errors = check_rows(
        doc.lines, all_lines=doc.lines, context_refs=[], data_refs=["L1"], parsed=parsed
    )
    assert "missing refs: L1" in errors
