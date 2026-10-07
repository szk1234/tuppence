import hashlib
from datetime import date

import pytest

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.identify import (
    AccountRef,
    Evidence,
    _short_hash,
    fingerprint_key,
    header_facts,
    identify,
    match_account,
)
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack

PACK = load_bank_pack()
LIMITS = ExtractLimits()
KEY = b"k" * 32


def evidence_for(fixtures, relative, kind):
    registry = LayoutRegistry(PACK)
    doc = extract_document(
        fixtures / relative, kind, sha256="x", limits=LIMITS, known_header=registry.is_known_header
    )
    return identify(doc, pack=PACK, registry=registry, key=KEY)


@pytest.mark.parametrize(
    "relative,kind,providers,account_kind,last4",
    [
        ("csv/monzo.csv", "csv", ["monzo"], "current", None),
        ("csv/barclays.csv", "csv", ["barclays"], "current", "5678"),
        (
            "csv/lloyds-halifax.csv",
            "csv",
            ["lloyds", "halifax", "bank_of_scotland"],
            "current",
            "4321",
        ),
        ("csv/nationwide.csv", "csv", ["nationwide"], "current", "5678"),
        ("csv/santander.csv", "csv", ["santander"], "current", "9876"),
        ("csv/barclaycard.csv", "csv", ["barclaycard"], "credit_card", "4242"),
        ("csv/hsbc.csv", "csv", [], "current", None),  # no headings: only a hint
        ("ofx/current.ofx", "ofx", ["hsbc"], "current", "2222"),
        ("ofx/card.qfx", "ofx", [], "credit_card", "4242"),
        ("camt/statement.xml", "camt053", ["nationwide"], None, "5678"),
        ("qif/bank.qif", "qif", [], None, None),
        ("pdf/card-text.pdf", "pdf", ["barclaycard"], "credit_card", "4242"),
        ("pdf/current-text.pdf", "pdf", ["nationwide"], None, "5678"),
        ("image/app-screenshot.png", "image", [], None, None),
    ],
)
def test_evidence_from_each_format(fixtures, relative, kind, providers, account_kind, last4):
    ev = evidence_for(fixtures, relative, kind)
    assert (ev.providers, ev.kind, ev.last4) == (providers, account_kind, last4)


def test_layout_fingerprints_are_stable_and_specific(fixtures):
    monzo = evidence_for(fixtures, "csv/monzo.csv", "csv")
    assert monzo.layout_fingerprint.startswith("csv:") and monzo.layout_id == "monzo"
    assert (
        evidence_for(fixtures, "csv/monzo.csv", "csv").layout_fingerprint
        == monzo.layout_fingerprint
    )
    assert evidence_for(fixtures, "csv/hsbc.csv", "csv").layout_fingerprint == "csv:noheader:3"
    assert evidence_for(fixtures, "csv/hsbc.csv", "csv").provider_hint == "hsbc"
    assert evidence_for(fixtures, "image/app-screenshot.png", "image").layout_fingerprint == "image"
    assert (
        evidence_for(fixtures, "pdf/card-text.pdf", "pdf").layout_fingerprint
        == "pdf:barclaycard:credit_card"
    )


def test_header_facts():
    facts = header_facts(
        "Card ending 4242\nStatement for 29 Sep 2026 to 28 Oct 2026\nPrevious balance   £842.16\n"
        "New balance   £909.85\nMinimum payment   £25.00\nCredit limit   £3,000.00"
    )
    assert (facts.last4, facts.period_start, facts.period_end) == (
        "4242",
        date(2026, 9, 29),
        date(2026, 10, 28),
    )
    assert (facts.opening_pence, facts.closing_pence, facts.looks_like_card) == (84216, 90985, True)
    assert (
        header_facts(
            "Balance brought forward 1,000.00\nBalance carried forward £12.00 CR"
        ).closing_pence
        == -1200
    )
    assert (
        header_facts("Account number 12345678\nCard number **** 9999").last4 is None
    )  # two numbers: unsure


def acct(id_, provider, kind, last4=None, status="active"):
    return AccountRef(
        id=id_,
        provider=provider,
        provider_name=provider.title(),
        kind=kind,
        nickname=id_,
        last4=last4,
        status=status,
    )


MONZO = Evidence(
    layout_fingerprint="csv:monzo",
    providers=["monzo"],
    provider_hint="monzo",
    kind="current",
    label="Monzo",
)


def test_one_strong_match_is_assigned():
    m = match_account(MONZO, [acct("a1", "monzo", "current"), acct("a2", "hsbc", "current")], [])
    assert m.account_id == "a1"


def test_last4_picks_between_cards():
    ev = Evidence(
        layout_fingerprint="pdf:barclaycard",
        providers=["barclaycard"],
        kind="credit_card",
        last4="4242",
        label="pdf",
    )
    cards = [
        acct("c1", "barclaycard", "credit_card", "4242"),
        acct("c2", "barclaycard", "credit_card", "1111"),
    ]
    assert match_account(ev, cards, []).account_id == "c1"
    unknown = match_account(ev, cards[1:], [])
    assert unknown.account_id is None and unknown.best_guess is None
    assert unknown.prefill == {"provider": "barclaycard", "kind": "credit_card", "last4": "4242"}
    assert unknown.reason == "None of your accounts match this statement."


def test_two_accounts_with_the_same_export_are_always_asked():
    accounts = [acct("personal", "monzo", "current"), acct("joint", "monzo", "current")]
    first = match_account(MONZO, accounts, [])
    assert first.account_id is None and first.candidates == ["personal", "joint"]
    again = match_account(MONZO, accounts, ["joint"])  # memory preselects, never decides
    assert again.account_id is None and again.best_guess == "joint"


def test_memory_alone_never_decides_for_an_unknown_layout():
    unknown = Evidence(layout_fingerprint="csv:new", label="CSV file in a new layout")
    accounts = [acct("a1", "monzo", "current"), acct("cu", "other", "current")]
    asked = match_account(unknown, accounts, [])
    assert (
        asked.account_id is None
        and asked.best_guess is None
        and asked.reason == "More than one of your accounts could match."
    )
    suggested = match_account(unknown, accounts, ["cu"])
    assert suggested.account_id is None and suggested.best_guess == "cu"
    assert match_account(unknown, accounts, ["cu", "a1"]).best_guess == "a1"


@pytest.mark.parametrize("fingerprint", ["image", "pdf:unknown:-", "csv:noheader:3", "qif:bank"])
def test_generic_fingerprints_never_auto_assign_from_memory(fingerprint):
    ev = Evidence(layout_fingerprint=fingerprint, label="x")
    accounts = [acct("a", "monzo", "current"), acct("b", "hsbc", "savings")]
    m = match_account(ev, accounts, ["a"])
    assert m.account_id is None and m.best_guess == "a" and "a" in m.candidates


def test_memory_decides_for_account_specific_fingerprints():
    accounts = [acct("a", "monzo", "current"), acct("b", "hsbc", "current")]
    ofx = Evidence(layout_fingerprint="ofx:040004:abc123", label="OFX download")
    assert match_account(ofx, accounts, ["b"]).account_id == "b"
    bank_and_last4 = Evidence(
        layout_fingerprint="pdf:hsbc:-", providers=["hsbc"], last4="1234", label="PDF"
    )
    known = [acct("a", "monzo", "current"), acct("b", "hsbc", "current", "1234")]
    assert match_account(bank_and_last4, known, ["b"]).account_id == "b"
    # a known bank without a last 4 on the statement is still just a suggestion with memory
    no_last4 = Evidence(layout_fingerprint="pdf:hsbc:-", providers=["hsbc"], label="PDF")
    two = [acct("b1", "hsbc", "current"), acct("b2", "hsbc", "savings")]
    m = match_account(no_last4, two, ["b1"])
    assert m.account_id is None and m.best_guess == "b1"


def test_a_last4_without_bank_evidence_only_suggests():
    ev = Evidence(
        layout_fingerprint="pdf:unknown:credit_card", kind="credit_card", last4="4242", label="pdf"
    )
    m = match_account(ev, [acct("barc", "barclaycard", "credit_card", "4242")], [])
    assert m.account_id is None and m.best_guess == "barc" and m.candidates == ["barc"]
    # an OFX/CAMT fingerprint is tied to one account, so its last 4 can decide
    ofx = ev.model_copy(update={"layout_fingerprint": "ofx:-:abc"})
    assert match_account(ofx, [acct("barc", "barclaycard", "credit_card", "4242")], []).account_id


@pytest.mark.parametrize(
    "text,last4",
    [
        ("Card ending 4242 29 Sep 2026", "4242"),
        ("Card ending 4242 29 Sep 2026 statement", "4242"),
        ("Account number 12345678 12 Sep 2026", "5678"),
        ("Account number 12345678 28/10/2026", "5678"),
        ("Account number: 12345678   1 of 3", "5678"),
        ("Card number 4929 1234 5678 9012 29 Sep", "9012"),
        ("Card number **** **** **** 9999", "9999"),
        ("Account number 20-00-00 12345678", "5678"),
    ],
)
def test_last4_comes_only_from_the_numbers_own_token(text, last4):
    assert header_facts(text).last4 == last4


def test_fingerprints_are_keyed_per_install_and_normalised(fixtures):
    registry = LayoutRegistry(PACK)

    def fp(key, relative="ofx/current.ofx"):
        doc = extract_document(
            fixtures / relative,
            "ofx",
            sha256="x",
            limits=LIMITS,
            known_header=registry.is_known_header,
        )
        return identify(doc, pack=PACK, registry=registry, key=key).layout_fingerprint

    assert fp(KEY) == fp(KEY)
    assert fp(KEY) != fp(b"j" * 32)
    unkeyed = hashlib.sha256(b"22222222").hexdigest()[:8]
    assert unkeyed not in fp(KEY)


def test_identifier_normalisation():
    assert _short_hash("gb29 nwbk 6016 1331 9268 19", KEY) == _short_hash(
        "GB29NWBK60161331926819", KEY
    )


def test_install_key_is_created_once_and_stays_private(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    first = fingerprint_key(db)
    assert len(first) == 32 and fingerprint_key(db) == first
    other = Database(tmp_path / "u.db")
    migrate(other, tmp_path / "c")
    assert fingerprint_key(other) != first


def test_closed_accounts_are_ignored_and_no_accounts_is_explained():
    assert (
        match_account(MONZO, [acct("old", "monzo", "current", status="closed")], []).reason
        == "You haven't added any accounts yet."
    )
