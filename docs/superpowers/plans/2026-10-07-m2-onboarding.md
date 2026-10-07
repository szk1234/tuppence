# Tuppence M2 — Onboarding and Household Finance Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A five-minute onboarding wizard (individual or family, dependants, work and income with pay dates, home, accounts and cards, loans and debts, goals, AI setup) that builds the household's financial skeleton, plus Settings pages to edit every part later and a completeness meter that says what each missing detail unlocks.

**Architecture:** New user-owned tables (accounts, owners, income sources, debts, goals, onboarding steps) with versioned writes; effective-dated household/person attributes go through the M1a `Timeline`. A pure pay-date engine uses the bundled gov.uk bank-holiday calendar for the household's nation. The wizard and Settings pages share form components; the completeness service derives prompts from the data.

**Tech Stack:** Python 3.12, FastAPI, SQLite; Svelte 5; Playwright.

**Spec:** `docs/superpowers/specs/2026-10-07-tuppence-design.md` §5 (all of it is this milestone's spec), §3.4, §10.2, §16 M2. Builds on M1a/M1b.

## Global Constraints

- Everything in the M0, M1a and M1b Global Constraints still applies.
- Onboarding builds the skeleton in about 5 minutes; every step can be skipped, resumed and edited later in Settings (spec §5.1).
- Wizard step ids and order (spec §5.2): `welcome`, `household`, `work_income`, `home`, `accounts`, `debts`, `goals`, `ai`, `first_upload`.
- Nations: `england`, `wales`, `scotland`, `northern_ireland`; England and Wales share the `england-and-wales` bank-holiday calendar.
- Pay rules (spec §5.4): weekly, fortnightly, 4-weekly (from an anchor date); fixed day of month; day *n* moved to the previous working day; last working day; last given weekday of the month. Working day = Monday–Friday and not a bank holiday in the household's nation.
- Accounts: kinds `current`, `savings`, `credit_card` (`cash_wallet` arrives in M6); `last4` exactly 4 digits when given; owners = one or more people (joint when >1). Cards also hold limit, purchase APR, promo APR and promo end date, statement day 1–31.
- Debts: kinds `personal_loan`, `car_finance_pcp`, `car_finance_hp`, `mortgage`, `student_loan`, `bnpl`, `overdraft`, `informal`, `other`; student loan plans `plan1`, `plan2`, `plan4`, `plan5`, `postgraduate`; car finance details: agreement start date, via broker (yes/no), balloon payment, total amount payable, annual mileage allowance; mortgage details: fixed-rate end date.
- Goals: kinds `emergency_fund`, `house_deposit`, `holiday`, `car`, `wedding`, `education`, `retirement`, `debt_free`, `other`; priority 1 (high) – 3 (low). When no emergency fund goal exists, the goals step suggests one.
- Home details (tenure `renting | mortgage | owned | living_with_family`, monthly amount, bedrooms, council tax band A–I) are effective-dated household attributes, so moving home is history, not an overwrite.
- Money is stored as integer pence in new tables (`*_pence` columns); APRs as percentages with up to 2 decimals; the API accepts and returns pounds as decimal strings (`"1450.00"`) to avoid float drift.
- Never ask for full account numbers, sort codes or full postcodes — last 4 digits and postcode district only (spec §5.2, §2 privacy).

## Review Focus

1. **Pay day on a weekend before a bank holiday** (e.g. 25 December 2026 is a Friday; Boxing Day substitute Monday 28 December) — "day 25, previous working day" must give Thursday 24 December 2026. Test in Task 2.
2. **Monthly pay on the 31st in a 30-day month or February** — clamps to the month's last day, then applies the working-day adjustment. Test in Task 2.
3. **Money typed with a £ sign or commas** (`£1,450`, `1450.5`) — accepted and stored exactly as pence; nonsense (`12.345`, `abc`) rejected with a friendly message. Test in Task 1.
4. **Closing a card that has history** — closed accounts disappear from pickers but stay in lists and keep their owners; never deleted. Test in Task 3.
5. **Resume the wizard after closing the window mid-way** — reopening continues at the first unfinished step with earlier answers intact. Test in Task 8.

---

## File Structure

```
src/tuppence/core/money.py              parse_pounds() -> pence, format_pounds()
src/tuppence/core/calendar.py           bank holidays per nation, is_working_day()
src/tuppence/core/payrules.py           PayRule (discriminated union), pay_dates(), next_pay_date()
src/tuppence/core/providers_uk.py       UK bank/card provider list
src/tuppence/core/migrations/0007_finance_profile.sql
src/tuppence/core/accounts.py           AccountService
src/tuppence/core/income.py             IncomeService
src/tuppence/core/debts.py              DebtService
src/tuppence/core/goals.py              GoalService
src/tuppence/core/onboarding.py         OnboardingService (steps + completeness + prompts)
src/tuppence/datapacks/baseline/uk-bank-holidays.json
scripts/build_bank_holidays.py
src/tuppence/app/routes/{accounts,income,debts,goals,onboarding}.py
web/src/lib/money.ts
web/src/components/forms/{AccountForm,IncomeForm,DebtForm,GoalForm,PersonForm,PayRuleField,MoneyInput}.svelte
web/src/pages/Welcome.svelte (wizard shell) + web/src/pages/welcome/<step>.svelte
web/src/pages/settings/{Accounts,Income,Debts,Goals,Timeline}.svelte
web/src/components/CompletenessCard.svelte
tests/core/test_{money,calendar,payrules,accounts,income,debts,goals,onboarding}.py, tests/app/test_finance_api.py
web/e2e/05-onboarding.spec.ts
```

Timeline extension: `src/tuppence/core/timeline.py` `ALLOWED_ATTRIBUTES["household"]` gains `housing_tenure`, `housing_monthly_pence` (int ≥ 0), `bedrooms` (int 0–20), `council_tax_band` (A–I).

---

### Task 1: Money helpers and the finance-profile schema

**Files:**
- Create: `src/tuppence/core/money.py`, `src/tuppence/core/migrations/0007_finance_profile.sql`, `src/tuppence/core/providers_uk.py`
- Modify: `src/tuppence/core/timeline.py` (household attributes above)
- Test: `tests/core/test_money.py`, `tests/core/test_finance_schema.py`

**Interfaces:**
- `parse_pounds(value: str | int | float | Decimal) -> int` — accepts `"£1,450"`, `"1450.5"`, `1450`, `Decimal("12.30")`, `"-20"` (negative allowed only via `allow_negative=True`); rejects more than 2 decimal places, empty, non-numeric with `InputError("Enter an amount like 1450 or 1,450.50.")`; returns pence
- `format_pounds(pence: int) -> str` — `"1450.00"`, `"-20.05"`
- `PROVIDERS: list[Provider]` where `Provider(id, name, kinds: tuple[str, ...])`; ids are lowercase slugs (`monzo`, `starling`, `revolut`, `chase`, `hsbc`, `first_direct`, `barclays`, `lloyds`, `halifax`, `bank_of_scotland`, `natwest`, `rbs`, `ulster_bank`, `santander`, `nationwide`, `tsb`, `coop_bank`, `metro_bank`, `virgin_money`, `kroo`, `atom`, `zopa`, `amex`, `barclaycard`, `capital_one`, `mbna`, `tesco_bank`, `sainsburys_bank`, `aqua`, `vanquis`, `marbles`, `fluid`, `jaja`, `other`); `provider_name(id) -> str`
- Schema (DDL in Step 3): `account`, `account_owner`, `income_source`, `debt`, `debt_entry`, `goal`, `onboarding_step`

- [ ] **Step 1: Write the failing tests**

`tests/core/test_money.py`:

```python
from decimal import Decimal

import pytest

from tuppence.core.errors import InputError
from tuppence.core.money import format_pounds, parse_pounds


@pytest.mark.parametrize("raw,pence", [
    ("£1,450", 145000), ("1450.5", 145050), (1450, 145000), ("0.01", 1), (Decimal("12.30"), 1230), (" £ 7 ", 700),
])
def test_parse(raw, pence):
    assert parse_pounds(raw) == pence


@pytest.mark.parametrize("raw", ["12.345", "abc", "", "1.2.3", "£", "-5"])
def test_parse_rejects(raw):
    with pytest.raises(InputError):
        parse_pounds(raw)


def test_negative_when_allowed():
    assert parse_pounds("-20.05", allow_negative=True) == -2005


def test_format():
    assert format_pounds(145000) == "1450.00" and format_pounds(-2005) == "-20.05" and format_pounds(1) == "0.01"
```

`tests/core/test_finance_schema.py`:

```python
import sqlite3

import pytest

from tuppence.core.db import Database
from tuppence.core.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    with db.connection() as c:
        yield c


def test_tables_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"account", "account_owner", "income_source", "debt", "debt_entry", "goal", "onboarding_step"} <= names


def test_last4_must_be_four_digits(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO account (id, provider, provider_name, kind, nickname, last4, created_at, updated_at)"
                     " VALUES ('a', 'monzo', 'Monzo', 'current', 'Main', '12a4', 'x', 'x')")


def test_student_loan_plan_only_for_student_loans(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO debt (id, kind, lender, balance_pence, balance_date, student_loan_plan, created_at, updated_at)"
                     " VALUES ('d', 'personal_loan', 'Acme', 100, '2026-10-01', 'plan2', 'x', 'x')")
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

`src/tuppence/core/money.py`:

```python
"""Pounds in, pence stored: no floats for money."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from tuppence.core.errors import InputError

_CLEAN = re.compile(r"[£,\s]")
_SHAPE = re.compile(r"^-?\d+(\.\d{1,2})?$")
MESSAGE = "Enter an amount like 1450 or 1,450.50."


def parse_pounds(value: str | int | float | Decimal, *, allow_negative: bool = False) -> int:
    if isinstance(value, bool):
        raise InputError(MESSAGE)
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = f"{value:.2f}" if round(value, 2) == value else repr(value)
    elif isinstance(value, Decimal):
        text = format(value, "f")
    else:
        text = _CLEAN.sub("", value)
    if not _SHAPE.match(text):
        raise InputError(MESSAGE)
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise InputError(MESSAGE) from None
    if amount < 0 and not allow_negative:
        raise InputError("Enter a positive amount.")
    return int((amount * 100).to_integral_value())


def format_pounds(pence: int) -> str:
    sign = "-" if pence < 0 else ""
    p = abs(pence)
    return f"{sign}{p // 100}.{p % 100:02d}"
```

`src/tuppence/core/migrations/0007_finance_profile.sql`:

```sql
CREATE TABLE account (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  provider_name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('current', 'savings', 'credit_card', 'cash_wallet')),
  nickname TEXT NOT NULL CHECK (length(trim(nickname)) > 0),
  last4 TEXT CHECK (last4 IS NULL OR (length(last4) = 4 AND last4 GLOB '[0-9][0-9][0-9][0-9]')),
  credit_limit_pence INTEGER CHECK (credit_limit_pence IS NULL OR credit_limit_pence >= 0),
  purchase_apr REAL CHECK (purchase_apr IS NULL OR purchase_apr BETWEEN 0 AND 100),
  promo_apr REAL CHECK (promo_apr IS NULL OR promo_apr BETWEEN 0 AND 100),
  promo_end TEXT,
  statement_day INTEGER CHECK (statement_day IS NULL OR statement_day BETWEEN 1 AND 31),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE account_owner (
  account_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL REFERENCES person(id),
  PRIMARY KEY (account_id, person_id)
);

CREATE TABLE income_source (
  id TEXT PRIMARY KEY,
  person_id TEXT NOT NULL REFERENCES person(id),
  kind TEXT NOT NULL CHECK (kind IN ('salary', 'self_employment', 'benefits', 'pension', 'rental', 'maintenance', 'other')),
  name TEXT NOT NULL CHECK (length(trim(name)) > 0),
  net_pence INTEGER NOT NULL CHECK (net_pence >= 0),
  account_id TEXT REFERENCES account(id),
  pay_rule TEXT NOT NULL,
  variable_components TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE debt (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('personal_loan', 'car_finance_pcp', 'car_finance_hp', 'mortgage', 'student_loan', 'bnpl', 'overdraft', 'informal', 'other')),
  lender TEXT NOT NULL CHECK (length(trim(lender)) > 0),
  person_id TEXT REFERENCES person(id),
  balance_pence INTEGER NOT NULL CHECK (balance_pence >= 0),
  balance_date TEXT NOT NULL,
  apr REAL CHECK (apr IS NULL OR apr BETWEEN 0 AND 1000),
  monthly_payment_pence INTEGER CHECK (monthly_payment_pence IS NULL OR monthly_payment_pence >= 0),
  end_date TEXT,
  student_loan_plan TEXT CHECK (student_loan_plan IS NULL OR (kind = 'student_loan' AND student_loan_plan IN ('plan1', 'plan2', 'plan4', 'plan5', 'postgraduate'))),
  details TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'settled')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE debt_entry (
  id INTEGER PRIMARY KEY,
  debt_id TEXT NOT NULL REFERENCES debt(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  amount_pence INTEGER NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE goal (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL CHECK (length(trim(name)) > 0),
  kind TEXT NOT NULL CHECK (kind IN ('emergency_fund', 'house_deposit', 'holiday', 'car', 'wedding', 'education', 'retirement', 'debt_free', 'other')),
  target_pence INTEGER CHECK (target_pence IS NULL OR target_pence > 0),
  saved_pence INTEGER NOT NULL DEFAULT 0 CHECK (saved_pence >= 0),
  target_date TEXT,
  priority INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'achieved', 'abandoned')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE onboarding_step (
  step TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('done', 'skipped')),
  updated_at TEXT NOT NULL
);
```

`src/tuppence/core/providers_uk.py`: a frozen dataclass `Provider(id, name, kinds)` and the `PROVIDERS` list above (banks: kinds `("current","savings","credit_card")`; card-only issuers such as `amex`, `barclaycard`, `capital_one`, `mbna`, `aqua`, `vanquis`, `marbles`, `fluid`, `jaja`: `("credit_card",)`; `other`: all kinds), plus `provider_name(id)` returning the display name or raising `InputError("Unknown provider.")`.

Extend `ALLOWED_ATTRIBUTES["household"]` in `timeline.py` with:

```python
        "housing_tenure": TypeAdapter(Literal["renting", "mortgage", "owned", "living_with_family"]),
        "housing_monthly_pence": TypeAdapter(Annotated[int, Field(ge=0)]),
        "bedrooms": TypeAdapter(Annotated[int, Field(ge=0, le=20)]),
        "council_tax_band": TypeAdapter(Literal["A", "B", "C", "D", "E", "F", "G", "H", "I"]),
```

- [ ] **Step 4: Tests, lint, pyright, commit** — `git commit -m "Add money helpers, UK provider list and finance-profile schema"`

---

### Task 2: Bank holidays and the pay-date engine

**Files:**
- Create: `scripts/build_bank_holidays.py`, `src/tuppence/datapacks/baseline/uk-bank-holidays.json`, `src/tuppence/core/calendar.py`, `src/tuppence/core/payrules.py`
- Test: `tests/core/test_calendar.py`, `tests/core/test_payrules.py`

**Interfaces:**
- `scripts/build_bank_holidays.py` downloads `https://www.gov.uk/bank-holidays.json` and writes `{"source": "...", "fetched": "YYYY-MM-DD", "divisions": {"england-and-wales": ["2019-01-01", …], "scotland": […], "northern-ireland": […]}}`. Run it once and commit the output.
- `calendar.division_for(nation: str | None) -> str` (`england`/`wales`/None → `england-and-wales`; `scotland`; `northern_ireland` → `northern-ireland`)
- `calendar.bank_holidays(nation: str | None) -> frozenset[date]` (cached)
- `calendar.is_working_day(day: date, nation: str | None) -> bool`
- `calendar.previous_working_day(day, nation) -> date` (returns `day` if already a working day), `next_working_day(day, nation) -> date`, `last_working_day(year, month, nation) -> date`
- `calendar.coverage_end() -> date` (last date in the data; beyond it only weekends count — `payrules` callers may show "bank holidays not yet known" past it)
- `payrules.PayRule` — pydantic discriminated union on `type`:
  - `Interval(type: Literal["weekly","fortnightly","four_weekly"], anchor: date)`
  - `MonthlyDay(type: Literal["monthly_day"], day: int 1..31, adjust: Literal["previous_working_day","next_working_day","none"] = "previous_working_day")`
  - `LastWorkingDay(type: Literal["last_working_day"])`
  - `LastWeekday(type: Literal["last_weekday"], weekday: int 0..6)` (0 = Monday)
- `payrules.parse_rule(data: dict) -> PayRule` (raises `InputError` with a friendly message), `pay_dates(rule, start: date, end: date, nation) -> list[date]` (inclusive range, sorted), `next_pay_date(rule, after: date, nation) -> date` (strictly after `after`), `describe(rule) -> str` (e.g. "Every 4 weeks", "On the 17th (earlier if it's a weekend or bank holiday)", "Last working day of the month", "Last Friday of the month")

- [ ] **Step 1: Generate the calendar data** — `uv run python scripts/build_bank_holidays.py` (uses `urllib`, 30 s timeout). Commit the JSON.

- [ ] **Step 2: Write the failing tests**

`tests/core/test_calendar.py`:

```python
from datetime import date

from tuppence.core import calendar as cal


def test_divisions():
    assert cal.division_for("wales") == "england-and-wales"
    assert cal.division_for(None) == "england-and-wales"
    assert cal.division_for("northern_ireland") == "northern-ireland"


def test_christmas_2026_and_substitute_boxing_day():
    h = cal.bank_holidays("england")
    assert date(2026, 12, 25) in h and date(2026, 12, 28) in h
    assert not cal.is_working_day(date(2026, 12, 28), "england")
    assert cal.previous_working_day(date(2026, 12, 25), "england") == date(2026, 12, 24)


def test_scotland_specific_holiday():
    assert date(2027, 1, 4) in cal.bank_holidays("scotland")  # 2 January substitute
    assert date(2027, 1, 4) not in cal.bank_holidays("england")


def test_last_working_day():
    assert cal.last_working_day(2027, 5, "england") == date(2027, 5, 28)  # 31 May 2027 is Spring bank holiday
    assert cal.last_working_day(2026, 10, "england") == date(2026, 10, 30)
```

`tests/core/test_payrules.py`:

```python
from datetime import date

import pytest

from tuppence.core.errors import InputError
from tuppence.core.payrules import describe, next_pay_date, parse_rule, pay_dates


def test_monthly_day_moves_back_over_christmas():
    rule = parse_rule({"type": "monthly_day", "day": 25})
    assert next_pay_date(rule, date(2026, 12, 1), "england") == date(2026, 12, 24)


def test_monthly_day_31_clamps_then_adjusts():
    rule = parse_rule({"type": "monthly_day", "day": 31})
    dates = pay_dates(rule, date(2027, 2, 1), date(2027, 4, 30), "england")
    assert dates == [date(2027, 2, 26), date(2027, 3, 31), date(2027, 4, 30)]  # 28 Feb 2027 is a Sunday


def test_next_working_day_adjust_and_none():
    nxt = parse_rule({"type": "monthly_day", "day": 17, "adjust": "next_working_day"})
    assert next_pay_date(nxt, date(2026, 10, 1), "england") == date(2026, 10, 19)  # 17 Oct 2026 is a Saturday
    none = parse_rule({"type": "monthly_day", "day": 17, "adjust": "none"})
    assert next_pay_date(none, date(2026, 10, 1), "england") == date(2026, 10, 17)


def test_last_working_day_rule():
    rule = parse_rule({"type": "last_working_day"})
    assert pay_dates(rule, date(2027, 5, 1), date(2027, 6, 30), "england") == [date(2027, 5, 28), date(2027, 6, 30)]


def test_last_weekday_rule():
    rule = parse_rule({"type": "last_weekday", "weekday": 4})
    assert next_pay_date(rule, date(2026, 10, 1), "england") == date(2026, 10, 30)


def test_interval_rules():
    rule = parse_rule({"type": "four_weekly", "anchor": "2026-09-11"})
    assert pay_dates(rule, date(2026, 9, 1), date(2026, 11, 30), "england") == [date(2026, 9, 11), date(2026, 10, 9), date(2026, 11, 6)]
    weekly = parse_rule({"type": "weekly", "anchor": "2026-10-02"})
    assert next_pay_date(weekly, date(2026, 10, 2), "england") == date(2026, 10, 9)
    fortnightly = parse_rule({"type": "fortnightly", "anchor": "2026-10-02"})
    assert next_pay_date(fortnightly, date(2026, 9, 1), "england") == date(2026, 9, 4)  # works backwards from anchor


def test_strictly_after():
    rule = parse_rule({"type": "monthly_day", "day": 15, "adjust": "none"})
    assert next_pay_date(rule, date(2026, 10, 15), "england") == date(2026, 11, 15)


def test_describe():
    assert describe(parse_rule({"type": "four_weekly", "anchor": "2026-09-11"})) == "Every 4 weeks"
    assert describe(parse_rule({"type": "monthly_day", "day": 17})) == "On the 17th (earlier if it's a weekend or bank holiday)"
    assert describe(parse_rule({"type": "last_weekday", "weekday": 4})) == "Last Friday of the month"


@pytest.mark.parametrize("bad", [{"type": "monthly_day", "day": 32}, {"type": "sometimes"}, {"type": "weekly"}])
def test_bad_rules(bad):
    with pytest.raises(InputError):
        parse_rule(bad)
```

- [ ] **Step 3: Run to verify failure**, then **Step 4: Implement**

`src/tuppence/core/calendar.py`:

```python
"""UK bank holidays per nation (gov.uk data bundled; refreshed by data packs in M6)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import cache
from importlib import resources


def division_for(nation: str | None) -> str:
    if nation == "scotland":
        return "scotland"
    if nation == "northern_ireland":
        return "northern-ireland"
    return "england-and-wales"


@cache
def _data() -> dict[str, frozenset[date]]:
    raw = json.loads(
        resources.files("tuppence.datapacks.baseline").joinpath("uk-bank-holidays.json").read_text(encoding="utf-8")
    )
    return {k: frozenset(date.fromisoformat(d) for d in v) for k, v in raw["divisions"].items()}


def bank_holidays(nation: str | None) -> frozenset[date]:
    return _data()[division_for(nation)]


def coverage_end() -> date:
    return max(max(v) for v in _data().values())


def is_working_day(day: date, nation: str | None) -> bool:
    return day.weekday() < 5 and day not in bank_holidays(nation)


def previous_working_day(day: date, nation: str | None) -> date:
    while not is_working_day(day, nation):
        day -= timedelta(days=1)
    return day


def next_working_day(day: date, nation: str | None) -> date:
    while not is_working_day(day, nation):
        day += timedelta(days=1)
    return day


def last_working_day(year: int, month: int, nation: str | None) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    return previous_working_day(first_next - timedelta(days=1), nation)
```

`src/tuppence/core/payrules.py`:

```python
"""Pay-date rules (spec §5.4). Pure functions; no database."""

from __future__ import annotations

import calendar as pycal
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from tuppence.core import calendar as cal
from tuppence.core.errors import InputError


class Interval(BaseModel):
    type: Literal["weekly", "fortnightly", "four_weekly"]
    anchor: date


class MonthlyDay(BaseModel):
    type: Literal["monthly_day"]
    day: int = Field(ge=1, le=31)
    adjust: Literal["previous_working_day", "next_working_day", "none"] = "previous_working_day"


class LastWorkingDay(BaseModel):
    type: Literal["last_working_day"]


class LastWeekday(BaseModel):
    type: Literal["last_weekday"]
    weekday: int = Field(ge=0, le=6)


PayRule = Annotated[Interval | MonthlyDay | LastWorkingDay | LastWeekday, Field(discriminator="type")]
_ADAPTER: TypeAdapter[PayRule] = TypeAdapter(PayRule)
_STEP = {"weekly": 7, "fortnightly": 14, "four_weekly": 28}
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def parse_rule(data: dict) -> PayRule:
    try:
        return _ADAPTER.validate_python(data)
    except ValidationError:
        raise InputError("That pay schedule isn't valid. Choose how often you're paid and when.") from None


def _month_date(rule: PayRule, year: int, month: int, nation: str | None) -> date:
    if isinstance(rule, LastWorkingDay):
        return cal.last_working_day(year, month, nation)
    if isinstance(rule, LastWeekday):
        last = date(year, month, pycal.monthrange(year, month)[1])
        return last - timedelta(days=(last.weekday() - rule.weekday) % 7)
    assert isinstance(rule, MonthlyDay)
    day = date(year, month, min(rule.day, pycal.monthrange(year, month)[1]))
    if rule.adjust == "previous_working_day":
        return cal.previous_working_day(day, nation)
    if rule.adjust == "next_working_day":
        return cal.next_working_day(day, nation)
    return day


def pay_dates(rule: PayRule, start: date, end: date, nation: str | None) -> list[date]:
    out: list[date] = []
    if isinstance(rule, Interval):
        step = timedelta(days=_STEP[rule.type])
        offset = (start - rule.anchor).days % step.days
        d = start + timedelta(days=(step.days - offset) % step.days)
        while d <= end:
            out.append(d)
            d += step
        return out
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        d = _month_date(rule, y, m, nation)
        if start <= d <= end:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def next_pay_date(rule: PayRule, after: date, nation: str | None) -> date:
    found = pay_dates(rule, after + timedelta(days=1), after + timedelta(days=70), nation)
    return found[0]


def describe(rule: PayRule) -> str:
    if isinstance(rule, Interval):
        return {"weekly": "Every week", "fortnightly": "Every 2 weeks", "four_weekly": "Every 4 weeks"}[rule.type]
    if isinstance(rule, LastWorkingDay):
        return "Last working day of the month"
    if isinstance(rule, LastWeekday):
        return f"Last {_DAYS[rule.weekday]} of the month"
    suffix = "th" if 11 <= rule.day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(rule.day % 10, "th")
    adjust = {
        "previous_working_day": " (earlier if it's a weekend or bank holiday)",
        "next_working_day": " (later if it's a weekend or bank holiday)",
        "none": "",
    }[rule.adjust]
    return f"On the {rule.day}{suffix}{adjust}"
```

> `pay_dates` for a month-based rule may compute a date in the *previous* month (moving back across a month boundary is impossible because adjustments stay within ±4 days of a mid/late-month date, but a "day 1, previous working day" rule can land in the prior month). The `start <= d <= end` filter keeps results correct; a rule like that also appears when iterating the next month. Add a test for `{"type": "monthly_day", "day": 1}` across 1 January 2028 (a Saturday → 31 December 2027) to pin it.

- [ ] **Step 5: Tests, lint, pyright, commit** — `git commit -m "Add UK bank holidays and pay-date rules engine"`

---

### Task 3: Accounts and cards service + API

**Files:**
- Create: `src/tuppence/core/accounts.py`, `src/tuppence/app/routes/accounts.py`
- Modify: `src/tuppence/app/services.py`, `src/tuppence/app/routes/__init__.py`
- Test: `tests/core/test_accounts.py`, `tests/app/test_accounts_api.py`

**Interfaces:**
- `AccountIn` (pydantic, extra forbidden): `provider` (id from `PROVIDERS`), `provider_name: str | None` (required when provider is `other`), `kind`, `nickname` (1–40 chars), `last4: str | None` (exactly 4 digits), `owner_ids: list[str]` (≥1 existing active person), card fields only when `kind == "credit_card"`: `credit_limit` (pounds string → pence), `purchase_apr`, `promo_apr`, `promo_end: date | None`, `statement_day: int | None` — card fields on a non-card raise `InputError("Only credit cards have a limit, APR or statement day.")`
- `Account` (API shape): `id, provider, provider_name, kind, nickname, last4, owner_ids, credit_limit: str | None, purchase_apr, promo_apr, promo_end, statement_day, status, version, joint: bool`
- `AccountService(db, household)`: `list(include_closed=False)`, `get(id)`, `create(AccountIn) -> Account`, `update(id, changes: dict, expected_version) -> Account` (owners replaceable via `owner_ids`), `close(id, expected_version) -> Account`, `reopen(id, expected_version) -> Account`
- Routes: `GET /api/accounts?include_closed=` → `{"accounts": [Account]}`, `POST /api/accounts` (201), `PATCH /api/accounts/{id}` `{changes, expected_version}`, `POST /api/accounts/{id}/close` `{expected_version}`, `POST /api/accounts/{id}/reopen`, `GET /api/accounts/providers` → `{"providers": [{id, name, kinds}]}`

- [ ] **Step 1: Write the failing tests** — cover: create current account with two owners → `joint` True; card with limit `"£2,500"` → `credit_limit == "2500.00"`; card fields on a current account rejected; `last4 "12a4"` rejected (422 via API); unknown owner rejected; provider `other` requires `provider_name`; close → hidden from default list, present with `include_closed=true`, owners intact; reopen; stale version → 409.

```python
# tests/core/test_accounts.py
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
    acct = svc.create(AccountIn(provider="monzo", kind="current", nickname="Joint", last4="1234", owner_ids=[a.id, b.id]))
    assert acct.joint and acct.provider_name == "Monzo" and sorted(acct.owner_ids) == sorted([a.id, b.id])


def test_card_fields(env):
    svc, a, _ = env
    card = svc.create(AccountIn(provider="barclaycard", kind="credit_card", nickname="Barclaycard", owner_ids=[a.id],
                                credit_limit="£2,500", purchase_apr=24.9, promo_apr=0, promo_end="2027-03-31", statement_day=12))
    assert card.credit_limit == "2500.00" and card.promo_end.isoformat() == "2027-03-31"
    with pytest.raises(InputError):
        svc.create(AccountIn(provider="monzo", kind="current", nickname="X", owner_ids=[a.id], credit_limit="100"))


def test_validation(env):
    svc, a, _ = env
    with pytest.raises(ValueError):
        AccountIn(provider="monzo", kind="current", nickname="X", owner_ids=[a.id], last4="12a4")
    with pytest.raises(InputError):
        svc.create(AccountIn(provider="monzo", kind="current", nickname="X", owner_ids=["p_nobody"]))
    with pytest.raises(InputError):
        svc.create(AccountIn(provider="other", kind="current", nickname="X", owner_ids=[a.id]))


def test_close_reopen_and_versions(env):
    svc, a, _ = env
    acct = svc.create(AccountIn(provider="hsbc", kind="current", nickname="Bills", owner_ids=[a.id]))
    closed = svc.close(acct.id, expected_version=1)
    assert closed.status == "closed" and svc.list() == [] and len(svc.list(include_closed=True)) == 1
    assert closed.owner_ids == [a.id]
    with pytest.raises(VersionConflict):
        svc.reopen(acct.id, expected_version=1)
    assert svc.reopen(acct.id, expected_version=2).status == "active"
```

API tests mirror these through `/api/accounts` using the signed-in `client` fixture (create people first via `/api/household/people`).

- [ ] **Step 2–4: Implement** `AccountService` (ids `a_` + 8 hex; owners written in the same transaction; `update` with `owner_ids` replaces owner rows; validation errors as `InputError`; money via `parse_pounds`/`format_pounds`) and the router. Register `accounts` on `Services` and `PROTECTED`.

- [ ] **Step 5: Tests, lint, pyright, commit** — `git commit -m "Add accounts and cards with owners, card details and closing"`

---

### Task 4: Income sources with pay rules

**Files:**
- Create: `src/tuppence/core/income.py`, `src/tuppence/app/routes/income.py`
- Modify: services, routes
- Test: `tests/core/test_income.py`, `tests/app/test_income_api.py`

**Interfaces:**
- `IncomeIn`: `person_id`, `kind`, `name` (1–60), `net_amount` (pounds → pence, must be > 0 for `salary`), `account_id: str | None` (must be an active account owned by — or jointly including — the person, else `InputError("Choose an account that <name> owns or shares.")`), `pay_rule: dict` (validated with `parse_rule`), `variable_components: list[Literal["bonus","overtime","commission"]] = []`
- `Income` (API): fields above + `id`, `net_amount: str`, `pay_rule_description: str`, `next_pay_date: date | None` (computed with the household's nation at read time), `status`, `version`
- `IncomeService(db, household, accounts)`: `list(include_ended=False)`, `get`, `create`, `update(id, changes, expected_version)`, `end(id, expected_version)`; `upcoming(after: date, days: int = 35) -> list[tuple[date, Income]]` (all active sources, sorted)
- Routes: `GET /api/income` → `{"income": [Income]}`, `POST /api/income`, `PATCH /api/income/{id}`, `POST /api/income/{id}/end`, `GET /api/income/upcoming?days=35`, `POST /api/income/preview-rule` `{pay_rule}` → `{"description", "next_dates": [5 dates from today]}` (used by the UI as you type)

- [ ] **Step 1: Write the failing tests** — salary "day 25 previous working day" into a joint account shows `next_pay_date` 24 Dec 2026 when computed after 1 Dec 2026 (inject `today` into the service for tests: `IncomeService(..., today=lambda: date(2026, 12, 1))`); account not owned by the person rejected; `£2,345.67` stored as 234567 pence and returned `"2345.67"`; preview-rule returns 5 dates; ended income hidden by default; `upcoming` merges two people's incomes in date order.

- [ ] **Step 2–4: Implement**, register, **Step 5: commit** — `git commit -m "Add income sources with pay schedules and upcoming pay dates"`

---

### Task 5: Debts and goals

**Files:**
- Create: `src/tuppence/core/debts.py`, `src/tuppence/core/goals.py`, `src/tuppence/app/routes/debts.py`, `src/tuppence/app/routes/goals.py`
- Modify: services, routes
- Test: `tests/core/test_debts.py`, `tests/core/test_goals.py`, `tests/app/test_debts_goals_api.py`

**Interfaces:**
- `DebtIn`: `kind`, `lender` (1–60; for `informal` this is the person or name, e.g. "Brother"), `person_id: str | None` (None = joint/household), `balance` (pounds), `balance_date: date = today`, `apr: float | None`, `monthly_payment: str | None`, `end_date: date | None`, `student_loan_plan` (required iff kind is `student_loan`), `details: dict` validated per kind:
  - car finance (`car_finance_pcp`, `car_finance_hp`): `agreement_start: date | None`, `via_broker: bool | None`, `balloon: str | None` (pounds; PCP only), `total_payable: str | None`, `annual_mileage: int | None` (PCP only)
  - `mortgage`: `fixed_until: date | None`, `rate_type: Literal["fixed","tracker","variable","svr"] | None`
  - `informal`: `direction: Literal["i_owe","owed_to_me"]` (required)
  - others: empty dict; unknown keys → `InputError`
- `Debt` (API): stored fields + `id`, money as pounds strings, `status`, `version`, and derived `car_finance_redress_window: bool` (True when kind is car finance, `via_broker` is True and `agreement_start` falls between 2007-04-06 and 2024-11-01 inclusive — spec §11.6; used by M6)
- `DebtService(db, household)`: `list(include_settled=False)`, `get`, `create`, `update`, `settle(id, expected_version)`; `total_balance_pence() -> int` (active only; informal `owed_to_me` excluded)
- `GoalIn`: `name`, `kind`, `target_amount: str | None`, `saved_amount: str = "0"`, `target_date: date | None` (must be in the future when given), `priority: 1|2|3 = 2`
- `GoalService(db)`: `list(include_closed=False)`, `get`, `create`, `update`, `set_status(id, status, expected_version)`; `suggest_emergency_fund() -> bool` (True when no active `emergency_fund` goal exists)
- Routes: `/api/debts` (GET → `{"debts": [Debt]}` / POST), `/api/debts/{id}` (PATCH), `/api/debts/{id}/settle`; `/api/goals` (GET → `{"goals": [Goal]}` / POST), `/api/goals/{id}` (PATCH), `/api/goals/{id}/status` `{status, expected_version}`; `GET /api/goals/suggestions` → `{"emergency_fund": bool}`

- [ ] **Step 1: Write the failing tests** — student loan without plan rejected and plan on non-student-loan rejected; PCP via broker started 2019-03-01 → `car_finance_redress_window` True; started 2025-01-01 → False; informal debt requires direction and `owed_to_me` is excluded from `total_balance_pence`; mortgage `fixed_until` stored; goal with past target date rejected; emergency-fund suggestion flips after creating one; settle hides a debt by default.

- [ ] **Step 2–4: Implement**, register, **Step 5: commit** — `git commit -m "Add debts (incl. car finance and informal) and savings goals"`

---

### Task 6: Onboarding progress and the completeness meter

**Files:**
- Create: `src/tuppence/core/onboarding.py`, `src/tuppence/app/routes/onboarding.py`
- Modify: services, routes
- Test: `tests/core/test_onboarding.py`, `tests/app/test_onboarding_api.py`

**Interfaces:**
- `STEPS = ["welcome", "household", "work_income", "home", "accounts", "debts", "goals", "ai", "first_upload"]` with titles: "Welcome", "Who's in your household", "Work and income", "Your home", "Accounts and cards", "Loans and debts", "What you're saving for", "Choose your AI", "Your first statements"
- `OnboardingService(db, household, timeline, accounts, income, debts, goals, settings, connections)`:
  - `state() -> OnboardingState` = `{steps: [{id, title, status: "todo"|"done"|"skipped"}], next_step: str | None, started: bool, finished: bool, completeness: int (0–100), prompts: [Prompt]}`
  - `mark(step, status: "done"|"skipped") -> OnboardingState`; `reset() -> OnboardingState` (dev/testing aid; protected route)
  - `Prompt = {id, text, unlocks, link}` — derived from data, e.g.:
    - no nation → "Tell us which UK nation you live in" / unlocks "the right tax bands and bank holidays" / `/settings/household`
    - no postcode district → unlocks "local rent comparisons"
    - an adult with no `employment_status` → "Add <name>'s work status" / unlocks "tax and benefit checks"
    - an adult with no `income_band` → unlocks "Marriage Allowance and Child Benefit checks"
    - a child without birth year → "Add <name>'s birth year" / unlocks "Tax-Free Childcare and funded childcare checks"
    - no income sources → unlocks "payday-to-payday budgets"
    - no accounts → unlocks "statement import"
    - a card without APR → "Add the interest rate for <nickname>" / unlocks "interest-cost estimates"
    - no housing tenure → unlocks "housing cost checks"
    - no AI model chosen (`llm.simple_model` unset) → unlocks "statement understanding and the coach"
  - `completeness` = percentage of the checks above (one point each, plus one per wizard step done or skipped) that are satisfied, rounded
- Routes: `GET /api/onboarding`, `POST /api/onboarding/steps/{step}` `{status}`, `POST /api/onboarding/reset`

- [ ] **Step 1: Write the failing tests** — fresh state: not started, `next_step == "welcome"`, completeness low, prompts include the nation prompt; marking steps moves `next_step`; skipping counts as progress; a child without birth year produces the named prompt; after filling nation/district/people/income/accounts and choosing a model, those prompts disappear and completeness rises; all steps done/skipped → `finished` True.

- [ ] **Step 2–4: Implement**, register, **Step 5: commit** — `git commit -m "Add onboarding progress, completeness meter and unlock prompts"`

---

### Task 7: Shared form components and Settings pages

**Files:**
- Create: `web/src/lib/money.ts`, `web/src/components/forms/{MoneyInput,PayRuleField,PersonForm,AccountForm,IncomeForm,DebtForm,GoalForm}.svelte`, `web/src/pages/settings/{Accounts,Income,Debts,Goals,Timeline}.svelte`, `web/src/components/CompletenessCard.svelte`
- Modify: `web/src/pages/settings/Household.svelte` (use `PersonForm`; add Home details section writing timeline attributes with a "from" date), `web/src/App.svelte`, `web/src/components/Nav.svelte` (a "Settings" group: Household, Accounts, Income, Debts, Goals, AI, Privacy, Agents), `web/src/pages/Home.svelte` (CompletenessCard with the top 3 prompts and "Continue setup")
- Test: `web/src/lib/money.test.ts`, `web/src/components/forms/*.test.ts` (one per form), `web/src/pages/settings/Accounts.test.ts`, `web/src/components/CompletenessCard.test.ts`

**Interfaces / behaviour:**
- `money.ts`: `parsePoundsInput(s: string): string | null` (normalises `"£1,450"` → `"1450.00"`, returns null when invalid), `formatGBP(pounds: string): string` (`"£1,450.00"` via `Intl.NumberFormat('en-GB', {style: 'currency', currency: 'GBP'})`)
- `MoneyInput.svelte`: labelled text input with `inputmode="decimal"` and a `£` prefix; shows "Enter an amount like 1450 or 1,450.50." when invalid
- `PayRuleField.svelte`: "How often are you paid?" select (Every week / Every 2 weeks / Every 4 weeks / On a set day each month / Last working day of the month / Last <weekday> of the month); conditional inputs (anchor date, day 1–31 with "If it's a weekend or bank holiday" select, weekday); live preview list "Next paydays: …" from `POST /api/income/preview-rule`
- `AccountForm`: provider select (from `/api/accounts/providers`, filtered by kind), "Provider name" when Other, kind, nickname, last 4 digits (`inputmode="numeric"`, `maxlength=4`), owners as checkboxes of people, card-only fields shown only for credit cards; help text "We never ask for full account numbers or sort codes."
- `IncomeForm`, `DebtForm` (kind-specific sections for car finance, mortgage, student loan, informal), `GoalForm` (shows "No emergency fund yet — most people aim for 3–6 months of essential spending" with a one-click "Add emergency fund goal" when the suggestion is true)
- Settings pages list items with edit/close/settle/end actions, use `Notice` for errors (409 shows the conflict message)
- `Timeline.svelte` (`/settings/timeline`): per person and the household, a chronological list of effective-dated changes (employment status, income band, housing) with an "Add a change" form (attribute, value, "from" date) — this is how life changes such as a new job or moving home are recorded

- [ ] **Step 1–4:** tests first (money parsing; AccountForm hides card fields for current accounts and shows them for credit cards; PayRuleField preview renders dates from a mocked API; Accounts page closes an account and shows it under "Closed"); implement; `npm --prefix web test && npm --prefix web run check && npm --prefix web run build`
- [ ] **Step 5: commit** — `git commit -m "Add finance profile forms and Settings pages for accounts, income, debts, goals and timeline"`

---

### Task 8: The onboarding wizard

**Files:**
- Create: `web/src/pages/Welcome.svelte`, `web/src/pages/welcome/{WelcomeStep,HouseholdStep,WorkIncomeStep,HomeStep,AccountsStep,DebtsStep,GoalsStep,AIStep,FirstUploadStep}.svelte`, `web/src/pages/Welcome.test.ts`
- Modify: `web/src/App.svelte` (route `/welcome`; after sign-in, if `GET /api/onboarding` says `started: false`, navigate to `/welcome`)

**Behaviour:**
- Shell: progress indicator "Step N of 9", step title as `<h1>`, "Back", "Skip this step" and "Continue" buttons; on load it opens `next_step` (resume); Continue marks the step `done`, Skip marks it `skipped`; after the last step → Home.
- Welcome: disclaimer text exactly: "Tuppence gives insights and guidance, not regulated financial advice. For debt help, MoneyHelper, StepChange and Citizens Advice are free." Nation select and postcode district input (district only, validation message from the API).
- Household: "Is this just you, or a family?" radio (Just me / Family). Just me → "Any children or dependants?" with an add-person mini form (role child/dependent adult, birth year for children). Family → add partner (adult) then dependants. The signed-in adult is added first as "You" (name editable).
- Work and income: per adult, employment status and optional income band (with an explanation of why it helps), then income sources using `IncomeForm` (receiving-account picker allows "I'll add accounts in a moment").
- Home: tenure, monthly amount, bedrooms, council tax band (Scotland shows A–H, Wales A–I, England A–H, Northern Ireland hides council tax: "Northern Ireland uses domestic rates"), saved as timeline attributes from today.
- Accounts, Debts, Goals: list + add using the shared forms; Goals shows the emergency-fund suggestion.
- AI: embeds the "Find local AI" detection and a compact "Add connection" (reuse from Settings › AI; extract a shared `ConnectionQuickAdd.svelte` if needed) and the "Model for everything" select; explains Local vs Internet in one sentence each; includes the research-lookups toggle presented as recommended on, with the text "Only merchant names are looked up — never amounts or your details."
- First statements: "Statement import arrives in the next update. You can skip this for now." with "Finish".

- [ ] **Step 1–4:** component tests for resume (mock `/api/onboarding` with `next_step: "accounts"` → the Accounts step heading shows), Skip (POST status skipped then next step), the Northern Ireland council tax rule; implement; build.

- [ ] **Step 5: End-to-end** — `web/e2e/05-onboarding.spec.ts` against a **fresh** server (extend `scripts/e2e.sh` to accept `E2E_FRESH=1`, which starts a second clean data dir for specs tagged `@fresh`, or give this spec its own `test.describe` that calls `POST /api/onboarding/reset` and creates people afresh). Scenarios:
  1. Family path end to end: Welcome (Wales, `CF10`) → Household (Family; partner "Sam Example"; child "Kid A" 2019) → Work and income (Alex employed, salary "Acme Payroll" £2,345.67 on the last working day into a joint Monzo added inline — or skip income, add accounts, then return via Back) → Home (renting, £950, 2 bedrooms, band C) → Accounts (joint Monzo ending 1234; Barclaycard credit card limit £2,500, APR 24.9, promo 0% until 31/03/2027) → Debts (PCP via broker, agreement start 2019-03-01, balance £8,000) → Goals (accept the emergency fund suggestion; add "House deposit" £20,000 by 2029) → AI (skip) → Finish → Home shows completeness ≥ 70% and a prompt "Choose an AI model" or similar.
  2. Resume: start a new wizard, complete Welcome and Household, reload the page → wizard reopens at "Work and income" with the household intact.
  3. Settings › Timeline: record "Alex Example — employment status — not working — from 2027-01-01" and see it listed after "employed".
  Run `bash scripts/e2e.sh` → all specs (M1a, M1b and M2) pass.

- [ ] **Step 6: Full verification and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest -q
npm --prefix web test && npm --prefix web run check
bash scripts/e2e.sh
uv run python scripts/denylist_guard.py --require
git add -A && git commit -m "Add onboarding wizard with resume, skip and completeness"
```
