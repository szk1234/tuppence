"""Onboarding progress, the profile completeness meter and the "unlock" prompts (spec §5.1).

Completeness formula (a percentage, 0-100):

    completeness = round(100 * satisfied / total)

where `total` is the number of profile checks plus the number of wizard steps, and `satisfied`
is how many of those are met. Every check and every step is worth one point:

* Household checks (always present): nation, postcode district, housing tenure.
* Data checks (always present): at least one income source, at least one account, an AI model
  chosen (`llm.simple_model` in simple mode, or any task chain in advanced mode).
* Per active adult: work status and income band. Per active child: birth year.
  Every active credit card also adds one check: its purchase APR.
* Dependent adults get no checks: they have no work or income questions of their own and no
  birth-year-driven entitlement checks, so a prompt would only nag.
* One per wizard step that is done or skipped (skipping counts as progress).

People who have left the household never add checks, and incomes of people who left do not
count. The checks grow with the household, so the meter never rewards an empty profile (and, by
design, adding an adult, a child or a card can lower the percentage until its new checks are met):
a fresh install scores 0, and a typical household reaches the 70% "well set up" mark only once most
checks are met. Each unmet check is also an unlock prompt (what it enables and where to fix it).

Step progress has no `version`: only this service writes it, `mark()` is idempotent per step
(the last write wins) and there is nothing for a stale tab to overwrite.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from tuppence.core.accounts import AccountService
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.debts import DebtService
from tuppence.core.errors import InputError
from tuppence.core.goals import GoalService
from tuppence.core.household import HouseholdService
from tuppence.core.income import IncomeService
from tuppence.core.settings_store import SettingsStore
from tuppence.core.timeline import Timeline
from tuppence.llm.routing import TaskRouter

STEPS = [
    "welcome",
    "household",
    "work_income",
    "home",
    "accounts",
    "debts",
    "goals",
    "ai",
    "first_upload",
]
TITLES = {
    "welcome": "Welcome",
    "household": "Who's in your household",
    "work_income": "Work and income",
    "home": "Your home",
    "accounts": "Accounts and cards",
    "debts": "Loans and debts",
    "goals": "What you're saving for",
    "ai": "Choose your AI",
    "first_upload": "Your first statements",
}
HOUSEHOLD_LINK = "/settings/household"

StepStatus = Literal["todo", "done", "skipped"]


class Step(BaseModel):
    id: str
    title: str
    status: StepStatus


class Prompt(BaseModel):
    id: str
    text: str
    unlocks: str
    link: str


class OnboardingState(BaseModel):
    steps: list[Step]
    next_step: str | None
    started: bool
    finished: bool
    completeness: int
    prompts: list[Prompt]


class OnboardingService:
    """`router` is how the AI choice is read (the same view the AI settings page uses)."""

    def __init__(
        self,
        db: Database,
        household: HouseholdService,
        timeline: Timeline,
        accounts: AccountService,
        income: IncomeService,
        debts: DebtService,
        goals: GoalService,
        settings: SettingsStore,
        router: TaskRouter,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.db = db
        self.household = household
        self.timeline = timeline
        self.accounts = accounts
        self.income = income
        self.debts = debts
        self.goals = goals
        self.settings = settings
        self.router = router
        self.today = today

    def _statuses(self) -> dict[str, StepStatus]:
        with self.db.connection() as conn:
            return {r["step"]: r["status"] for r in conn.execute("SELECT * FROM onboarding_step")}

    def ai_model_chosen(self) -> bool:
        view = self.router.view()
        return view.simple_model is not None or any(t.chain for t in view.tasks.values())

    def _checks(self) -> list[tuple[bool, Prompt]]:
        """Every profile check as (met, the prompt to show while it isn't)."""
        day = self.today()
        home = self.timeline.as_of("household", "1", day)
        h = self.household.get()
        checks: list[tuple[bool, Prompt]] = []

        def add(met: bool, id: str, text: str, unlocks: str, link: str = HOUSEHOLD_LINK) -> None:
            checks.append((met, Prompt(id=id, text=text, unlocks=unlocks, link=link)))

        add(
            h.nation is not None,
            "nation",
            "Tell us which UK nation you live in",
            "the right tax bands and bank holidays",
        )
        add(
            bool(h.postcode_district),
            "postcode",
            "Add your postcode district (like LS6)",
            "local rent comparisons",
        )
        for p in self.household.list_people():
            attrs: dict[str, Any] = self.timeline.as_of("person", p.id, day)
            if p.role == "adult":
                add(
                    "employment_status" in attrs,
                    f"employment:{p.id}",
                    f"Add {p.display_name}'s work status",
                    "tax and benefit checks",
                )
                add(
                    "income_band" in attrs,
                    f"income_band:{p.id}",
                    f"Add {p.display_name}'s income band",
                    "Marriage Allowance and Child Benefit checks",
                )
            elif p.role == "child":
                add(
                    p.birth_year is not None,
                    f"birth_year:{p.id}",
                    f"Add {p.display_name}'s birth year",
                    "Tax-Free Childcare and funded childcare checks",
                )
        add(
            bool(self.income.list()),
            "income",
            "Add where your money comes from",
            "payday-to-payday budgets",
            "/settings/income",
        )
        accounts = self.accounts.list()
        add(
            bool(accounts),
            "accounts",
            "Add your accounts",
            "statement import",
            "/settings/accounts",
        )
        for a in accounts:
            if a.kind == "credit_card":
                add(
                    a.purchase_apr is not None,
                    f"card_apr:{a.id}",
                    f"Add the interest rate for {a.nickname}",
                    "interest-cost estimates",
                    "/settings/accounts",
                )
        add(
            "housing_tenure" in home,
            "housing_tenure",
            "Tell us whether you rent or own your home",
            "housing cost checks",
        )
        add(
            self.ai_model_chosen(),
            "ai_model",
            "Choose an AI model",
            "statement understanding and the coach",
            "/settings/ai",
        )
        return checks

    def state(self) -> OnboardingState:
        recorded = self._statuses()
        steps = [Step(id=s, title=TITLES[s], status=recorded.get(s, "todo")) for s in STEPS]
        checks = self._checks()
        progressed = sum(1 for s in steps if s.status != "todo")
        total = len(checks) + len(steps)
        satisfied = sum(1 for met, _ in checks if met) + progressed
        return OnboardingState(
            steps=steps,
            next_step=next((s.id for s in steps if s.status == "todo"), None),
            started=progressed > 0,
            finished=progressed == len(steps),
            completeness=round(100 * satisfied / total),
            prompts=[p for met, p in checks if not met],
        )

    def mark(self, step: str, status: str) -> OnboardingState:
        if step not in STEPS:
            raise InputError(f"'{step}' isn't a setup step.")
        if status not in ("done", "skipped"):
            raise InputError("A step is either done or skipped.")
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO onboarding_step (step, status, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(step) DO UPDATE SET status = excluded.status,"
                " updated_at = excluded.updated_at",
                [step, status, to_iso(utcnow())],
            )
        return self.state()

    def reset(self) -> OnboardingState:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM onboarding_step")
        return self.state()
