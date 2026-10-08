from datetime import date

from agents.helpers import manifest
from tuppence.agents.transfers import Movement, TransferMatcher, pair_transfers
from tuppence.ingest.store import StatementStore


def mv(i, account, day, pence, text="", kind="current"):
    return Movement(i, account, kind, date(2026, 10, day), pence, text)


def test_opposite_equal_amounts_on_two_accounts_within_three_days_pair():
    moves = [
        mv("o1", "cur", 5, -20000, "TO RAINY DAY"),
        mv("i1", "sav", 6, 20000, "FROM CURRENT"),
        mv("o2", "cur", 9, -15000, "EXAMPLE CARD CO"),
        mv("i2", "card", 12, 15000, "PAYMENT RECEIVED - THANK YOU", kind="credit_card"),
        mv("x1", "cur", 20, -5000, "PAT EXAMPLE"),
        mv("x2", "sav", 25, 5000, "PAT EXAMPLE"),  # five days apart: no
        mv("y1", "cur", 21, -999, "SAME ACCOUNT"),
        mv("y2", "cur", 21, 999, "REFUND"),  # the same account: no
    ]
    pairs = pair_transfers(moves, scope={m.id for m in moves})
    assert {(p.out_id, p.in_id) for p in pairs} == {("o1", "i1"), ("o2", "i2")}
    card = next(p for p in pairs if p.in_id == "i2")
    assert card.card_repayment and card.days_apart == 3 and card.confidence == 0.99


def test_closest_dates_then_words_win_and_each_side_pairs_once():
    moves = [
        mv("o1", "cur", 5, -5000, "MOVE MONEY"),
        mv("o2", "cur", 7, -5000, "TRANSFER TO SAVINGS"),
        mv("i1", "sav", 7, 5000, "FROM CURRENT"),
    ]
    pairs = pair_transfers(moves, scope={"i1"})
    assert [(p.out_id, p.in_id) for p in pairs] == [("o2", "i1")]
    assert pairs[0].words == ("TRANSFER", "SAVINGS")


def test_at_least_one_side_must_be_in_scope():
    moves = [mv("o1", "cur", 5, -100, ""), mv("i1", "sav", 5, 100, "")]
    assert pair_transfers(moves, scope=set()) == []
    only = pair_transfers(moves, scope={"o1"})
    assert len(only) == 1 and only[0].confidence == 0.85


def test_matcher_marks_both_sides_and_never_the_persons_rows(aenv):
    aenv.add_account("a_savings", "savings", owners=["p_alex"], nickname="Rainy day")
    aenv.add_account(
        "a_card", "credit_card", owners=["p_alex"], nickname="Example Card", provider="other"
    )
    out = aenv.add_txn(date(2026, 10, 5), -20000, "TO RAINY DAY")
    arrive = aenv.add_txn(date(2026, 10, 5), 20000, "FROM CURRENT", account_id="a_savings")
    pay = aenv.add_txn(date(2026, 10, 9), -15000, "EXAMPLE CARD CO")
    received = aenv.add_txn(date(2026, 10, 10), 15000, "PAYMENT RECEIVED", account_id="a_card")
    gift = aenv.add_txn(date(2026, 10, 12), -3000, "PAT EXAMPLE")
    back = aenv.add_txn(date(2026, 10, 12), 3000, "PAT EXAMPLE", account_id="a_savings")
    aenv.understanding.set_by_person(gift, expected_version=1, category_id="gifts.presents")
    matcher = TransferMatcher(
        aenv.db, aenv.understanding, aenv.versions, lambda: manifest("transfer_matcher")
    )
    counts = matcher.run([out, arrive, pay, received, gift, back], run_id="r1")
    assert counts["pairs"] == 2
    a, b = aenv.understanding.get(out), aenv.understanding.get(arrive)
    assert a.is_transfer and a.transfer_pair_id == arrive and b.transfer_pair_id == out
    assert a.category_id == "transfers.between-accounts" and a.decided_by == "rule"
    assert aenv.understanding.get(received).category_id == "transfers.card-repayment"
    assert aenv.understanding.get(gift).category_id == "gifts.presents"
    assert not aenv.understanding.get(back).is_transfer


def test_one_sided_transfer_to_an_account_without_statements(aenv):
    aenv.add_account("a_savings", "savings", owners=["p_alex"], nickname="Rainy day", last4="4321")
    t = aenv.add_txn(date(2026, 10, 5), -20000, "TRANSFER TO ****4321")
    matcher = TransferMatcher(
        aenv.db, aenv.understanding, aenv.versions, lambda: manifest("transfer_matcher")
    )
    assert matcher.run([t], run_id="r1")["one_sided"] == 1
    row = aenv.understanding.get(t)
    assert row.is_transfer and row.transfer_pair_id is None and row.confidence == 0.85
    aenv.add_statement("a_savings", date(2026, 10, 1), date(2026, 10, 31))
    t2 = aenv.add_txn(date(2026, 10, 6), -100, "TRANSFER TO ****4321")
    assert matcher.run([t2], run_id="r2")["one_sided"] == 0  # its statement is in: no match


def test_removing_one_side_releases_the_other(aenv):
    aenv.add_account("a_savings", "savings", owners=["p_alex"])
    savings_statement = aenv.add_statement("a_savings")
    out = aenv.add_txn(date(2026, 10, 5), -20000, "TRANSFER")
    arrive = aenv.add_txn(
        date(2026, 10, 5), 20000, "TRANSFER", account_id="a_savings", statement_id=savings_statement
    )
    matcher = TransferMatcher(
        aenv.db, aenv.understanding, aenv.versions, lambda: manifest("transfer_matcher")
    )
    matcher.run([out, arrive], run_id="r1")
    StatementStore(aenv.db).delete(savings_statement)
    assert matcher.run([], run_id="r2")["released"] == 1
    row = aenv.understanding.get(out)
    assert row.status == "unknown" and not row.is_transfer and row.waiting == "queued"


def test_a_coincidence_is_only_a_guess_and_not_a_transfer_sticks(aenv):
    aenv.add_account("a_card", "credit_card", owners=["p_alex"], nickname="Example Card")
    friend = aenv.add_txn(date(2026, 10, 3), -5000, "PAT EXAMPLE")
    refund = aenv.add_txn(date(2026, 10, 5), 5000, "HARBOUR PHARMACY REFUND", account_id="a_card")
    matcher = TransferMatcher(
        aenv.db, aenv.understanding, aenv.versions, lambda: manifest("transfer_matcher")
    )
    matcher.run([friend, refund], run_id="r1")
    guess = aenv.understanding.get(friend)
    assert guess.is_transfer and guess.status == "guessed" and guess.confidence == 0.6
    aenv.understanding.set_by_person(
        friend, expected_version=guess.version, is_transfer=False, category_id="gifts.presents"
    )
    assert aenv.understanding.get(refund).status == "unknown"  # its pair was undone
    matcher.run([friend, refund], run_id="r2")
    assert not aenv.understanding.get(friend).is_transfer
    assert not aenv.understanding.get(refund).is_transfer


def _matcher(aenv):
    return TransferMatcher(
        aenv.db, aenv.understanding, aenv.versions, lambda: manifest("transfer_matcher")
    )


def test_a_one_sided_transfer_is_paired_when_the_other_statement_arrives(aenv):
    aenv.add_account("a_savings", "savings", owners=["p_alex"], nickname="Rainy day", last4="4321")
    out = aenv.add_txn(date(2026, 10, 5), -20000, "TRANSFER TO ****4321")
    matcher = _matcher(aenv)
    assert matcher.run([out], run_id="r1")["one_sided"] == 1
    one = aenv.understanding.get(out)
    assert one.evidence["kind"] == "transfer_one_sided" and one.transfer_pair_id is None
    # the savings statement is uploaded: the other half arrives
    arrive = aenv.add_txn(date(2026, 10, 6), 20000, "FROM CURRENT", account_id="a_savings")
    counts = matcher.run([arrive], run_id="r2")
    assert counts["pairs"] == 1
    a, b = aenv.understanding.get(out), aenv.understanding.get(arrive)
    assert a.transfer_pair_id == arrive and b.transfer_pair_id == out
    assert a.evidence["kind"] == "transfer_pair" and a.is_transfer and b.is_transfer


def test_a_restored_confirmed_transfer_is_never_modified_or_paired_against(aenv):
    aenv.add_account("a_savings", "savings", owners=["p_alex"])
    out = aenv.add_txn(date(2026, 10, 5), -20000, "TRANSFER")
    aenv.understanding.set_by_person(out, expected_version=1, is_transfer=True)
    before = aenv.understanding.get(out)
    assert before.status == "confirmed" and before.transfer_pair_id is None
    arrive = aenv.add_txn(date(2026, 10, 5), 20000, "TRANSFER", account_id="a_savings")
    counts = _matcher(aenv).run([out, arrive], run_id="r1")
    assert counts["pairs"] == 0
    assert aenv.understanding.get(out) == before
    assert aenv.understanding.get(arrive).transfer_pair_id is None
    assert not aenv.understanding.get(arrive).is_transfer


def _paired(aenv):
    aenv.add_account("a_savings", "savings", owners=["p_alex"])
    statement = aenv.add_statement("a_savings")
    out = aenv.add_txn(date(2026, 10, 5), -20000, "TRANSFER")
    arrive = aenv.add_txn(
        date(2026, 10, 5), 20000, "TRANSFER", account_id="a_savings", statement_id=statement
    )
    _matcher(aenv).run([out, arrive], run_id="r1")
    assert aenv.understanding.get(out).transfer_pair_id == arrive
    return out, arrive, statement


def test_releasing_one_side_releases_its_partner(aenv):
    out, arrive, _ = _paired(aenv)
    with aenv.db.transaction() as conn:
        assert aenv.understanding.release(conn, out, actor="test", reason="basis gone")
    for t in (out, arrive):
        row = aenv.understanding.get(t)
        assert row.status == "unknown" and not row.is_transfer and row.transfer_pair_id is None
        assert row.waiting == "queued"


def test_retiring_the_transfer_category_releases_both_sides(aenv):
    out, arrive, _ = _paired(aenv)
    aenv.categories.retire(
        "transfers.between-accounts", aenv.categories.get("transfers.between-accounts").version
    )
    aenv.categorise([out])
    for t in (out, arrive):
        row = aenv.understanding.get(t)
        assert row.transfer_pair_id is None and not row.is_transfer
    assert aenv.understanding.get(arrive).status == "unknown"  # out of scope: only released


def test_removing_a_statement_leaves_no_half_pair(aenv):
    out, arrive, statement = _paired(aenv)
    StatementStore(aenv.db).delete(statement)
    assert _matcher(aenv).run([], run_id="r2")["released"] == 1
    row = aenv.understanding.get(out)
    assert row.transfer_pair_id is None and not row.is_transfer and row.status == "unknown"


def test_a_person_unmarking_one_side_releases_the_other(aenv):
    out, arrive, _ = _paired(aenv)
    row = aenv.understanding.get(out)
    aenv.understanding.set_by_person(
        out, expected_version=row.version, is_transfer=False, category_id="gifts.presents"
    )
    other = aenv.understanding.get(arrive)
    assert other.status == "unknown" and not other.is_transfer and other.transfer_pair_id is None


def test_the_person_letting_tuppence_decide_again_releases_the_partner(aenv):
    out, arrive, _ = _paired(aenv)
    aenv.understanding.set_by_person(out, expected_version=2, is_transfer=True)
    row = aenv.understanding.get(out)
    aenv.understanding.release_by_person(out, expected_version=row.version)
    for t in (out, arrive):
        r = aenv.understanding.get(t)
        assert r.status == "unknown" and not r.is_transfer and r.transfer_pair_id is None


def test_a_confirmed_partner_is_left_alone_and_the_release_says_so(aenv):
    out, arrive, _ = _paired(aenv)
    aenv.understanding.set_by_person(arrive, expected_version=2, is_transfer=True)
    kept = aenv.understanding.get(arrive)
    with aenv.db.transaction() as conn:
        assert aenv.understanding.release(conn, out, actor="test", reason="basis gone")
    assert aenv.understanding.get(arrive) == kept
    assert aenv.understanding.get(out).evidence["partner_confirmed"] == arrive
