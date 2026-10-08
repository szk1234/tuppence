"""I2 (final review): a transaction line that carries a sort code, an account number, a card
ending or a name is sent with those details masked, read like any other row, and stored as
printed. Nothing is withheld and lost silently. All statements are synthetic."""

import datetime as dt
import json

import pytest

from ingest.helpers import add_account, drain, use_local_model
from tuppence.ingest.models import Document, Line, MaskedLine, ParsedRow, ParsedStatement
from tuppence.ingest.parse import restore_masked

TEXT_STATEMENT = """Example Bank plc
Statement period 01/10/2026 to 31/10/2026
Date Description Amount
01/10/2026 Greenbasket Stores -42.18
03/10/2026 Transfer to A/C 87654321 -250.00
05/10/2026 Acme Payroll Ltd +1,200.00
07/10/2026 Little Cafe -3.50
09/10/2026 Payment to 20-11-33 41234567 J SMITH -75.00
"""
DETAILS = ("87654321", "20-11-33", "41234567")


def _settle(services, outcome, account):
    record = services.statements.get(outcome.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(outcome.record.id)
    return record


def test_rows_with_account_details_are_read_and_stored_as_printed(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")
    outcome = services.ingest.upload("probe.txt", TEXT_STATEMENT.encode())
    drain(services)
    record = _settle(services, outcome, account)
    assert record.status == "imported", (record.error, record.check_errors)
    rows = {r.raw_description: r.amount_pence for r in services.statements.transactions(record.id)}
    assert rows == {
        "Greenbasket Stores": -4218,
        "Transfer to A/C 87654321": -25000,
        "Acme Payroll Ltd": 120000,
        "Little Cafe": -350,
        "Payment to 20-11-33 41234567 J SMITH": -7500,
    }
    sent = json.dumps(scripted.requests)
    assert [d for d in DETAILS if d in sent] == []


def _pdf_with_summary_box() -> bytes:
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 10)
    rows = [
        [(56, "Example Bank plc", False)],
        [(56, "Statement period 01/10/2026 to 31/10/2026", False)],
        [(56, "Opening balance £1,000.00", False)],
        [(56, "Closing balance £707.82", False)],
        [(56, "Date", False), (140, "Description", False), (470, "Amount", True),
         (540, "Balance", True)],
        [(56, "01 Oct 2026", False), (140, "Greenbasket Stores", False), (470, "-42.18", True),
         (540, "957.82", True)],
        [(56, "03 Oct 2026", False), (140, "FPO J SMITH 20-11-33 41234567", False),
         (470, "-250.00", True), (540, "707.82", True)],
    ]  # fmt: skip
    y = 800
    for cells in rows:
        for x, text, right in cells:
            (c.drawRightString if right else c.drawString)(x, y, text)
        y -= 15
    c.setFont("Helvetica", 8)
    c.drawString(56, 48, "SYNTHETIC TEST STATEMENT - NOT A REAL DOCUMENT")
    c.save()
    return buf.getvalue()


def test_a_pdf_row_with_account_details_adds_up_with_the_summary_box(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")
    outcome = services.ingest.upload("details.pdf", _pdf_with_summary_box())
    drain(services)
    record = _settle(services, outcome, account)
    assert record.status == "imported", (record.error, record.check_errors)
    assert record.balance_verified
    rows = services.statements.transactions(record.id)
    assert [(r.raw_description, r.amount_pence) for r in rows] == [
        ("Greenbasket Stores", -4218),
        ("FPO J SMITH 20-11-33 41234567", -25000),
    ]
    sent = json.dumps(scripted.requests)
    assert [d for d in DETAILS if d in sent] == []


def test_a_line_that_cant_be_sent_is_offered_on_the_fix_up_screen(ingest_env, monkeypatch):
    """Should a line's details not mask cleanly, it is held back and reported, and the person
    can add it as a row: it is never lost."""
    from tuppence.ingest import sensitive
    from tuppence.ingest.service import RowEdit

    services, _ = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")
    real = sensitive.prepare_outbound
    monkeypatch.setattr(
        sensitive,
        "prepare_outbound",
        lambda text, names=(), **near: (
            None if sensitive.classify(text, names=names) else real(text, **near)
        ),
    )
    outcome = services.ingest.upload("probe.txt", TEXT_STATEMENT.encode())
    drain(services)
    record = _settle(services, outcome, account)
    assert record.status == "needs_review"
    held = record.draft["document"]["held_amount_refs"]
    assert held == ["L5", "L8"]
    assert any("held back" in e for e in record.check_errors)
    rows = [
        RowEdit(ref=r["ref"], date=r["date"], amount_pence=r["amount_pence"],
                description=r["raw_description"])
        for r in record.draft["parsed"]["rows"]
    ] + [
        RowEdit(ref="L5", date=dt.date(2026, 10, 3), amount_pence=-25000,
                description="Transfer to A/C 87654321"),
        RowEdit(ref="L8", date=dt.date(2026, 10, 9), amount_pence=-7500,
                description="Payment to J SMITH"),
    ]  # fmt: skip
    saved = services.ingest.save_draft(
        record.id, rows=rows, skipped=[], expected_version=record.version
    )
    assert not any("held back" in e for e in saved.check_errors)


@pytest.mark.parametrize("field", ["raw_description", "merchant"])
def test_placeholders_in_the_models_reply_are_put_back(field):
    doc = Document(
        kind="text",
        sha256="x",
        lines=[Line(ref="L1", text="03/10/2026 Transfer to A/C 87654321 -250.00")],
        data_refs=["L1"],
        masked={
            "L1": MaskedLine(
                text="03/10/2026 Transfer to A/C [hidden-a] -250.00",
                hidden={"[hidden-a]": "87654321"},
            )
        },
    )
    row = ParsedRow(
        ref="L1",
        date=dt.date(2026, 10, 3),
        amount_pence=-25000,
        amount_text="-250.00",
        raw_description="Transfer to A/C [hidden-a]",
    )
    setattr(row, field, "Transfer to A/C [hidden-a]")
    parsed = ParsedStatement(importer="ai-read", rows=[row])
    restore_masked(parsed, doc)
    assert getattr(parsed.rows[0], field) == "Transfer to A/C 87654321"


# --- no path sends a detail (scan follow-up) ---------------------------------------------------


def _plain(text: str) -> str:
    """Request text as a reader would see it: compatibility forms folded, invisible
    characters gone, spaces made plain and single."""
    import unicodedata

    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return " ".join(text.split())


def test_retry_feedback_never_carries_a_detail(ingest_env):
    """A first answer that isn't JSON, then one with a sign the wrong way round: each retry
    sends the chunk again with what failed and the previous answer, all of it masked."""
    from evals import oracle

    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")

    def wrong_sign(body):
        answer = oracle.read(body["messages"][-1]["content"])
        for row in answer["transactions"]:
            if "Transfer" in row["raw_desc"]:
                row["amount"] = abs(row["amount"])
        return json.dumps(answer)

    scripted.replies = [{"content": "not json"}, wrong_sign]
    outcome = services.ingest.upload("probe.txt", TEXT_STATEMENT.encode())
    drain(services)
    record = _settle(services, outcome, account)
    assert record.status == "imported", (record.error, record.check_errors)
    users = [m["content"] for r in scripted.requests for m in r["messages"] if m["role"] == "user"]
    assert len(users) >= 3 and any("previous answer failed" in u for u in users)
    sent = _plain(json.dumps(scripted.requests, ensure_ascii=False))
    assert [d for d in DETAILS if d in sent] == []


ODD_STATEMENT = (
    "Example Bank plc\n"
    "Statement period 01/10/2026 to 31/10/2026\n"
    "Date Description Amount\n"
    "01/10/2026 Greenbasket Stores -42.18\n"
    "03/10/2026 Transfer to A/C ８７６５４３２１ -250.00\n"
    "05/10/2026 Payment to 20​-11-33 4123​4567 -75.00\n"
    "07/10/2026 Payment to 12 34 56 41234567 -10.00\n"
    "09/10/2026 Refund ALEX​EXAMPLE +5.00\n"
)


def test_details_printed_with_unicode_tricks_are_masked_end_to_end(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")  # the household: Alex Example
    outcome = services.ingest.upload("odd.txt", ODD_STATEMENT.encode())
    drain(services)
    record = _settle(services, outcome, account)
    assert record.status == "imported", (record.error, record.check_errors)
    stored = sorted(r.raw_description for r in services.statements.transactions(record.id))
    assert stored == [
        "Greenbasket Stores",
        "Payment to 12 34 56 41234567",
        "Payment to 20-11-33 41234567",
        "Refund ALEXEXAMPLE",  # a zero-width space shows no gap, so it is dropped
        "Transfer to A/C 87654321",
    ]
    sent = _plain(json.dumps(scripted.requests, ensure_ascii=False)).casefold()
    for detail in ("87654321", "41234567", "20-11-33", "12 34 56", "alex example", "alexexample"):
        assert detail not in sent, detail


def test_the_layout_sketch_never_carries_a_cell_value(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")
    csv = (
        "Date,Description,Amount,Ref ９８７６５,Payee account\n"
        "01/10/2026,TRANSFER ALEX​EXAMPLE,-42.18,1,８７６５４３２１\n"
        "02/10/2026,SHOP,-3.40,2,20​-11-33 4123​4567\n"
        "03/10/2026,PAY,900.00,3,\n"
    )
    outcome = services.ingest.upload("odd.csv", csv.encode())
    drain(services)
    _settle(services, outcome, account)
    assert scripted.requests  # the layout was learned from a sketch
    sent = _plain(json.dumps(scripted.requests, ensure_ascii=False)).casefold()
    for detail in ("98765", "87654321", "41234567", "20-11-33", "alex", "transfer", "shop"):
        assert detail not in sent, detail


def test_vision_transcripts_are_masked_before_they_are_read(fixtures):
    from tuppence.ingest.extract import ExtractLimits, extract_document
    from tuppence.ingest.textprep import plan_chunks, render

    class Vision:
        def transcribe(self, image, media_type):
            return [
                "Card statement", "Date   Description   Amount",
                "05 Oct 2026   FPO J SMITH 20​-11-33 ４１２３４５６７   10.00",
                "06 Oct 2026   Refund to card •••• 4242   5.00 CR",
                "07 Oct 2026   Greenbasket Stores   42.18",
            ]  # fmt: skip

    doc = extract_document(
        fixtures / "pdf" / "card-scanned.pdf",
        "pdf",
        sha256="x",
        limits=ExtractLimits(),
        vision=Vision(),
    )
    sent = _plain("\n".join(render(c.lines) for c in plan_chunks(doc, rows_per_chunk=40)))
    for detail in ("41234567", "20-11-33", "4242"):
        assert detail not in sent, detail
    assert "Greenbasket Stores" in sent and "10.00" in sent and "5.00 CR" in sent
