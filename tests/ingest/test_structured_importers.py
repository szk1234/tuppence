from datetime import date

import pytest

from tuppence.core.errors import UserFacing
from tuppence.ingest.check import check_document
from tuppence.ingest.importers.camt import CamtError, camt_document, parse_camt
from tuppence.ingest.importers.ofx import OfxError, ofx_document, parse_ofx
from tuppence.ingest.importers.qif import QifError, parse_qif, qif_document
from tuppence.ingest.textnum import decode_text


def test_ofx_sgml_current_account(fixtures):
    text = decode_text((fixtures / "ofx" / "current.ofx").read_bytes())
    doc, parsed = ofx_document(text, sha256="x"), parse_ofx(text)
    assert doc.meta == {
        "bankid": "400000",
        "acctid": "40000011112222",
        "accttype": "CHECKING",
        "card": "0",
    }
    assert doc.preamble_refs == ["H1"] and doc.data_refs == ["L1", "L2", "L3", "L4", "L5"]
    assert doc.by_ref()["L1"].text == "20261001 | -42.18 | DEBIT | GREENBASKET STORES | CARD 1234"
    assert check_document(doc, parsed) == []
    assert (parsed.period_start, parsed.period_end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert parsed.closing_balance_pence == 252732 and parsed.opening_balance_pence is None
    assert [r.amount_pence for r in parsed.rows] == [-4218, -4820, -340, 165000, -2890]
    assert (
        parsed.rows[3].raw_description == "ACME PAYROLL LTD OCT WAGES"
        and parsed.rows[3].bank_type == "CREDIT"
    )


def test_qfx_xml_credit_card(fixtures):
    text = decode_text((fixtures / "ofx" / "card.qfx").read_bytes())
    doc, parsed = ofx_document(text, sha256="x"), parse_ofx(text)
    assert doc.meta["card"] == "1" and doc.meta["acctid"] == "XXXXXXXXXXXX4242"
    assert check_document(doc, parsed) == []
    assert [r.amount_pence for r in parsed.rows] == [-6420, -415, 615, 15000]
    assert parsed.closing_balance_pence == -31280


def test_not_ofx():
    with pytest.raises(OfxError):
        ofx_document("Date,Amount\n", sha256="x")


def test_qif_bank(fixtures):
    text = decode_text((fixtures / "qif" / "bank.qif").read_bytes())
    doc, parsed = qif_document(text, sha256="x"), parse_qif(text)
    assert doc.meta == {"qif_type": "Bank", "card": "0"} and doc.data_refs == [
        "L2",
        "L7",
        "L12",
        "L17",
        "L21",
    ]
    assert check_document(doc, parsed) == []
    assert [(r.date, r.amount_pence) for r in parsed.rows][2] == (date(2026, 10, 17), 165000)
    assert parsed.rows[1].bank_category == "Bills:Insurance"


def test_qif_month_first_dates_are_detected():
    text = (
        "!Type:CCard\nD10/13/2026\nT-4.15\nPLittle Cafe\n^\nD10/02/2026\nT-64.20\nPGreenbasket\n^\n"
    )
    parsed = parse_qif(text)
    assert [r.date for r in parsed.rows] == [date(2026, 10, 13), date(2026, 10, 2)]
    assert qif_document(text, sha256="x").meta["card"] == "1"


def test_not_qif():
    with pytest.raises(QifError):
        parse_qif("D01/10/2026\nT-1.00\n^\n")


def test_camt053(fixtures):
    data = (fixtures / "camt" / "statement.xml").read_bytes()
    doc, parsed = camt_document(data, sha256="x"), parse_camt(data)
    assert doc.meta == {"iban": "GB00SYNT00000012345678", "bic": "NAIAGB21"}
    assert check_document(doc, parsed) == []
    assert (parsed.opening_balance_pence, parsed.closing_balance_pence) == (100000, 252732)
    assert [r.amount_pence for r in parsed.rows] == [-4218, -4820, -340, 165000, -2890]
    assert [s.ref for s in parsed.skipped] == ["L6"] and "pending" in parsed.skipped[0].reason
    assert parsed.rows[3].raw_description == "Acme Payroll Ltd OCT WAGES"


def test_camt_refuses_entity_tricks():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">'
        b"<Stmt><Id>&b;</Id></Stmt></Document>"
    )
    with pytest.raises(CamtError):
        parse_camt(bomb)


def test_camt_errors_never_quote_the_library():
    with pytest.raises(CamtError) as broken:
        parse_camt(b"<Document><Stmt></Document>")
    assert "line" not in str(broken.value) and "column" not in str(broken.value)
    assert isinstance(broken.value, UserFacing)


def test_camt_never_resolves_external_entities(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    xxe = (
        f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file://{secret}">]>'
        '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">'
        "<Stmt><Id>&e;</Id></Stmt></Document>"
    ).encode()
    with pytest.raises(CamtError) as refused:
        parse_camt(xxe)
    assert "TOP-SECRET-VALUE" not in str(refused.value)


def test_importer_errors_are_ours_to_show():
    assert all(issubclass(e, UserFacing) for e in (OfxError, QifError, CamtError))


def test_unclosed_ofx_aggregates_are_refused_quickly():
    import time

    big = "<OFX>" + "<STMTTRN><TRNAMT>1<DTPOSTED>20260101" * 6000  # about 216 KB
    started = time.perf_counter()
    for call in (lambda: parse_ofx(big), lambda: ofx_document(big, sha256="x")):
        with pytest.raises(OfxError, match="never closed"):
            call()
    assert time.perf_counter() - started < 0.5


def test_ofx_size_and_count_limits(monkeypatch):
    from tuppence.ingest.importers import ofx

    monkeypatch.setattr(ofx, "MAX_CHARS", 50)
    with pytest.raises(OfxError, match="too large"):
        parse_ofx("<OFX>" + "x" * 100)
    monkeypatch.setattr(ofx, "MAX_CHARS", 10_000)
    monkeypatch.setattr(ofx, "MAX_TRANSACTIONS", 2)
    many = "<OFX>" + "<STMTTRN><TRNAMT>1<DTPOSTED>20260101</STMTTRN>" * 3
    with pytest.raises(OfxError, match="too many"):
        parse_ofx(many)


def test_ofx_entities_are_unescaped():
    text = "<OFX><STMTTRN><DTPOSTED>20260101<TRNAMT>-1.00<NAME>Tom &amp; Jerry</STMTTRN></OFX>"
    assert parse_ofx(text).rows[0].raw_description == "Tom & Jerry"


def _camt(stmts: str) -> bytes:
    return (
        '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt>'
        f"{stmts}</BkToCstmrStmt></Document>"
    ).encode()


def _stmt(iban, start, amount, opening, closing, ind="DBIT", extra=""):
    return (
        f"<Stmt><Id>{start}</Id><FrToDt><FrDtTm>{start}T00:00:00</FrDtTm>"
        f"<ToDtTm>{start}T23:59:59</ToDtTm></FrToDt>"
        f"<Acct><Id><IBAN>{iban}</IBAN></Id></Acct>"
        f"<Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt>{opening}</Amt>"
        "<CdtDbtInd>CRDT</CdtDbtInd></Bal>"
        f"<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt>{closing}</Amt>"
        "<CdtDbtInd>CRDT</CdtDbtInd></Bal>"
        f"<Ntry><Amt>{amount}</Amt><CdtDbtInd>{ind}</CdtDbtInd>{extra}<Sts><Cd>BOOK</Cd></Sts>"
        f"<BookgDt><Dt>{start}</Dt></BookgDt></Ntry></Stmt>"
    )


def test_camt_reads_every_statement_of_one_account_in_date_order():
    data = _camt(
        _stmt("GB00SYNT1", "2026-11-05", "20.00", "90.00", "70.00")
        + _stmt("GB00SYNT1", "2026-10-05", "10.00", "100.00", "90.00")
    )
    doc, parsed = camt_document(data, sha256="x"), parse_camt(data)
    assert doc.data_refs == ["L1", "L2"]
    assert [(r.ref, r.amount_pence) for r in parsed.rows] == [("L1", -1000), ("L2", -2000)]
    assert (parsed.opening_balance_pence, parsed.closing_balance_pence) == (10000, 7000)
    assert (parsed.period_start, parsed.period_end) == (date(2026, 10, 5), date(2026, 11, 5))
    assert check_document(doc, parsed) == []


def test_camt_refuses_statements_for_two_accounts():
    data = _camt(
        _stmt("GB00SYNT1", "2026-10-05", "10.00", "100.00", "90.00")
        + _stmt("GB00SYNT2", "2026-10-05", "5.00", "50.00", "45.00")
    )
    for call in (lambda: parse_camt(data), lambda: camt_document(data, sha256="x")):
        with pytest.raises(CamtError, match="more than one account") as refused:
            call()
        assert isinstance(refused.value, UserFacing)


def test_camt_reversal_flips_the_sign_and_bad_encoding_is_ours():
    data = _camt(
        _stmt("GB00SYNT1", "2026-10-05", "5.00", "1", "1", extra="<RvslInd>true</RvslInd>")
    )
    assert parse_camt(data).rows[0].amount_pence == 500
    with pytest.raises(CamtError):
        parse_camt(b'<?xml version="1.0" encoding="no-such-codec"?><Document/>')


def test_an_ofx_file_in_another_currency_is_refused_plainly(fixtures):
    """M8: its figures would otherwise be stored as pounds."""
    text = decode_text((fixtures / "ofx" / "current.ofx").read_bytes())
    assert "<CURDEF>GBP" in text
    with pytest.raises(OfxError, match=r"in EUR.*pounds"):
        parse_ofx(text.replace("<CURDEF>GBP", "<CURDEF>EUR"))
    assert isinstance(OfxError("x"), UserFacing)


def test_a_camt_statement_in_another_currency_is_refused_plainly(fixtures):
    data = (fixtures / "camt" / "statement.xml").read_bytes()
    assert b"Ccy>GBP<" in data
    with pytest.raises(CamtError, match=r"in USD.*pounds"):
        parse_camt(data.replace(b"Ccy>GBP<", b"Ccy>USD<"))
