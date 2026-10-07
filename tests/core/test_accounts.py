import pytest

from tuppence.core.accounts import AccountIn, AccountService
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.household import HouseholdService, PersonIn
from tuppence.core.migrate import migrate
from tuppence.core.records import VersionConflict


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    hh = HouseholdService(db)
    a = hh.create_person(PersonIn(display_name="Alex Example", role="adult"))
    b = hh.create_person(PersonIn(display_name="Sam Example", role="adult"))
    return AccountService(db, hh), a, b


def test_joint_current_account(env):
    svc, a, b = env
    acct = svc.create(
        AccountIn(
            provider="monzo", kind="current", nickname="Joint", last4="1234", owner_ids=[a.id, b.id]
        )
    )
    assert acct.joint and acct.provider_name == "Monzo"
    assert sorted(acct.owner_ids) == sorted([a.id, b.id])


def test_card_fields(env):
    svc, a, _ = env
    card = svc.create(
        AccountIn(
            provider="barclaycard",
            kind="credit_card",
            nickname="Barclaycard",
            owner_ids=[a.id],
            credit_limit="£2,500",
            purchase_apr=24.9,
            promo_apr=0,
            promo_end="2027-03-31",
            statement_day=12,
        )
    )
    assert card.credit_limit == "2500.00" and card.promo_end.isoformat() == "2027-03-31"
    with pytest.raises(InputError):
        svc.create(
            AccountIn(
                provider="monzo",
                kind="current",
                nickname="X",
                owner_ids=[a.id],
                credit_limit="100",
            )
        )


def test_validation(env):
    svc, a, _ = env
    with pytest.raises(ValueError):
        AccountIn(provider="monzo", kind="current", nickname="X", owner_ids=[a.id], last4="12a4")
    with pytest.raises(InputError):
        svc.create(
            AccountIn(provider="monzo", kind="current", nickname="X", owner_ids=["p_nobody"])
        )
    with pytest.raises(InputError):
        svc.create(AccountIn(provider="other", kind="current", nickname="X", owner_ids=[a.id]))
    named = svc.create(
        AccountIn(
            provider="other",
            provider_name="Village Credit Union",
            kind="savings",
            nickname="Pot",
            owner_ids=[a.id],
        )
    )
    assert named.provider_name == "Village Credit Union"


def test_close_reopen_and_versions(env):
    svc, a, _ = env
    acct = svc.create(
        AccountIn(provider="hsbc", kind="current", nickname="Bills", owner_ids=[a.id])
    )
    closed = svc.close(acct.id, expected_version=1)
    assert (
        closed.status == "closed" and svc.list() == [] and len(svc.list(include_closed=True)) == 1
    )
    assert closed.owner_ids == [a.id]
    with pytest.raises(VersionConflict):
        svc.reopen(acct.id, expected_version=1)
    assert svc.reopen(acct.id, expected_version=2).status == "active"


def test_owner_only_update_bumps_version(env):
    svc, a, b = env
    acct = svc.create(
        AccountIn(provider="hsbc", kind="current", nickname="Bills", owner_ids=[a.id])
    )
    out = svc.update(acct.id, {"owner_ids": [a.id, b.id]}, 1)
    assert out.version == 2 and out.joint
    with pytest.raises(VersionConflict):
        svc.update(acct.id, {"owner_ids": [b.id]}, 1)
    with pytest.raises(InputError):
        svc.update(acct.id, {"owner_ids": []}, 2)


def test_update_cross_field_rules_and_noop(env):
    svc, a, _ = env
    acct = svc.create(
        AccountIn(provider="hsbc", kind="current", nickname="Bills", owner_ids=[a.id])
    )
    with pytest.raises(InputError):
        svc.update(acct.id, {"statement_day": 5}, 1)
    with pytest.raises(InputError):
        svc.update(acct.id, {"provider": "other"}, 1)
    assert svc.update(acct.id, {"nickname": "Bills"}, 1).version == 1
    with pytest.raises(VersionConflict):
        svc.update(acct.id, {"nickname": "Bills"}, 7)
    renamed = svc.update(acct.id, {"nickname": "Household", "last4": "9876"}, 1)
    assert renamed.version == 2 and renamed.last4 == "9876"
    card = svc.create(
        AccountIn(
            provider="amex",
            kind="credit_card",
            nickname="Amex",
            owner_ids=[a.id],
            credit_limit="1000",
        )
    )
    assert svc.update(card.id, {"credit_limit": "1,500.50"}, 1).credit_limit == "1500.50"


def test_any_kind_for_any_provider(env):
    svc, a, _ = env
    acct = svc.create(AccountIn(provider="amex", kind="savings", nickname="Save", owner_ids=[a.id]))
    assert acct.kind == "savings"
    assert svc.update(acct.id, {"nickname": "Saver"}, 1).version == 2


def test_retired_owner_kept_on_edit(env):
    svc, a, b = env
    acct = svc.create(
        AccountIn(provider="hsbc", kind="current", nickname="Joint", owner_ids=[a.id, b.id])
    )
    svc.household.retire_person(b.id, 1)
    out = svc.update(acct.id, {"nickname": "Bills", "owner_ids": [a.id, b.id]}, 1)
    assert out.nickname == "Bills" and out.joint
    c = svc.household.create_person(PersonIn(display_name="Kit Example", role="adult"))
    svc.household.retire_person(c.id, 1)
    with pytest.raises(InputError):
        svc.update(acct.id, {"owner_ids": [a.id, c.id]}, 2)


def test_owner_reorder_is_not_a_change(env):
    svc, a, b = env
    acct = svc.create(
        AccountIn(provider="hsbc", kind="current", nickname="J", owner_ids=[a.id, b.id])
    )
    assert svc.update(acct.id, {"owner_ids": [b.id, a.id]}, 1).version == 1


def test_apr_rules_and_clearing_card_fields(env):
    svc, a, _ = env
    with pytest.raises(ValueError):
        AccountIn(
            provider="amex", kind="credit_card", nickname="C", owner_ids=[a.id], purchase_apr=24.123
        )
    with pytest.raises(ValueError):
        AccountIn(
            provider="amex", kind="credit_card", nickname="C", owner_ids=[a.id], statement_day=32
        )
    with pytest.raises(InputError):
        svc.create(
            AccountIn(
                provider="amex",
                kind="credit_card",
                nickname="C",
                owner_ids=[a.id],
                purchase_apr=10,
                promo_apr=30,
            )
        )
    card = svc.create(
        AccountIn(
            provider="amex", kind="credit_card", nickname="C", owner_ids=[a.id], credit_limit="500"
        )
    )
    cleared = svc.update(card.id, {"credit_limit": None}, 1)
    assert cleared.credit_limit is None and cleared.version == 2


def test_same_status_changes_and_edits_while_closed_are_refused(env):
    svc, a, _ = env
    acct = svc.create(
        AccountIn(provider="hsbc", kind="current", nickname="Bills", owner_ids=[a.id])
    )
    with pytest.raises(InputError, match="already open"):
        svc.reopen(acct.id, acct.version)
    closed = svc.close(acct.id, acct.version)
    with pytest.raises(InputError, match="already closed"):
        svc.close(acct.id, closed.version)
    with pytest.raises(InputError, match="Reopen it"):
        svc.update(acct.id, {"nickname": "Old bills"}, closed.version)
    assert svc.reopen(acct.id, closed.version).status == "active"
