from datetime import date
from typing import Any, cast

import pytest

from tuppence.core.accounts import AccountIn, AccountService
from tuppence.core.db import Database
from tuppence.core.debts import DebtIn, DebtService
from tuppence.core.errors import InputError
from tuppence.core.goals import GoalService
from tuppence.core.household import HouseholdPatch, HouseholdService, PersonIn
from tuppence.core.income import IncomeIn, IncomeService
from tuppence.core.migrate import migrate
from tuppence.core.onboarding import STEPS, OnboardingService
from tuppence.core.settings_store import SettingsStore
from tuppence.core.timeline import Timeline
from tuppence.llm.routing import TaskRouter

TODAY = date(2026, 10, 7)


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    household = HouseholdService(db, today=lambda: TODAY)
    timeline = Timeline(db, today=lambda: TODAY)
    accounts = AccountService(db, household)
    settings = SettingsStore(db)
    svc = OnboardingService(
        db,
        household,
        timeline,
        accounts,
        IncomeService(db, household, accounts, today=lambda: TODAY),
        DebtService(db, household, today=lambda: TODAY),
        GoalService(db, today=lambda: TODAY),
        settings,
        TaskRouter(db, settings, cast(Any, None)),
        today=lambda: TODAY,
    )
    return svc, household, timeline, accounts, settings


def ids(state):
    return {p.id for p in state.prompts}


def test_fresh_state(env):
    svc = env[0]
    s = svc.state()
    assert [x.id for x in s.steps] == STEPS and all(x.status == "todo" for x in s.steps)
    assert s.steps[1].title == "Who's in your household"
    assert not s.started and not s.finished and s.next_step == "welcome"
    assert s.completeness == 0  # nothing satisfied, so low
    assert {"nation", "postcode", "income", "accounts", "housing_tenure", "ai_model"} <= ids(s)
    nation = next(p for p in s.prompts if p.id == "nation")
    assert nation.link == "/settings/household" and "tax bands" in nation.unlocks


def test_mark_moves_next_step_and_skip_counts(env):
    svc = env[0]
    s = svc.mark("welcome", "done")
    assert s.started and s.next_step == "household"
    s = svc.mark("household", "skipped")
    assert s.next_step == "work_income" and s.steps[1].status == "skipped"
    assert s.completeness == round(100 * 2 / 15)
    # idempotent per step, last write wins
    s = svc.mark("household", "done")
    assert s.steps[1].status == "done" and sum(x.status != "todo" for x in s.steps) == 2
    with pytest.raises(InputError):
        svc.mark("nope", "done")
    with pytest.raises(InputError):
        svc.mark("welcome", "todo")  # type: ignore[arg-type]


def test_all_steps_finish_and_reset(env):
    svc = env[0]
    for step in STEPS:
        s = svc.mark(step, "skipped")
    assert s.finished and s.next_step is None
    r = svc.reset()
    assert not r.started and not r.finished and r.next_step == "welcome"


def test_child_without_birth_year_and_adult_prompts(env):
    svc, household, timeline, *_ = env
    kid = household.create_person(PersonIn(display_name="Sam", role="child"))
    alex = household.create_person(PersonIn(display_name="Alex", role="adult"))
    s = svc.state()
    texts = {p.id: p for p in s.prompts}
    assert texts[f"birth_year:{kid.id}"].text == "Add Sam's birth year"
    assert "Tax-Free Childcare" in texts[f"birth_year:{kid.id}"].unlocks
    assert texts[f"employment:{alex.id}"].text == "Add Alex's work status"
    assert "income_band:" + alex.id in texts
    timeline.set("person", alex.id, "employment_status", "employed", date(2020, 1, 1))
    timeline.set("person", alex.id, "income_band", "12570_50270", date(2020, 1, 1))
    from tuppence.core.household import PersonPatch

    household.update_person(kid.id, PersonPatch(birth_year=2018), kid.version)
    s = svc.state()
    assert f"employment:{alex.id}" not in ids(s) and f"income_band:{alex.id}" not in ids(s)
    assert f"birth_year:{kid.id}" not in ids(s)


def test_retired_people_never_prompt_and_their_income_is_ignored(env):
    svc, household, _, accounts, _ = env
    gone = household.create_person(PersonIn(display_name="Gone", role="adult"))
    svc.income.create(
        IncomeIn(
            person_id=gone.id,
            kind="salary",
            name="Old job",
            net_amount="1000",
            pay_rule={"type": "monthly_day", "day": 25, "adjust": "previous_working_day"},
        )
    )
    assert "income" not in ids(svc.state())
    household.retire_person(gone.id, 1)
    s = svc.state()
    assert not any(gone.id in i for i in ids(s))
    assert "income" in ids(s)  # a person who left no longer counts as income


def test_card_without_apr_prompts(env):
    svc, household, _, accounts, _ = env
    a = household.create_person(PersonIn(display_name="Alex", role="adult"))
    card = accounts.create(
        AccountIn(
            provider="other",
            provider_name="Bank",
            kind="credit_card",
            nickname="Everyday card",
            owner_ids=[a.id],
        )
    )
    s = svc.state()
    p = next(p for p in s.prompts if p.id == f"card_apr:{card.id}")
    assert p.text == "Add the interest rate for Everyday card" and "interest-cost" in p.unlocks
    assert "accounts" not in ids(s)
    accounts.update(card.id, {"purchase_apr": 22.9}, card.version)
    assert f"card_apr:{card.id}" not in ids(svc.state())


def test_filling_in_the_profile_clears_prompts_and_raises_completeness(env):
    svc, household, timeline, accounts, settings = env
    low = svc.state().completeness
    a = household.create_person(PersonIn(display_name="Alex", role="adult"))
    h = household.update(HouseholdPatch(nation="england", postcode_district="LS6"), 1)
    assert h.nation == "england"
    timeline.set("person", a.id, "employment_status", "employed", date(2020, 1, 1))
    timeline.set("person", a.id, "income_band", "12570_50270", date(2020, 1, 1))
    timeline.set("household", "1", "housing_tenure", "renting", date(2020, 1, 1))
    acc = accounts.create(
        AccountIn(
            provider="other",
            provider_name="Bank",
            kind="current",
            nickname="Main",
            owner_ids=[a.id],
        )
    )
    inc_svc = svc.income
    inc_svc.create(
        IncomeIn(
            person_id=a.id,
            kind="salary",
            name="Pay",
            net_amount="2000",
            account_id=acc.id,
            pay_rule={"type": "monthly_day", "day": 25, "adjust": "previous_working_day"},
        )
    )
    settings.set("llm.simple_model", {"connection_id": "c1", "model_id": "m"}, expected_version=0)
    s = svc.state()
    assert s.prompts == []
    assert s.completeness == round(100 * 8 / 17) and s.completeness > low  # 8 checks, 9 steps
    assert s.completeness < 70
    for step in STEPS:
        s = svc.mark(step, "done")
    assert s.completeness == 100 and s.finished


def test_advanced_route_chain_counts_as_ai_model(env):
    svc, _, _, _, settings = env
    assert "ai_model" in ids(svc.state())
    with svc.db.transaction() as conn:
        conn.execute(
            "INSERT INTO llm_route (task, chain, local_only, version, updated_at)"
            ' VALUES (\'coach\', \'[{"connection_id": "c", "model_id": "m"}]\', 0, 1, \'x\')'
        )
    assert "ai_model" not in ids(svc.state())  # a chain counts in either mode


def _fill(env):
    svc, household, timeline, accounts, settings = env
    a = household.create_person(PersonIn(display_name="Alex", role="adult"))
    household.update(HouseholdPatch(nation="england", postcode_district="LS6"), 1)
    timeline.set("person", a.id, "employment_status", "employed", date(2020, 1, 1))
    timeline.set("person", a.id, "income_band", "12570_50270", date(2020, 1, 1))
    timeline.set("household", "1", "housing_tenure", "renting", date(2020, 1, 1))
    acc = accounts.create(
        AccountIn(
            provider="other", provider_name="B", kind="current", nickname="M", owner_ids=[a.id]
        )
    )
    svc.income.create(
        IncomeIn(
            person_id=a.id,
            kind="salary",
            name="Pay",
            net_amount="2000",
            account_id=acc.id,
            pay_rule={"type": "monthly_day", "day": 25, "adjust": "previous_working_day"},
        )
    )
    settings.set("llm.simple_model", {"connection_id": "c1", "model_id": "m"}, expected_version=0)
    return a


def test_realistic_profile_with_some_steps_reaches_70(env):
    svc = env[0]
    _fill(env)
    for step in STEPS[:4]:
        svc.mark(step, "done")
    assert svc.state().completeness == round(100 * 12 / 17) >= 70


def test_adding_people_or_cards_can_lower_completeness(env):
    svc, household, *_ = env
    _fill(env)
    before = svc.state().completeness
    household.create_person(PersonIn(display_name="Sam", role="child"))
    assert svc.state().completeness < before


def test_dependent_adults_have_no_prompts(env):
    svc, household, *_ = env
    d = household.create_person(PersonIn(display_name="Nan", role="dependent_adult"))
    assert not any(d.id in i for i in ids(svc.state()))


def test_person_prompts_deep_link_to_the_person_on_household(env):
    svc, household, *_ = env
    kid = household.create_person(PersonIn(display_name="Sam", role="child"))
    alex = household.create_person(PersonIn(display_name="Alex", role="adult"))
    links = {p.id: p.link for p in svc.state().prompts}
    assert links[f"birth_year:{kid.id}"] == f"/settings/household#person-{kid.id}"
    assert links[f"employment:{alex.id}"] == f"/settings/household#person-{alex.id}"
    assert links[f"income_band:{alex.id}"] == f"/settings/household#person-{alex.id}"


def test_prefer_not_to_say_answers_the_income_band_prompt(env):
    svc, household, timeline, *_ = env
    alex = household.create_person(PersonIn(display_name="Alex", role="adult"))
    timeline.set("person", alex.id, "income_band", "prefer_not_to_say", TODAY)
    assert f"income_band:{alex.id}" not in ids(svc.state())


def test_dependent_adults_may_record_work_and_income_but_are_never_prompted(env):
    svc, household, timeline, *_ = env
    d = household.create_person(PersonIn(display_name="Nan", role="dependent_adult"))
    timeline.set("person", d.id, "employment_status", "retired", TODAY)
    timeline.set("person", d.id, "income_band", "under_12570", TODAY)
    assert not any(d.id in i for i in ids(svc.state()))


def test_student_loan_without_a_plan_prompts(env):
    svc = env[0]
    loan = svc.debts.create(
        DebtIn(kind="student_loan", lender="Student Loans Company", balance="1")
    )
    p = next(p for p in svc.state().prompts if p.id == f"student_loan_plan:{loan.id}")
    assert p.text == "Add which plan your Student Loans Company loan is on"
    assert p.link == "/settings/debts" and "repayment" in p.unlocks
    svc.debts.update(loan.id, {"student_loan_plan": "plan2"}, loan.version)
    assert f"student_loan_plan:{loan.id}" not in ids(svc.state())
