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
