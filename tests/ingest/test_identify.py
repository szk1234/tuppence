from datetime import date

import pytest

from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.identify import AccountRef, Evidence, header_facts, identify, match_account
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack

PACK = load_bank_pack()
LIMITS = ExtractLimits()


def evidence_for(fixtures, relative, kind):
    registry = LayoutRegistry(PACK)
    doc = extract_document(
        fixtures / relative, kind, sha256="x", limits=LIMITS, known_header=registry.is_known_header
    )
    return identify(doc, pack=PACK, registry=registry)


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


def test_memory_decides_for_an_unknown_layout_with_one_answer():
    unknown = Evidence(layout_fingerprint="csv:new", label="CSV file in a new layout")
    accounts = [acct("a1", "monzo", "current"), acct("cu", "other", "current")]
    asked = match_account(unknown, accounts, [])
    assert (
        asked.account_id is None
        and asked.best_guess is None
        and asked.reason == "More than one of your accounts could match."
    )
    assert match_account(unknown, accounts, ["cu"]).account_id == "cu"
    assert (
        match_account(unknown, accounts, ["cu", "a1"]).account_id is None
    )  # answered differently before


def test_closed_accounts_are_ignored_and_no_accounts_is_explained():
    assert (
        match_account(MONZO, [acct("old", "monzo", "current", status="closed")], []).reason
        == "You haven't added any accounts yet."
    )
