# Tuppence M1a — Foundation Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The durable core every later milestone builds on: SQLite store with migrations and backups, versioned settings, login modes (launch token for local/desktop, first-run admin for Docker), the effective-dated household timeline with a people API, configurable agent manifests with presets, a coalescing job queue, and a Svelte app shell with Household and Agents settings pages — all proven end to end in a browser.

**Architecture:** `create_app()` builds a `Services` container (database, settings store, config service, job queue, worker) at startup, runs migrations (backing up first), and mounts API routers under `/api` behind a session dependency with CSRF protection. Agent manifests merge four layers: repo defaults < preset < user TOML < UI overrides. The UI is a single Svelte app with a tiny client-side router.

**Tech Stack:** Python 3.12, FastAPI, sqlite3 (stdlib), argon2-cffi 25, pydantic 2, tomllib (stdlib); Svelte 5 + Vite + vitest; Playwright (`@playwright/test`) for end-to-end.

**Spec:** `docs/superpowers/specs/2026-10-07-tuppence-design.md` (§3.1, §3.3, §3.4, §5.3, §5.5, §10.1, §11.2, §14.1, §14.2, §16 M1). Builds on M0 (`docs/superpowers/plans/2026-10-07-m0-bootstrap.md`).

## Global Constraints

- Everything from the M0 plan's Global Constraints still applies (licence, privacy claim wording, denylist, data paths, `/health` vs `/api/`, security headers, UK English, commit trailers per the implementing agent's harness plus the `Claude-Session:` line).
- Storage: one SQLite file `tuppence.db` in the data folder; WAL mode, `foreign_keys=ON`, `busy_timeout=5000`. Timestamps stored as UTC ISO strings `YYYY-MM-DDTHH:MM:SSZ`; dates as `YYYY-MM-DD`.
- A backup is taken before any migration runs on a database that already has applied migrations; daily backups keep 7 (spec §14.1).
- Every user-owned row carries `version`; a stale write returns HTTP 409 with `{"detail": "This was changed somewhere else. Reload and try again.", "current_version": <int>}` (spec §3.4).
- Modes: `local`/`desktop` authenticate with a per-launch token exchanged at `GET /auth/launch?token=…` for a session cookie; `server` uses first-run admin setup — never default credentials (spec §3.1, §14.2).
- Session cookie `tuppence_session`: HttpOnly, `SameSite=Strict`, `Secure` when `secure_cookies` is on, path `/`, 30-day sliding expiry. Unsafe methods (`POST/PUT/PATCH/DELETE`) on authenticated `/api/*` routes need header `X-CSRF-Token` matching the session's token.
- Passwords: Argon2id via `argon2-cffi`; minimum 10 characters; login lockout after 5 failures for 60 s per (IP, username) → HTTP 429 with `Retry-After`.
- Agent manifest precedence (spec §11.2): repo defaults < preset < user `config/agents/<name>.toml` < Settings › Agents overrides. Presets: `frugal`, `balanced` (default), `thorough`.
- Agent budget values copied from spec §10.1: researcher ≤20 merchants/run, ≤5 tool calls/merchant, ≤2 page fetches/merchant; purpose analyst depth ≤5, ≤3 open conversations; questions ≤5 open; coach ≤8 tool calls/turn, 60 s cloud / 180 s local, ≤50 rows per tool result, last 8 turns kept; backlog sweep revisits confidence < 0.7.
- Jobs: one pending (queued) job per (kind, scope); triggers may debounce; one running job at a time for exclusive kinds; running jobs return to the queue after a crash/restart.
- Household: postcode **district only** (outward code, e.g. `LS6`); nations `england | wales | scotland | northern_ireland`; people roles `adult | child | dependent_adult`; people are retired, never deleted.
- UI copy in plain UK English; accessible (labels on every input, keyboard reachable, visible focus).

## Review Focus

1. **Stale edits from two browser tabs** — the second save must get a clear "changed somewhere else" message (409), never silently overwrite. Test in Task 2 (settings) and Task 4 (people).
2. **Full postcode typed into the district field** (e.g. `LS6 2AB`) — must be rejected with a friendly message, and nothing stored. Test in Task 4.
3. **Restart while a job is running** — the job must be picked up again, not stuck in `running` forever. Test in Task 6.
4. **Expired or reused desktop launch link** (e.g. bookmarked `…/auth/launch?token=` from yesterday) — friendly 403 page explaining to reopen Tuppence; never a session. Test in Task 3.
5. **Hand-edited user TOML with a typo** (unknown key or wrong type) — app must still start; Settings › Agents shows the file error and ignores that layer. Test in Task 5.

---

## File Structure

```
src/tuppence/core/clock.py            utcnow(), to_iso(), from_iso()
src/tuppence/core/db.py               connect(), transaction(), Database
src/tuppence/core/backup.py           backup_db(), daily_backup(), rotate_backups()
src/tuppence/core/migrate.py          available_migrations(), migrate(), MigrationError
src/tuppence/core/migrations/__init__.py
src/tuppence/core/migrations/0001_core.sql     app_settings, person, household, profile_entry
src/tuppence/core/migrations/0002_auth.sql     app_user, session, login_attempt
src/tuppence/core/migrations/0003_config.sql   agent_override
src/tuppence/core/migrations/0004_jobs.sql     job
src/tuppence/core/records.py          VersionConflict, NotFound, update_versioned()
src/tuppence/core/settings_store.py   SettingDef registry, SettingsStore, SettingInvalid
src/tuppence/core/auth.py             passwords, Users, Sessions, LoginLimiter
src/tuppence/core/timeline.py         Timeline (effective-dated attributes)
src/tuppence/core/household.py        HouseholdService, Person, Household, input models
src/tuppence/core/jobs.py             Job, JobQueue, Worker, DeferJob, Periodic
src/tuppence/config/__init__.py
src/tuppence/config/models.py         AgentManifest, Budgets, QuestionLimits, ModelRef, Task
src/tuppence/config/service.py        ConfigService, deep_merge()
src/tuppence/config/defaults/agents/*.toml     13 default manifests
src/tuppence/config/defaults/presets/{frugal,balanced,thorough}.toml
src/tuppence/app/services.py          Services, build_services()
src/tuppence/app/errors.py            exception → JSON handlers
src/tuppence/app/deps.py              get_services(), Principal, require_session()
src/tuppence/app/routes/__init__.py   include_routers()
src/tuppence/app/routes/auth.py       /auth/launch, /api/auth/*
src/tuppence/app/routes/settings.py   /api/settings
src/tuppence/app/routes/household.py  /api/household, /api/household/people, /api/household/timeline
src/tuppence/app/routes/config.py     /api/config/*
src/tuppence/app/routes/jobs.py       /api/jobs (read-only list)
web/src/lib/api.ts, web/src/lib/session.svelte.ts, web/src/lib/router.svelte.ts
web/src/App.svelte, web/src/components/{Nav,Field,Notice}.svelte
web/src/pages/{Home,Login,Setup,LaunchExpired,NotFound}.svelte
web/src/pages/settings/{Household,Agents}.svelte
web/e2e/*.spec.ts, web/playwright.config.ts
tests/core/*, tests/app/*, tests/config/*
```

Modified: `src/tuppence/app/factory.py` (services + routers + lifespan), `src/tuppence/cli.py` (launch URL), `src/tuppence/desktop/launcher.py` (launch URL), `pyproject.toml` (argon2-cffi), `web/package.json`.

---

### Task 1: Database core, migrations and backups

**Files:**
- Create: `src/tuppence/core/__init__.py`, `src/tuppence/core/clock.py`, `src/tuppence/core/db.py`, `src/tuppence/core/backup.py`, `src/tuppence/core/migrate.py`, `src/tuppence/core/migrations/__init__.py`, `src/tuppence/core/migrations/0001_core.sql`
- Test: `tests/core/__init__.py`, `tests/core/test_db.py`, `tests/core/test_migrate.py`, `tests/core/test_backup.py`

**Interfaces:**
- Produces:
  - `tuppence.core.clock.utcnow() -> datetime` (aware UTC, microseconds zeroed), `to_iso(dt) -> str` (`YYYY-MM-DDTHH:MM:SSZ`), `from_iso(s) -> datetime`
  - `tuppence.core.db.connect(path: Path) -> sqlite3.Connection` (row_factory `sqlite3.Row`, autocommit mode)
  - `tuppence.core.db.transaction(conn) -> ContextManager[Connection]` (BEGIN IMMEDIATE / COMMIT / ROLLBACK)
  - `tuppence.core.db.Database(path)` with `.path`, `.connection() -> ContextManager[Connection]`, `.transaction() -> ContextManager[Connection]`
  - `tuppence.core.backup.backup_db(src: Path, dest_dir: Path, label: str, *, now: datetime | None = None) -> Path`
  - `tuppence.core.backup.daily_backup(src: Path, dest_dir: Path, today: date, *, keep: int = 7) -> Path | None`
  - `tuppence.core.backup.rotate_backups(dest_dir: Path, *, prefix: str = "daily-", keep: int = 7) -> list[Path]`
  - `tuppence.core.migrate.available_migrations() -> list[tuple[str, str]]` (version, sql), `migrate(db: Database, backups_dir: Path) -> list[str]` (applied versions), `MigrationError`
  - Tables from `0001_core.sql`: `app_settings`, `person`, `household`, `profile_entry` (DDL below)

- [ ] **Step 1: Write the failing tests**

`tests/core/__init__.py`: empty.

`tests/core/test_db.py`:

```python
import sqlite3

import pytest

from tuppence.core.clock import from_iso, to_iso, utcnow
from tuppence.core.db import Database, connect, transaction


def test_connect_sets_pragmas(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert isinstance(conn.execute("SELECT 1 AS x").fetchone(), sqlite3.Row)
    finally:
        conn.close()


def test_transaction_commits_and_rolls_back(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.connection() as conn:
        conn.execute("CREATE TABLE t (v INTEGER)")
    with db.transaction() as conn:
        conn.execute("INSERT INTO t VALUES (1)")
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO t VALUES (2)")
        raise RuntimeError("boom")
    with db.connection() as conn:
        assert [r[0] for r in conn.execute("SELECT v FROM t")] == [1]


def test_nested_transaction_helper_on_open_connection(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (v INTEGER)")
    with transaction(conn):
        conn.execute("INSERT INTO t VALUES (5)")
    assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    conn.close()


def test_iso_roundtrip():
    now = utcnow()
    assert now.microsecond == 0
    s = to_iso(now)
    assert s.endswith("Z") and len(s) == 20
    assert from_iso(s) == now
```

`tests/core/test_migrate.py`:

```python
import pytest

from tuppence.core import migrate as mig
from tuppence.core.db import Database


def tables(db):
    with db.connection() as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_database_gets_all_migrations_and_no_backup(tmp_path):
    db = Database(tmp_path / "tuppence.db")
    applied = mig.migrate(db, tmp_path / "backups")
    assert applied[0] == "0001_core"
    assert {"app_settings", "person", "household", "profile_entry", "schema_migrations"} <= tables(db)
    assert not (tmp_path / "backups").exists() or not any((tmp_path / "backups").iterdir())


def test_migrate_is_idempotent(tmp_path):
    db = Database(tmp_path / "tuppence.db")
    mig.migrate(db, tmp_path / "backups")
    assert mig.migrate(db, tmp_path / "backups") == []


def test_backup_taken_before_pending_migration_on_existing_db(tmp_path, monkeypatch):
    db = Database(tmp_path / "tuppence.db")
    first = mig.available_migrations()[:1]
    monkeypatch.setattr(mig, "available_migrations", lambda: first)
    mig.migrate(db, tmp_path / "backups")
    extra = ("9999_extra", "CREATE TABLE extra (x INTEGER);")
    monkeypatch.setattr(mig, "available_migrations", lambda: [*first, extra])
    assert mig.migrate(db, tmp_path / "backups") == ["9999_extra"]
    backups = list((tmp_path / "backups").iterdir())
    assert len(backups) == 1 and "pre-9999_extra" in backups[0].name


def test_failed_migration_rolls_back_and_raises(tmp_path, monkeypatch):
    db = Database(tmp_path / "tuppence.db")
    bad = ("0001_bad", "CREATE TABLE ok (x INTEGER); CREATE TABLE ok (x INTEGER);")
    monkeypatch.setattr(mig, "available_migrations", lambda: [bad])
    with pytest.raises(mig.MigrationError) as exc:
        mig.migrate(db, tmp_path / "backups")
    assert "0001_bad" in str(exc.value)
    assert "ok" not in tables(db)
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0


def test_migration_file_names_are_well_formed():
    for version, sql in mig.available_migrations():
        assert mig.VERSION_RE.match(version), version
        assert sql.strip()


def test_schema_constraints(tmp_path):
    import sqlite3

    db = Database(tmp_path / "tuppence.db")
    mig.migrate(db, tmp_path / "b")
    with db.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO person (id, display_name, role, created_at, updated_at) VALUES ('p1','  ','adult','x','x')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO profile_entry (subject_type, subject_id, attribute, value, valid_from, valid_to, created_at)"
                " VALUES ('person','p1','employment_status','\"employed\"','2026-05-01','2026-04-01','x')"
            )
```

`tests/core/test_backup.py`:

```python
from datetime import date

from tuppence.core.backup import backup_db, daily_backup, rotate_backups
from tuppence.core.db import Database


def make_db(tmp_path):
    db = Database(tmp_path / "tuppence.db")
    with db.connection() as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('hello')")
    return db


def test_backup_is_a_readable_copy(tmp_path):
    db = make_db(tmp_path)
    out = backup_db(db.path, tmp_path / "backups", "manual")
    copy = Database(out)
    with copy.connection() as conn:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "hello"
    assert out.name.startswith("tuppence-manual-")


def test_daily_backup_once_per_day_and_rotation(tmp_path):
    db = make_db(tmp_path)
    dest = tmp_path / "backups"
    for day in range(1, 10):
        daily_backup(db.path, dest, date(2026, 10, day))
    assert daily_backup(db.path, dest, date(2026, 10, 9)) is None  # already done today
    names = sorted(p.name for p in dest.iterdir())
    assert names == [f"daily-2026-10-0{d}.db" for d in range(3, 10)]


def test_rotate_ignores_other_files(tmp_path):
    dest = tmp_path / "b"
    dest.mkdir()
    (dest / "tuppence-pre-0002.db").write_text("x")
    for d in range(1, 4):
        (dest / f"daily-2026-01-0{d}.db").write_text("x")
    removed = rotate_backups(dest, keep=2)
    assert [p.name for p in removed] == ["daily-2026-01-01.db"]
    assert (dest / "tuppence-pre-0002.db").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core -q` → Expected: FAIL with `ModuleNotFoundError: No module named 'tuppence.core'`.

- [ ] **Step 3: Implement**

`src/tuppence/core/__init__.py`: `"""Domain core: storage, settings, auth, household, jobs."""`

`src/tuppence/core/clock.py`:

```python
"""Time helpers. All stored timestamps are UTC ISO strings with a trailing Z."""

from __future__ import annotations

from datetime import UTC, datetime

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime(_FMT)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, _FMT).replace(tzinfo=UTC)
```

`src/tuppence/core/db.py`:

```python
"""SQLite access: one short-lived connection per unit of work (spec §3.3)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as conn, transaction(conn):
            yield conn
```

`src/tuppence/core/backup.py`:

```python
"""Database backups: before migrations and once a day (spec §14.1)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from tuppence.core.clock import utcnow


def backup_db(src: Path, dest_dir: Path, label: str, *, now: datetime | None = None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or utcnow()).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / f"tuppence-{label}-{stamp}.db"
    return _copy(src, dest)


def daily_backup(src: Path, dest_dir: Path, today: date, *, keep: int = 7) -> Path | None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"daily-{today.isoformat()}.db"
    if dest.exists():
        return None
    _copy(src, dest)
    rotate_backups(dest_dir, prefix="daily-", keep=keep)
    return dest


def rotate_backups(dest_dir: Path, *, prefix: str = "daily-", keep: int = 7) -> list[Path]:
    files = sorted((p for p in dest_dir.glob(f"{prefix}*.db") if p.is_file()), key=lambda p: p.name, reverse=True)
    removed = files[keep:]
    for path in removed:
        path.unlink()
    return sorted(removed, key=lambda p: p.name)


def _copy(src: Path, dest: Path) -> Path:
    tmp = dest.with_suffix(".tmp")
    source = sqlite3.connect(src)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    tmp.replace(dest)
    return dest
```

`src/tuppence/core/migrate.py`:

```python
"""Numbered SQL migrations shipped inside the package (spec §14.1)."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from tuppence.core.backup import backup_db
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database

PACKAGE = "tuppence.core.migrations"
VERSION_RE = re.compile(r"^\d{4}_[a-z0-9_]+$")


class MigrationError(RuntimeError):
    pass


def available_migrations() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for entry in resources.files(PACKAGE).iterdir():
        name = entry.name
        if name.endswith(".sql"):
            version = name[:-4]
            if not VERSION_RE.match(version):
                raise MigrationError(f"Badly named migration file: {name}")
            found.append((version, entry.read_text(encoding="utf-8")))
    return sorted(found)


def migrate(db: Database, backups_dir: Path) -> list[str]:
    with db.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    todo = [(v, sql) for v, sql in available_migrations() if v not in done]
    if not todo:
        return []
    if done:
        backup_db(db.path, backups_dir, f"pre-{todo[0][0]}")
    applied: list[str] = []
    for version, sql in todo:
        if not VERSION_RE.match(version):
            raise MigrationError(f"Badly named migration: {version}")
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{sql}\n;\n"
            f"INSERT INTO schema_migrations (version, applied_at) VALUES ('{version}', '{to_iso(utcnow())}');\n"
            "COMMIT;"
        )
        with db.connection() as conn:
            try:
                conn.executescript(script)
            except Exception as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise MigrationError(f"Migration {version} failed: {exc}") from exc
        applied.append(version)
    return applied
```

> Note: `executescript` can't take parameters; `version` is validated by `VERSION_RE` and the timestamp is generated locally, so inlining them is safe. Add `# noqa: S608` if ruff flags the f-string.

`src/tuppence/core/migrations/__init__.py`: empty (makes the folder a package so `importlib.resources` finds the `.sql` files; hatchling includes non-Python files under `src/tuppence` automatically — verify with `uv build` in Task 8's verification).

`src/tuppence/core/migrations/0001_core.sql`:

```sql
CREATE TABLE app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE person (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
  role TEXT NOT NULL CHECK (role IN ('adult', 'child', 'dependent_adult')),
  birth_year INTEGER CHECK (birth_year IS NULL OR birth_year BETWEEN 1900 AND 2100),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE household (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  nation TEXT CHECK (nation IS NULL OR nation IN ('england', 'wales', 'scotland', 'northern_ireland')),
  postcode_district TEXT,
  currency TEXT NOT NULL DEFAULT 'GBP',
  period_mode TEXT NOT NULL DEFAULT 'calendar_month' CHECK (period_mode IN ('calendar_month', 'pay_cycle')),
  period_anchor_person_id TEXT REFERENCES person(id),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE profile_entry (
  id INTEGER PRIMARY KEY,
  subject_type TEXT NOT NULL CHECK (subject_type IN ('household', 'person', 'account', 'income_source')),
  subject_id TEXT NOT NULL,
  attribute TEXT NOT NULL,
  value TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'proposal', 'import')),
  created_at TEXT NOT NULL,
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX ix_profile_entry_lookup ON profile_entry (subject_type, subject_id, attribute, valid_from);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core -q` → Expected: all pass. Then `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright` → clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add SQLite core: connections, transactions, migrations and backups"
```
(Append your harness's attribution trailer lines plus `Claude-Session: https://claude.ai/code/session_01NJjEQDR3UGuFJz6TzhmiFM`.)

---

### Task 2: Versioned records, settings store and the Services container

**Files:**
- Create: `src/tuppence/core/errors.py`, `src/tuppence/core/records.py`, `src/tuppence/core/settings_store.py`, `src/tuppence/app/services.py`, `src/tuppence/app/errors.py`, `src/tuppence/app/deps.py`, `src/tuppence/app/routes/__init__.py`, `src/tuppence/app/routes/settings.py`
- Modify: `src/tuppence/app/factory.py`
- Test: `tests/core/test_records.py`, `tests/core/test_settings_store.py`, `tests/app/test_settings_api.py`, `tests/app/conftest.py`

**Interfaces:**
- Consumes: `Database`, `migrate`, `DataPaths`, `RuntimeSettings`, `to_iso/utcnow`.
- Produces:
  - `tuppence.core.records.VersionConflict(table, key, expected, current)` (attrs `.current`), `NotFound(table, key)`
  - `update_versioned(conn, table: str, key_col: str, key: object, expected_version: int, changes: Mapping[str, object], *, now: str) -> int` (new version)
  - `tuppence.core.settings_store.SettingDef(key, default, adapter, description)`; module-level `SETTINGS: dict[str, SettingDef]`; `define(key, type_, default, description) -> None`
  - `SettingEntry(key, value, version, description, default)` (pydantic); `SettingsStore(db)` with `get(key) -> Any`, `entry(key) -> SettingEntry`, `all() -> list[SettingEntry]`, `set(key, value, *, expected_version: int) -> SettingEntry`; `SettingInvalid(key, message)`; unknown key → `KeyError`
  - Settings defined in this task (key → type, default): `privacy.local_only` → bool, False; `privacy.pseudonymise` → bool, False; `privacy.research_lookups` → bool, False; `privacy.live_market_data` → bool, True; `privacy.datapack_updates` → bool, True; `config.preset` → Literal["frugal","balanced","thorough"], "balanced"; `llm.monthly_cap_gbp` → float ≥ 0, 10.0; `llm.run_cap_gbp` → float ≥ 0, 1.0; `llm.usd_to_gbp` → float > 0, 0.75
  - `tuppence.app.services.Services` dataclass (`runtime`, `paths`, `db`, `settings`; later tasks add fields) and `build_services(runtime: RuntimeSettings) -> Services` (ensures paths, runs migrations)
  - `tuppence.app.deps.get_services(request) -> Services`
  - `tuppence.core.errors.InputError(ValueError)` — the one 422 error type; core modules import it from here (core never imports `app`)
  - `tuppence.app.errors.install_error_handlers(app)`: VersionConflict → 409 `{"detail": "This was changed somewhere else. Reload and try again.", "current_version": n}`; NotFound → 404 `{"detail": "Not found"}`; SettingInvalid / `InputError` → 422 `{"detail": message}`
  - `tuppence.app.routes.include_routers(app)`; `GET /api/settings` → `{"settings": [SettingEntry…]}`; `PATCH /api/settings/{key}` body `{"value": any, "expected_version": int}` → SettingEntry
  - In `create_app`: `app.state.services = build_services(settings)`; `include_routers(app)` before the `/api` catch-all

> **Auth note:** `/api/settings` is unauthenticated in this task; Task 3 puts every router (except auth) behind `require_session`. Tests here build the app via the `client` fixture in `tests/app/conftest.py`, which Task 3 will upgrade to sign in automatically.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_records.py`:

```python
import pytest

from tuppence.core.db import Database
from tuppence.core.records import NotFound, VersionConflict, update_versioned


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    with d.connection() as conn:
        conn.execute("CREATE TABLE thing (id TEXT PRIMARY KEY, name TEXT, version INTEGER NOT NULL DEFAULT 1, updated_at TEXT)")
        conn.execute("INSERT INTO thing (id, name) VALUES ('a', 'old')")
    return d


def test_update_bumps_version(db):
    with db.transaction() as conn:
        assert update_versioned(conn, "thing", "id", "a", 1, {"name": "new"}, now="2026-10-07T00:00:00Z") == 2
    with db.connection() as conn:
        row = conn.execute("SELECT name, version, updated_at FROM thing").fetchone()
    assert tuple(row) == ("new", 2, "2026-10-07T00:00:00Z")


def test_stale_version_raises_conflict_with_current(db):
    with db.transaction() as conn:
        update_versioned(conn, "thing", "id", "a", 1, {"name": "x"}, now="t")
    with pytest.raises(VersionConflict) as exc, db.transaction() as conn:
        update_versioned(conn, "thing", "id", "a", 1, {"name": "y"}, now="t")
    assert exc.value.current == 2


def test_missing_row_raises_not_found(db):
    with pytest.raises(NotFound), db.transaction() as conn:
        update_versioned(conn, "thing", "id", "zzz", 1, {"name": "y"}, now="t")


def test_rejects_unsafe_identifiers(db):
    with pytest.raises(ValueError), db.transaction() as conn:
        update_versioned(conn, "thing; DROP TABLE thing", "id", "a", 1, {"name": "y"}, now="t")
    with pytest.raises(ValueError), db.transaction() as conn:
        update_versioned(conn, "thing", "id", "a", 1, {"name = 'x', version": "y"}, now="t")
```

`tests/core/test_settings_store.py`:

```python
import pytest

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.core.records import VersionConflict
from tuppence.core.settings_store import SettingInvalid, SettingsStore


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return SettingsStore(db)


def test_defaults_have_version_zero(store):
    e = store.entry("privacy.local_only")
    assert e.value is False and e.version == 0 and e.default is False
    assert store.get("privacy.live_market_data") is True
    assert store.get("config.preset") == "balanced"


def test_set_then_get_and_versioning(store):
    e = store.set("privacy.local_only", True, expected_version=0)
    assert e.value is True and e.version == 1
    e2 = store.set("privacy.local_only", False, expected_version=1)
    assert e2.version == 2 and store.get("privacy.local_only") is False


def test_stale_write_conflicts(store):
    store.set("privacy.local_only", True, expected_version=0)
    with pytest.raises(VersionConflict) as exc:
        store.set("privacy.local_only", False, expected_version=0)
    assert exc.value.current == 1


def test_validation(store):
    with pytest.raises(SettingInvalid):
        store.set("config.preset", "turbo", expected_version=0)
    with pytest.raises(SettingInvalid):
        store.set("llm.monthly_cap_gbp", -1, expected_version=0)
    with pytest.raises(KeyError):
        store.get("no.such.key")


def test_all_lists_every_defined_setting(store):
    keys = {e.key for e in store.all()}
    assert {"privacy.local_only", "privacy.pseudonymise", "config.preset", "llm.monthly_cap_gbp"} <= keys
```

`tests/app/conftest.py`:

```python
import pytest
from fastapi.testclient import TestClient

from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


@pytest.fixture
def make_app(tmp_path):
    def _make(mode="server", **kw):
        settings = RuntimeSettings.for_mode(mode, data_dir=tmp_path / "data", web_dir=tmp_path / "no-ui", **kw)
        return create_app(settings)

    return _make


@pytest.fixture
def client(make_app):
    return TestClient(make_app())
```

`tests/app/test_settings_api.py`:

```python
def test_list_settings(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    keys = {s["key"] for s in r.json()["settings"]}
    assert "privacy.local_only" in keys


def test_patch_setting_and_conflict(client):
    r = client.patch("/api/settings/privacy.local_only", json={"value": True, "expected_version": 0})
    assert r.status_code == 200 and r.json()["version"] == 1
    stale = client.patch("/api/settings/privacy.local_only", json={"value": False, "expected_version": 0})
    assert stale.status_code == 409
    assert stale.json() == {"detail": "This was changed somewhere else. Reload and try again.", "current_version": 1}


def test_patch_invalid_value_is_422(client):
    r = client.patch("/api/settings/config.preset", json={"value": "turbo", "expected_version": 0})
    assert r.status_code == 422 and "config.preset" in r.json()["detail"]


def test_patch_unknown_key_is_404(client):
    assert client.patch("/api/settings/nope", json={"value": 1, "expected_version": 0}).status_code == 404


def test_database_created_in_data_dir(tmp_path, make_app):
    make_app()
    assert (tmp_path / "data" / "tuppence.db").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_records.py tests/core/test_settings_store.py tests/app/test_settings_api.py -q` → FAIL (modules missing).

- [ ] **Step 3: Implement**

`src/tuppence/core/records.py`:

```python
"""Per-record writes with optimistic version checks (spec §3.4)."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class VersionConflict(Exception):
    def __init__(self, table: str, key: object, expected: int, current: int) -> None:
        super().__init__(f"{table} {key!r}: expected version {expected}, found {current}")
        self.table, self.key, self.expected, self.current = table, key, expected, current


class NotFound(Exception):
    def __init__(self, table: str, key: object) -> None:
        super().__init__(f"{table} {key!r} not found")
        self.table, self.key = table, key


def _check(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def update_versioned(
    conn: sqlite3.Connection,
    table: str,
    key_col: str,
    key: object,
    expected_version: int,
    changes: Mapping[str, object],
    *,
    now: str,
) -> int:
    _check(table)
    _check(key_col)
    if not changes:
        raise ValueError("no changes given")
    assignments = ", ".join(f"{_check(col)} = ?" for col in changes)
    sql = f"UPDATE {table} SET {assignments}, version = version + 1, updated_at = ? WHERE {key_col} = ? AND version = ?"  # noqa: S608
    cur = conn.execute(sql, [*changes.values(), now, key, expected_version])
    if cur.rowcount == 1:
        return expected_version + 1
    row = conn.execute(f"SELECT version FROM {table} WHERE {key_col} = ?", [key]).fetchone()  # noqa: S608
    if row is None:
        raise NotFound(table, key)
    raise VersionConflict(table, key, expected_version, int(row[0]))
```

`src/tuppence/core/settings_store.py`:

```python
"""Typed, versioned app settings stored as JSON in `app_settings`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.records import VersionConflict


@dataclass(frozen=True)
class SettingDef:
    key: str
    default: Any
    adapter: TypeAdapter[Any]
    description: str


SETTINGS: dict[str, SettingDef] = {}


def define(key: str, type_: Any, default: Any, description: str) -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(type_)
    SETTINGS[key] = SettingDef(key, adapter.validate_python(default), adapter, description)


define("privacy.local_only", bool, False, "Keep AI and research calls on this device or your home network.")
define("privacy.pseudonymise", bool, False, "Swap names and account numbers for stand-ins before cloud AI calls.")
define("privacy.research_lookups", bool, False, "Let the agent look up merchant names online (never amounts or your details).")
define("privacy.live_market_data", bool, True, "Fetch interest rates, inflation, exchange rates and prices.")
define("privacy.datapack_updates", bool, True, "Download updated UK data packs (tax, benefits, rents).")
define("config.preset", Literal["frugal", "balanced", "thorough"], "balanced", "How much work the agents do.")
define("llm.monthly_cap_gbp", Annotated[float, Field(ge=0)], 10.0, "Monthly AI spending cap in pounds.")
define("llm.run_cap_gbp", Annotated[float, Field(ge=0)], 1.0, "Spending cap for one analysis run, in pounds.")
define("llm.usd_to_gbp", Annotated[float, Field(gt=0)], 0.75, "Exchange rate used to price AI usage.")


class SettingInvalid(Exception):
    def __init__(self, key: str, message: str) -> None:
        super().__init__(f"{key}: {message}")
        self.key = key


class SettingEntry(BaseModel):
    key: str
    value: Any
    default: Any
    version: int
    description: str


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _def(self, key: str) -> SettingDef:
        if key not in SETTINGS:
            raise KeyError(key)
        return SETTINGS[key]

    def entry(self, key: str) -> SettingEntry:
        d = self._def(key)
        with self.db.connection() as conn:
            row = conn.execute("SELECT value, version FROM app_settings WHERE key = ?", [key]).fetchone()
        if row is None:
            return SettingEntry(key=key, value=d.default, default=d.default, version=0, description=d.description)
        value = d.adapter.validate_python(json.loads(row["value"]))
        return SettingEntry(key=key, value=value, default=d.default, version=row["version"], description=d.description)

    def get(self, key: str) -> Any:
        return self.entry(key).value

    def all(self) -> list[SettingEntry]:
        return [self.entry(k) for k in sorted(SETTINGS)]

    def set(self, key: str, value: Any, *, expected_version: int) -> SettingEntry:
        d = self._def(key)
        try:
            clean = d.adapter.validate_python(value)
        except ValidationError as exc:
            raise SettingInvalid(key, exc.errors()[0]["msg"]) from exc
        payload = json.dumps(d.adapter.dump_python(clean, mode="json"))
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            row = conn.execute("SELECT version FROM app_settings WHERE key = ?", [key]).fetchone()
            current = 0 if row is None else int(row["version"])
            if current != expected_version:
                raise VersionConflict("app_settings", key, expected_version, current)
            if row is None:
                conn.execute(
                    "INSERT INTO app_settings (key, value, version, updated_at) VALUES (?, ?, 1, ?)", [key, payload, now]
                )
            else:
                conn.execute(
                    "UPDATE app_settings SET value = ?, version = version + 1, updated_at = ? WHERE key = ?",
                    [payload, now, key],
                )
        return self.entry(key)
```

`src/tuppence/app/services.py`:

```python
"""Everything a request or job needs, built once at startup."""

from __future__ import annotations

from dataclasses import dataclass

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.core.settings_store import SettingsStore
from tuppence.paths import DataPaths
from tuppence.settings import RuntimeSettings


@dataclass
class Services:
    runtime: RuntimeSettings
    paths: DataPaths
    db: Database
    settings: SettingsStore


def build_services(runtime: RuntimeSettings) -> Services:
    paths = DataPaths(runtime.data_dir).ensure()
    db = Database(paths.db)
    migrate(db, paths.backups)
    return Services(runtime=runtime, paths=paths, db=db, settings=SettingsStore(db))
```

`src/tuppence/app/deps.py`:

```python
from __future__ import annotations

from fastapi import Request

from tuppence.app.services import Services


def get_services(request: Request) -> Services:
    return request.app.state.services
```

`src/tuppence/core/errors.py`:

```python
"""Errors shared by the domain core (no web-framework imports here)."""


class InputError(ValueError):
    """User input that's well-formed but not acceptable (HTTP 422)."""
```

`src/tuppence/app/errors.py`:

```python
"""Map domain exceptions to friendly JSON errors."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.settings_store import SettingInvalid

CONFLICT_MESSAGE = "This was changed somewhere else. Reload and try again."


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(VersionConflict)
    async def _conflict(_r: Request, exc: VersionConflict) -> JSONResponse:
        return JSONResponse({"detail": CONFLICT_MESSAGE, "current_version": exc.current}, status_code=409)

    @app.exception_handler(NotFound)
    async def _missing(_r: Request, _exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.exception_handler(SettingInvalid)
    async def _invalid_setting(_r: Request, exc: SettingInvalid) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(InputError)
    async def _invalid_input(_r: Request, exc: InputError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)
```

`src/tuppence/app/routes/settings.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.settings_store import SettingEntry

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingUpdate(BaseModel):
    value: Any
    expected_version: int


@router.get("")
def list_settings(services: Services = Depends(get_services)) -> dict[str, list[SettingEntry]]:
    return {"settings": services.settings.all()}


@router.patch("/{key}")
def update_setting(key: str, body: SettingUpdate, services: Services = Depends(get_services)) -> SettingEntry:
    try:
        return services.settings.set(key, body.value, expected_version=body.expected_version)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown setting") from None
```

`src/tuppence/app/routes/__init__.py`:

```python
"""All API routers. Included by create_app() before the /api catch-all."""

from __future__ import annotations

from fastapi import FastAPI

from tuppence.app.routes import settings


def include_routers(app: FastAPI) -> None:
    app.include_router(settings.router)
```

Modify `src/tuppence/app/factory.py` — inside `create_app`, after setting `app.state.settings`, replace the `app.state.paths = DataPaths(...)` line and add routers:

```python
    services = build_services(settings)
    app.state.services = services
    app.state.paths = services.paths
    install_error_handlers(app)
```

and immediately before `app.include_router(api)` (the `/api` catch-all) add `include_routers(app)`. Add the imports (`from tuppence.app.errors import install_error_handlers`, `from tuppence.app.routes import include_routers`, `from tuppence.app.services import build_services`) and remove the now-unused `DataPaths` import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q` → all pass (M0 tests too). `uv run ruff check . && uv run ruff format --check . && uv run pyright` → clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add versioned records, typed settings store and settings API"
```

---

### Task 3: Login modes — launch token, first-run admin, sessions and CSRF

**Files:**
- Create: `src/tuppence/core/migrations/0002_auth.sql`, `src/tuppence/core/auth.py`, `src/tuppence/app/routes/auth.py`
- Modify: `pyproject.toml` (add `argon2-cffi>=25.1`), `src/tuppence/app/deps.py` (Principal, require_session), `src/tuppence/app/services.py` (users, sessions, limiter; purge launch sessions), `src/tuppence/app/routes/__init__.py` (auth router; protect others), `src/tuppence/cli.py` (launch URL), `src/tuppence/desktop/launcher.py` (launch URL), `tests/app/conftest.py` (signed-in client), `tests/desktop/test_launcher.py`, `tests/test_cli.py` if affected
- Test: `tests/core/test_auth.py`, `tests/app/test_auth_api.py`

**Interfaces:**
- Consumes: `Services`, `Database`, `RuntimeSettings.launch_token`, `RuntimeSettings.secure_cookies`.
- Produces:
  - `tuppence.core.auth.hash_password(pw) -> str`, `verify_password(stored, pw) -> bool`, `validate_new_password(username, pw) -> None` (raises `WeakPassword`)
  - `User` (pydantic: `id: int`, `username: str`, `is_admin: bool`); `Users(db)` with `count() -> int`, `create(username, password, *, is_admin) -> User`, `authenticate(username, password) -> User | None`, `get(user_id) -> User | None`
  - `Session` (pydantic: `kind: Literal["user","launch"]`, `user_id: int | None`, `csrf_token: str`, `expires_at: str`); `Sessions(db, *, ttl_days=30)` with `create(kind, user_id=None) -> tuple[str, Session]` (returns raw token), `get(token) -> Session | None` (sliding renewal), `delete(token) -> None`, `purge_kind(kind) -> int`
  - `LoginLimiter(db, *, max_failures=5, lockout_seconds=60)` with `retry_after(key) -> int | None`, `record_failure(key) -> None`, `reset(key) -> None`
  - `tuppence.app.deps.Principal` (pydantic: `kind`, `user_id`, `username: str | None`, `csrf_token`), `require_session(request) -> Principal` (401 `{"detail": "Sign in required."}`; 403 `{"detail": "Missing or invalid CSRF token."}` on unsafe methods)
  - `SESSION_COOKIE = "tuppence_session"`
  - Routes: `GET /auth/launch?token=` (local/desktop only; 303 → `/` with cookie, or 403 HTML containing "This link has expired"); `GET /api/auth/session` → `{"authenticated", "mode", "needs_setup", "user": {"username","is_admin"} | null, "csrf_token": str | null}`; `POST /api/auth/setup` `{"username","password"}` (server mode only; 409 `{"detail": "Setup is already complete."}` once a user exists); `POST /api/auth/login` (server only; 401 `{"detail": "Wrong username or password."}`; 429 with `Retry-After` and `{"detail": "Too many attempts. Try again in N seconds."}`); `POST /api/auth/logout` → 204
  - `include_routers` puts `settings` (and every later non-auth router) under `dependencies=[Depends(require_session)]`
  - CLI `serve` in local mode prints and opens `http://127.0.0.1:<port>/auth/launch?token=<token>`; desktop window opens the same path
  - Test helper: fixture `client` (server mode, admin `admin`/`correct-horse-battery` already set up, CSRF header attached) and fixture `anon_client`

- [ ] **Step 1: Write the failing tests**

`tests/core/test_auth.py`:

```python
import pytest

from tuppence.core.auth import LoginLimiter, Sessions, Users, WeakPassword, hash_password, validate_new_password, verify_password
from tuppence.core.db import Database
from tuppence.core.migrate import migrate


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    migrate(d, tmp_path / "b")
    return d


def test_password_hash_roundtrip():
    h = hash_password("correct-horse-battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct-horse-battery")
    assert not verify_password(h, "wrong-password-1")


def test_password_policy():
    with pytest.raises(WeakPassword):
        validate_new_password("alex", "short")
    with pytest.raises(WeakPassword):
        validate_new_password("alexander1", "alexander1")
    validate_new_password("alex", "a-much-longer-passphrase")


def test_users_create_and_authenticate(db):
    users = Users(db)
    assert users.count() == 0
    u = users.create("Alex", "correct-horse-battery", is_admin=True)
    assert u.is_admin and users.count() == 1
    assert users.authenticate("alex", "correct-horse-battery").id == u.id  # case-insensitive username
    assert users.authenticate("alex", "nope-nope-nope") is None
    assert users.authenticate("nobody", "correct-horse-battery") is None


def test_sessions_create_get_delete(db):
    sessions = Sessions(db)
    token, s = sessions.create("launch")
    assert len(token) >= 32 and s.kind == "launch" and len(s.csrf_token) >= 32
    assert sessions.get(token).csrf_token == s.csrf_token
    assert sessions.get("not-a-token") is None
    sessions.delete(token)
    assert sessions.get(token) is None


def test_expired_session_is_rejected(db):
    sessions = Sessions(db, ttl_days=0)
    token, _ = sessions.create("launch")
    assert sessions.get(token) is None


def test_purge_kind(db):
    sessions = Sessions(db)
    t1, _ = sessions.create("launch")
    users = Users(db)
    u = users.create("alex", "correct-horse-battery", is_admin=True)
    t2, _ = sessions.create("user", u.id)
    assert sessions.purge_kind("launch") == 1
    assert sessions.get(t1) is None and sessions.get(t2) is not None


def test_login_limiter_locks_after_five_failures(db):
    limiter = LoginLimiter(db, max_failures=5, lockout_seconds=60)
    key = "127.0.0.1|alex"
    for _ in range(5):
        assert limiter.retry_after(key) is None
        limiter.record_failure(key)
    wait = limiter.retry_after(key)
    assert wait is not None and 0 < wait <= 60
    limiter.reset(key)
    assert limiter.retry_after(key) is None
```

`tests/app/test_auth_api.py`:

```python
from fastapi.testclient import TestClient


def test_server_mode_needs_setup_then_login(make_app):
    c = TestClient(make_app("server"))
    s = c.get("/api/auth/session").json()
    assert s == {"authenticated": False, "mode": "server", "needs_setup": True, "user": None, "csrf_token": None}
    assert c.get("/api/settings").status_code == 401

    r = c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    assert r.status_code == 200 and r.json()["authenticated"] is True
    assert "tuppence_session" in r.cookies
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    csrf = r.json()["csrf_token"]
    assert c.get("/api/settings").status_code == 200

    again = c.post("/api/auth/setup", json={"username": "x", "password": "another-long-one"}, headers={"X-CSRF-Token": csrf})
    assert again.status_code == 409


def test_setup_rejects_weak_password(make_app):
    c = TestClient(make_app("server"))
    r = c.post("/api/auth/setup", json={"username": "alex", "password": "short"})
    assert r.status_code == 422 and "10 characters" in r.json()["detail"]


def test_csrf_required_for_unsafe_methods(client):
    ok = client.patch("/api/settings/privacy.local_only", json={"value": True, "expected_version": 0})
    assert ok.status_code == 200
    client.headers.pop("X-CSRF-Token")
    bad = client.patch("/api/settings/privacy.local_only", json={"value": False, "expected_version": 1})
    assert bad.status_code == 403 and bad.json()["detail"] == "Missing or invalid CSRF token."


def test_login_logout_and_wrong_password(make_app):
    c = TestClient(make_app("server"))
    c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    csrf = c.get("/api/auth/session").json()["csrf_token"]
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert c.get("/api/auth/session").json()["authenticated"] is False
    bad = c.post("/api/auth/login", json={"username": "alex", "password": "wrong-password-1"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Wrong username or password."
    good = c.post("/api/auth/login", json={"username": "ALEX", "password": "correct-horse-battery"})
    assert good.status_code == 200 and good.json()["user"]["username"] == "alex"


def test_login_lockout(make_app):
    c = TestClient(make_app("server"))
    c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    c.cookies.clear()
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "alex", "password": "nope-nope-nope"}).status_code == 401
    locked = c.post("/api/auth/login", json={"username": "alex", "password": "correct-horse-battery"})
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    assert "Too many attempts" in locked.json()["detail"]


def test_cross_origin_login_rejected(make_app):
    c = TestClient(make_app("server"))
    r = c.post(
        "/api/auth/setup",
        json={"username": "alex", "password": "correct-horse-battery"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_launch_token_flow(make_app):
    app = make_app("desktop", launch_token="T" * 43)
    c = TestClient(app, follow_redirects=False)
    assert c.get("/api/settings").status_code == 401
    bad = c.get("/auth/launch", params={"token": "nope"})
    assert bad.status_code == 403 and "This link has expired" in bad.text
    r = c.get("/auth/launch", params={"token": "T" * 43})
    assert r.status_code == 303 and r.headers["location"] == "/"
    s = c.get("/api/auth/session").json()
    assert s["authenticated"] is True and s["mode"] == "desktop" and s["user"] is None
    assert c.get("/api/settings").status_code == 200


def test_launch_route_absent_in_server_mode(make_app):
    c = TestClient(make_app("server"), follow_redirects=False)
    assert c.get("/auth/launch", params={"token": "x"}).status_code == 404


def test_old_launch_sessions_purged_on_restart(tmp_path, make_app):
    c1 = TestClient(make_app("desktop", launch_token="A" * 43), follow_redirects=False)
    c1.get("/auth/launch", params={"token": "A" * 43})
    cookie = c1.cookies.get("tuppence_session")
    c2 = TestClient(make_app("desktop", launch_token="B" * 43))
    c2.cookies.set("tuppence_session", cookie)
    assert c2.get("/api/settings").status_code == 401
```

Update `tests/app/conftest.py` (replace the `client` fixture, keep `make_app`):

```python
@pytest.fixture
def anon_client(make_app):
    return TestClient(make_app())


@pytest.fixture
def client(make_app):
    c = TestClient(make_app("server"))
    r = c.post("/api/auth/setup", json={"username": "admin", "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv add argon2-cffi` then `uv run pytest tests/core/test_auth.py tests/app/test_auth_api.py -q` → FAIL (module/route missing).

- [ ] **Step 3: Implement**

`src/tuppence/core/migrations/0002_auth.sql`:

```sql
CREATE TABLE app_user (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(username) BETWEEN 1 AND 64),
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE session (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('user', 'launch')),
  user_id INTEGER REFERENCES app_user(id) ON DELETE CASCADE,
  csrf_token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE login_attempt (
  key TEXT PRIMARY KEY,
  failures INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT
);
```

`src/tuppence/core/auth.py`:

```python
"""Users, sessions and login throttling (spec §3.1, §14.2)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import BaseModel

from tuppence.core.clock import from_iso, to_iso, utcnow
from tuppence.core.db import Database

_hasher = PasswordHasher()
MIN_PASSWORD = 10


class WeakPassword(ValueError):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_new_password(username: str, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise WeakPassword(f"Use at least {MIN_PASSWORD} characters for your password.")
    if password.strip().lower() == username.strip().lower():
        raise WeakPassword("Your password can't be the same as your username.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class User(BaseModel):
    id: int
    username: str
    is_admin: bool


class Users:
    def __init__(self, db: Database) -> None:
        self.db = db

    def count(self) -> int:
        with self.db.connection() as conn:
            return int(conn.execute("SELECT count(*) FROM app_user").fetchone()[0])

    def create(self, username: str, password: str, *, is_admin: bool) -> User:
        username = username.strip()
        validate_new_password(username, password)
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO app_user (username, password_hash, is_admin, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                [username, hash_password(password), int(is_admin), now, now],
            )
            user_id = int(cur.lastrowid or 0)
        return User(id=user_id, username=username, is_admin=is_admin)

    def authenticate(self, username: str, password: str) -> User | None:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, is_admin FROM app_user WHERE username = ?", [username.strip()]
            ).fetchone()
        if row is None:
            _hasher.hash(password)  # keep timing similar for unknown users
            return None
        if not verify_password(row["password_hash"], password):
            return None
        return User(id=row["id"], username=row["username"], is_admin=bool(row["is_admin"]))

    def get(self, user_id: int) -> User | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT id, username, is_admin FROM app_user WHERE id = ?", [user_id]).fetchone()
        return None if row is None else User(id=row["id"], username=row["username"], is_admin=bool(row["is_admin"]))


class Session(BaseModel):
    kind: Literal["user", "launch"]
    user_id: int | None
    csrf_token: str
    expires_at: str


class Sessions:
    def __init__(self, db: Database, *, ttl_days: int = 30) -> None:
        self.db = db
        self.ttl = timedelta(days=ttl_days)

    def create(self, kind: Literal["user", "launch"], user_id: int | None = None) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = utcnow()
        expires = to_iso(now + self.ttl)
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO session (token_hash, kind, user_id, csrf_token, created_at, expires_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [_token_hash(token), kind, user_id, csrf, to_iso(now), expires, to_iso(now)],
            )
        return token, Session(kind=kind, user_id=user_id, csrf_token=csrf, expires_at=expires)

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        key = _token_hash(token)
        now = utcnow()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM session WHERE token_hash = ?", [key]).fetchone()
            if row is None:
                return None
            if from_iso(row["expires_at"]) <= now:
                conn.execute("DELETE FROM session WHERE token_hash = ?", [key])
                return None
            expires = row["expires_at"]
            if now - from_iso(row["last_seen_at"]) > timedelta(hours=1):
                expires = to_iso(now + self.ttl)
                conn.execute(
                    "UPDATE session SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?", [to_iso(now), expires, key]
                )
        return Session(kind=row["kind"], user_id=row["user_id"], csrf_token=row["csrf_token"], expires_at=expires)

    def delete(self, token: str | None) -> None:
        if token:
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM session WHERE token_hash = ?", [_token_hash(token)])

    def purge_kind(self, kind: Literal["user", "launch"]) -> int:
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM session WHERE kind = ?", [kind]).rowcount


class LoginLimiter:
    def __init__(self, db: Database, *, max_failures: int = 5, lockout_seconds: int = 60) -> None:
        self.db = db
        self.max_failures = max_failures
        self.lockout = timedelta(seconds=lockout_seconds)

    def retry_after(self, key: str) -> int | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT locked_until FROM login_attempt WHERE key = ?", [key]).fetchone()
        if row is None or row["locked_until"] is None:
            return None
        remaining = (from_iso(row["locked_until"]) - utcnow()).total_seconds()
        return int(remaining) + 1 if remaining > 0 else None

    def record_failure(self, key: str) -> None:
        now = utcnow()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT failures, locked_until FROM login_attempt WHERE key = ?", [key]).fetchone()
            failures = 1 if row is None else int(row["failures"]) + 1
            if row is not None and row["locked_until"] and from_iso(row["locked_until"]) <= now:
                failures = 1
            locked = to_iso(now + self.lockout) if failures >= self.max_failures else None
            conn.execute(
                "INSERT INTO login_attempt (key, failures, locked_until) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET failures = excluded.failures, locked_until = excluded.locked_until",
                [key, failures, locked],
            )

    def reset(self, key: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM login_attempt WHERE key = ?", [key])
```

Add to `Services` (in `services.py`) the fields `users: Users`, `sessions: Sessions`, `limiter: LoginLimiter`, built in `build_services`; and in `build_services`, when `runtime.mode in ("local", "desktop")`, call `sessions.purge_kind("launch")` so links and cookies from previous launches stop working.

`src/tuppence/app/deps.py` (extend):

```python
from __future__ import annotations

import secrets
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel

from tuppence.app.services import Services

SESSION_COOKIE = "tuppence_session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class Principal(BaseModel):
    kind: Literal["user", "launch"]
    user_id: int | None
    username: str | None
    csrf_token: str


def get_services(request: Request) -> Services:
    return request.app.state.services


def require_session(request: Request) -> Principal:
    services = get_services(request)
    session = services.sessions.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(sent, session.csrf_token):
            raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")
    username = None
    if session.user_id is not None:
        user = services.users.get(session.user_id)
        username = user.username if user else None
    principal = Principal(kind=session.kind, user_id=session.user_id, username=username, csrf_token=session.csrf_token)
    request.state.principal = principal
    return principal


def check_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("host", "")
    if origin.split("://", 1)[-1].rstrip("/") != host:
        raise HTTPException(status_code=403, detail="Cross-site request blocked.")
```

`src/tuppence/app/routes/auth.py`:

```python
"""Sign-in: launch links (local/desktop) and accounts (server mode)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from tuppence.app.deps import SESSION_COOKIE, check_same_origin, get_services, require_session
from tuppence.app.services import Services
from tuppence.core.auth import WeakPassword

router = APIRouter(tags=["auth"])
MAX_AGE = 30 * 24 * 3600

EXPIRED_HTML = """<!doctype html><html lang="en-GB"><meta charset="utf-8"><title>Tuppence</title>
<body style="font-family: system-ui, sans-serif; max-width: 36rem; margin: 3rem auto; padding: 0 1rem">
<h1>This link has expired</h1><p>Close this tab and open Tuppence again from your apps or terminal.</p></body></html>"""


class Credentials(BaseModel):
    username: str
    password: str


class SessionUser(BaseModel):
    username: str
    is_admin: bool


class SessionInfo(BaseModel):
    authenticated: bool
    mode: str
    needs_setup: bool
    user: SessionUser | None
    csrf_token: str | None


def _set_cookie(response: Response, token: str, services: Services) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=MAX_AGE, httponly=True, samesite="strict",
        secure=services.runtime.secure_cookies, path="/",
    )


def _info(services: Services, token: str | None) -> SessionInfo:
    mode = services.runtime.mode
    needs_setup = mode == "server" and services.users.count() == 0
    session = services.sessions.get(token)
    if session is None:
        return SessionInfo(authenticated=False, mode=mode, needs_setup=needs_setup, user=None, csrf_token=None)
    user = services.users.get(session.user_id) if session.user_id is not None else None
    return SessionInfo(
        authenticated=True, mode=mode, needs_setup=needs_setup,
        user=SessionUser(username=user.username, is_admin=user.is_admin) if user else None,
        csrf_token=session.csrf_token,
    )


@router.get("/auth/launch", include_in_schema=False)
def launch(token: str, services: Services = Depends(get_services)) -> Response:
    expected = services.runtime.launch_token
    if services.runtime.mode == "server" or not expected:
        raise HTTPException(status_code=404)
    if not secrets.compare_digest(token, expected):
        return HTMLResponse(EXPIRED_HTML, status_code=403)
    raw, _ = services.sessions.create("launch")
    response = RedirectResponse("/", status_code=303)
    _set_cookie(response, raw, services)
    return response


@router.get("/api/auth/session")
def session_info(request: Request, services: Services = Depends(get_services)) -> SessionInfo:
    return _info(services, request.cookies.get(SESSION_COOKIE))


def _start_user_session(services: Services, user_id: int) -> tuple[str, JSONResponse]:
    raw, _ = services.sessions.create("user", user_id)
    info = _info(services, raw)
    response = JSONResponse(info.model_dump())
    _set_cookie(response, raw, services)
    return raw, response


@router.post("/api/auth/setup", dependencies=[Depends(check_same_origin)])
def setup(body: Credentials, services: Services = Depends(get_services)) -> JSONResponse:
    if services.runtime.mode != "server":
        raise HTTPException(status_code=404)
    if services.users.count() > 0:
        raise HTTPException(status_code=409, detail="Setup is already complete.")
    try:
        user = services.users.create(body.username, body.password, is_admin=True)
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _start_user_session(services, user.id)[1]


@router.post("/api/auth/login", dependencies=[Depends(check_same_origin)])
def login(body: Credentials, request: Request, services: Services = Depends(get_services)) -> JSONResponse:
    if services.runtime.mode != "server":
        raise HTTPException(status_code=404)
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}|{body.username.strip().lower()}"
    wait = services.limiter.retry_after(key)
    if wait is not None:
        return JSONResponse(
            {"detail": f"Too many attempts. Try again in {wait} seconds."}, status_code=429,
            headers={"Retry-After": str(wait)},
        )
    user = services.users.authenticate(body.username, body.password)
    if user is None:
        services.limiter.record_failure(key)
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    services.limiter.reset(key)
    return _start_user_session(services, user.id)[1]


@router.post("/api/auth/logout", status_code=204, dependencies=[Depends(require_session)])
def logout(request: Request, services: Services = Depends(get_services)) -> Response:
    services.sessions.delete(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
```

`src/tuppence/app/routes/__init__.py`:

```python
"""All API routers. Included by create_app() before the /api catch-all."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from tuppence.app.deps import require_session
from tuppence.app.routes import auth, settings

PROTECTED = [settings.router]


def include_routers(app: FastAPI) -> None:
    app.include_router(auth.router)
    for router in PROTECTED:
        app.include_router(router, dependencies=[Depends(require_session)])
```

CLI (`src/tuppence/cli.py`, `_serve`): in local mode create `token = secrets.token_urlsafe(32)` and pass `launch_token=token` to `RuntimeSettings.for_mode`; print and open `f"{url}auth/launch?token={token}"` instead of `url` (server mode prints the plain URL). Desktop (`launcher.py`): open the window/browser at `server.url + "auth/launch?token=" + settings.launch_token`; keep `server.url` in the smoke JSON. Update `tests/desktop/test_launcher.py::test_falls_back_to_browser_when_no_gui` to assert `"/auth/launch?token=" in opened[0]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q` → all pass (update any M0 test that now gets 401 from `/api/*`; `/health` stays public). Lint + pyright clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add sign-in: launch links, first-run admin, sessions, CSRF and login lockout"
```

---

### Task 4: Household, people and the effective-dated profile timeline

**Files:**
- Create: `src/tuppence/core/timeline.py`, `src/tuppence/core/household.py`, `src/tuppence/app/routes/household.py`
- Modify: `src/tuppence/app/services.py` (add `household: HouseholdService`, `timeline: Timeline`), `src/tuppence/app/routes/__init__.py` (add to `PROTECTED`)
- Test: `tests/core/test_timeline.py`, `tests/core/test_household.py`, `tests/app/test_household_api.py`

**Interfaces:**
- Consumes: `Database`, `update_versioned`, `VersionConflict`, `NotFound`, `tuppence.core.errors.InputError`.
- Produces:
  - `tuppence.core.timeline.TimelineEntry` (pydantic: `id`, `subject_type`, `subject_id`, `attribute`, `value: Any`, `valid_from: date`, `valid_to: date | None`, `source`)
  - `Timeline(db)` with `set(subject_type, subject_id, attribute, value, valid_from: date, *, source="user") -> TimelineEntry`, `end(subject_type, subject_id, attribute, valid_to: date) -> None`, `value_as_of(subject_type, subject_id, attribute, on: date) -> Any | None`, `as_of(subject_type, subject_id, on: date) -> dict[str, Any]`, `history(subject_type, subject_id, attribute: str | None = None) -> list[TimelineEntry]`
  - Allowed attributes (others → `InputError`): person: `employment_status` ∈ {employed, self_employed, both, retired, student, not_working}; `income_band` ∈ {under_12570, 12570_50270, 50270_100000, 100000_125140, over_125140}; `household_member` bool. household: `nation` (as household), `postcode_district` (as below). `ALLOWED_ATTRIBUTES: dict[str, dict[str, TypeAdapter]]` exported for later milestones to extend.
  - `tuppence.core.household.Person` (`id`, `display_name`, `role`, `birth_year`, `status`, `version`), `Household` (`nation`, `postcode_district`, `currency`, `period_mode`, `period_anchor_person_id`, `version`)
  - `PersonIn` (display_name 1–60 chars trimmed, role, birth_year optional 1900..current year), `PersonPatch` (all optional), `HouseholdPatch` (optional fields)
  - `normalise_district(value: str) -> str` — uppercases/trims; accepts outward codes matching `^[A-Z]{1,2}[0-9][A-Z0-9]?$`; a full postcode raises `InputError("Just the first part of your postcode, please (for example LS6).")`; anything else raises `InputError("That doesn't look like a UK postcode district (for example LS6).")`
  - `HouseholdService(db)`: `get() -> Household` (creates the singleton row lazily), `update(changes: HouseholdPatch, expected_version) -> Household`, `list_people(include_retired=False) -> list[Person]`, `get_person(id) -> Person`, `create_person(data: PersonIn) -> Person` (id `p_` + 8 hex chars), `update_person(id, changes: PersonPatch, expected_version) -> Person`, `retire_person(id, expected_version) -> Person`
  - Routes: `GET /api/household`; `PATCH /api/household` `{"changes": {...}, "expected_version": n}`; `GET /api/household/people?include_retired=false`; `POST /api/household/people` (201); `PATCH /api/household/people/{id}`; `POST /api/household/people/{id}/retire` `{"expected_version": n}`; `GET /api/household/timeline?subject_type=&subject_id=`; `POST /api/household/timeline` `{"subject_type","subject_id","attribute","value","valid_from"}` (201)

- [ ] **Step 1: Write the failing tests**

`tests/core/test_timeline.py`:

```python
from datetime import date

import pytest

from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.migrate import migrate
from tuppence.core.timeline import Timeline


@pytest.fixture
def tl(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return Timeline(db)


def test_change_of_employment_closes_previous_interval(tl):
    tl.set("person", "p1", "employment_status", "employed", date(2024, 1, 1))
    tl.set("person", "p1", "employment_status", "not_working", date(2026, 5, 1))
    assert tl.value_as_of("person", "p1", "employment_status", date(2026, 4, 30)) == "employed"
    assert tl.value_as_of("person", "p1", "employment_status", date(2026, 5, 1)) == "not_working"
    hist = tl.history("person", "p1", "employment_status")
    assert [(h.valid_from, h.valid_to) for h in hist] == [(date(2024, 1, 1), date(2026, 5, 1)), (date(2026, 5, 1), None)]


def test_backdated_change_inserted_between_entries(tl):
    tl.set("person", "p1", "employment_status", "employed", date(2024, 1, 1))
    tl.set("person", "p1", "employment_status", "retired", date(2027, 1, 1))
    tl.set("person", "p1", "employment_status", "self_employed", date(2025, 6, 1))
    hist = tl.history("person", "p1", "employment_status")
    assert [(h.value, h.valid_from, h.valid_to) for h in hist] == [
        ("employed", date(2024, 1, 1), date(2025, 6, 1)),
        ("self_employed", date(2025, 6, 1), date(2027, 1, 1)),
        ("retired", date(2027, 1, 1), None),
    ]


def test_same_day_set_replaces_value(tl):
    tl.set("person", "p1", "income_band", "12570_50270", date(2026, 4, 6))
    tl.set("person", "p1", "income_band", "50270_100000", date(2026, 4, 6))
    hist = tl.history("person", "p1", "income_band")
    assert len(hist) == 1 and hist[0].value == "50270_100000"


def test_before_first_entry_is_none_and_as_of_collects(tl):
    tl.set("person", "p1", "employment_status", "employed", date(2024, 1, 1))
    tl.set("person", "p1", "household_member", True, date(2024, 1, 1))
    assert tl.value_as_of("person", "p1", "employment_status", date(2023, 12, 31)) is None
    assert tl.as_of("person", "p1", date(2025, 1, 1)) == {"employment_status": "employed", "household_member": True}


def test_end_closes_open_interval(tl):
    tl.set("person", "p1", "household_member", True, date(2024, 1, 1))
    tl.end("person", "p1", "household_member", date(2026, 3, 1))
    assert tl.value_as_of("person", "p1", "household_member", date(2026, 3, 1)) is None
    assert tl.value_as_of("person", "p1", "household_member", date(2026, 2, 28)) is True


def test_rejects_unknown_attribute_and_bad_value(tl):
    with pytest.raises(InputError):
        tl.set("person", "p1", "shoe_size", 9, date(2026, 1, 1))
    with pytest.raises(InputError):
        tl.set("person", "p1", "employment_status", "astronaut", date(2026, 1, 1))
```

`tests/core/test_household.py`:

```python
import pytest

from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.household import HouseholdPatch, HouseholdService, PersonIn, PersonPatch, normalise_district
from tuppence.core.migrate import migrate
from tuppence.core.records import VersionConflict


@pytest.fixture
def hh(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return HouseholdService(db)


@pytest.mark.parametrize("raw,expected", [("ls6", "LS6"), (" sw1a ", "SW1A"), ("M1", "M1"), ("cf10", "CF10")])
def test_normalise_district(raw, expected):
    assert normalise_district(raw) == expected


@pytest.mark.parametrize("raw", ["LS6 2AB", "ls62ab", "SW1A 1AA"])
def test_full_postcode_rejected_with_friendly_message(raw):
    with pytest.raises(InputError) as exc:
        normalise_district(raw)
    assert "first part of your postcode" in str(exc.value)


def test_nonsense_rejected():
    with pytest.raises(InputError):
        normalise_district("hello")


def test_household_singleton_and_update(hh):
    h = hh.get()
    assert h.version == 1 and h.currency == "GBP" and h.nation is None
    h2 = hh.update(HouseholdPatch(nation="wales", postcode_district="cf10"), expected_version=1)
    assert h2.nation == "wales" and h2.postcode_district == "CF10" and h2.version == 2
    with pytest.raises(VersionConflict):
        hh.update(HouseholdPatch(nation="england"), expected_version=1)


def test_people_lifecycle(hh):
    alex = hh.create_person(PersonIn(display_name="  Alex Example ", role="adult"))
    kid = hh.create_person(PersonIn(display_name="Kid A", role="child", birth_year=2019))
    assert alex.display_name == "Alex Example" and alex.id.startswith("p_")
    assert [p.display_name for p in hh.list_people()] == ["Alex Example", "Kid A"]
    renamed = hh.update_person(kid.id, PersonPatch(display_name="Kid Alpha"), expected_version=1)
    assert renamed.version == 2
    hh.retire_person(kid.id, expected_version=2)
    assert [p.display_name for p in hh.list_people()] == ["Alex Example"]
    assert len(hh.list_people(include_retired=True)) == 2


def test_person_validation():
    with pytest.raises(ValueError):
        PersonIn(display_name="", role="adult")
    with pytest.raises(ValueError):
        PersonIn(display_name="Kid", role="child", birth_year=1800)
```

`tests/app/test_household_api.py`:

```python
def test_people_api_roundtrip(client):
    r = client.post("/api/household/people", json={"display_name": "Alex Example", "role": "adult"})
    assert r.status_code == 201
    pid = r.json()["id"]
    people = client.get("/api/household/people").json()["people"]
    assert [p["id"] for p in people] == [pid]
    upd = client.patch(f"/api/household/people/{pid}", json={"changes": {"display_name": "Alex E."}, "expected_version": 1})
    assert upd.status_code == 200 and upd.json()["version"] == 2
    stale = client.patch(f"/api/household/people/{pid}", json={"changes": {"display_name": "X"}, "expected_version": 1})
    assert stale.status_code == 409
    gone = client.post(f"/api/household/people/{pid}/retire", json={"expected_version": 2})
    assert gone.status_code == 200 and gone.json()["status"] == "retired"


def test_household_patch_rejects_full_postcode(client):
    h = client.get("/api/household").json()
    r = client.patch("/api/household", json={"changes": {"postcode_district": "LS6 2AB"}, "expected_version": h["version"]})
    assert r.status_code == 422 and "first part of your postcode" in r.json()["detail"]
    assert client.get("/api/household").json()["postcode_district"] is None


def test_timeline_api(client):
    pid = client.post("/api/household/people", json={"display_name": "Alex Example", "role": "adult"}).json()["id"]
    r = client.post(
        "/api/household/timeline",
        json={"subject_type": "person", "subject_id": pid, "attribute": "employment_status", "value": "employed", "valid_from": "2024-01-01"},
    )
    assert r.status_code == 201
    entries = client.get("/api/household/timeline", params={"subject_type": "person", "subject_id": pid}).json()["entries"]
    assert entries[0]["value"] == "employed" and entries[0]["valid_to"] is None


def test_household_requires_sign_in(anon_client):
    assert anon_client.get("/api/household").status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_timeline.py tests/core/test_household.py tests/app/test_household_api.py -q` → FAIL.

- [ ] **Step 3: Implement**

`src/tuppence/core/timeline.py`:

```python
"""Effective-dated profile attributes (spec §5.3).

Intervals are [valid_from, valid_to): valid_to is exclusive and NULL means "still true".
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError

SubjectType = Literal["household", "person", "account", "income_source"]

ALLOWED_ATTRIBUTES: dict[str, dict[str, TypeAdapter[Any]]] = {
    "person": {
        "employment_status": TypeAdapter(Literal["employed", "self_employed", "both", "retired", "student", "not_working"]),
        "income_band": TypeAdapter(
            Literal["under_12570", "12570_50270", "50270_100000", "100000_125140", "over_125140"]
        ),
        "household_member": TypeAdapter(bool),
    },
    "household": {
        "nation": TypeAdapter(Literal["england", "wales", "scotland", "northern_ireland"]),
        "postcode_district": TypeAdapter(str),
    },
}


class TimelineEntry(BaseModel):
    id: int
    subject_type: str
    subject_id: str
    attribute: str
    value: Any
    valid_from: date
    valid_to: date | None
    source: str


def _validate(subject_type: str, attribute: str, value: Any) -> Any:
    adapters = ALLOWED_ATTRIBUTES.get(subject_type, {})
    if attribute not in adapters:
        raise InputError(f"'{attribute}' can't be recorded for a {subject_type}.")
    try:
        return adapters[attribute].validate_python(value)
    except ValidationError as exc:
        raise InputError(f"{attribute}: {exc.errors()[0]['msg']}") from exc


def _row(row: Any) -> TimelineEntry:
    return TimelineEntry(
        id=row["id"], subject_type=row["subject_type"], subject_id=row["subject_id"], attribute=row["attribute"],
        value=json.loads(row["value"]), valid_from=date.fromisoformat(row["valid_from"]),
        valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None, source=row["source"],
    )


class Timeline:
    def __init__(self, db: Database) -> None:
        self.db = db

    def set(
        self, subject_type: str, subject_id: str, attribute: str, value: Any, valid_from: date, *, source: str = "user"
    ) -> TimelineEntry:
        clean = _validate(subject_type, attribute, value)
        start = valid_from.isoformat()
        payload = json.dumps(clean)
        key = [subject_type, subject_id, attribute]
        with self.db.transaction() as conn:
            same = conn.execute(
                "SELECT id FROM profile_entry WHERE subject_type=? AND subject_id=? AND attribute=? AND valid_from=?",
                [*key, start],
            ).fetchone()
            if same is not None:
                conn.execute("UPDATE profile_entry SET value = ?, source = ? WHERE id = ?", [payload, source, same["id"]])
                entry_id = same["id"]
            else:
                nxt = conn.execute(
                    "SELECT valid_from FROM profile_entry WHERE subject_type=? AND subject_id=? AND attribute=?"
                    " AND valid_from > ? ORDER BY valid_from LIMIT 1",
                    [*key, start],
                ).fetchone()
                conn.execute(
                    "UPDATE profile_entry SET valid_to = ? WHERE subject_type=? AND subject_id=? AND attribute=?"
                    " AND valid_from < ? AND (valid_to IS NULL OR valid_to > ?)",
                    [start, *key, start, start],
                )
                cur = conn.execute(
                    "INSERT INTO profile_entry (subject_type, subject_id, attribute, value, valid_from, valid_to, source, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [*key, payload, start, nxt["valid_from"] if nxt else None, source, to_iso(utcnow())],
                )
                entry_id = cur.lastrowid
            row = conn.execute("SELECT * FROM profile_entry WHERE id = ?", [entry_id]).fetchone()
        return _row(row)

    def end(self, subject_type: str, subject_id: str, attribute: str, valid_to: date) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE profile_entry SET valid_to = ? WHERE subject_type=? AND subject_id=? AND attribute=?"
                " AND valid_to IS NULL AND valid_from < ?",
                [valid_to.isoformat(), subject_type, subject_id, attribute, valid_to.isoformat()],
            )

    def value_as_of(self, subject_type: str, subject_id: str, attribute: str, on: date) -> Any | None:
        day = on.isoformat()
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT value FROM profile_entry WHERE subject_type=? AND subject_id=? AND attribute=?"
                " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) ORDER BY valid_from DESC LIMIT 1",
                [subject_type, subject_id, attribute, day, day],
            ).fetchone()
        return None if row is None else json.loads(row["value"])

    def as_of(self, subject_type: str, subject_id: str, on: date) -> dict[str, Any]:
        day = on.isoformat()
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT attribute, value FROM profile_entry WHERE subject_type=? AND subject_id=?"
                " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) ORDER BY attribute, valid_from",
                [subject_type, subject_id, day, day],
            ).fetchall()
        return {r["attribute"]: json.loads(r["value"]) for r in rows}

    def history(self, subject_type: str, subject_id: str, attribute: str | None = None) -> list[TimelineEntry]:
        sql = "SELECT * FROM profile_entry WHERE subject_type=? AND subject_id=?"
        params: list[Any] = [subject_type, subject_id]
        if attribute is not None:
            sql += " AND attribute = ?"
            params.append(attribute)
        with self.db.connection() as conn:
            rows = conn.execute(sql + " ORDER BY attribute, valid_from", params).fetchall()
        return [_row(r) for r in rows]
```

`src/tuppence/core/household.py`:

```python
"""The household and its people (spec §5.2, §5.5)."""

from __future__ import annotations

import re
import secrets
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, update_versioned

Nation = Literal["england", "wales", "scotland", "northern_ireland"]
Role = Literal["adult", "child", "dependent_adult"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]

_DISTRICT = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
_FULL = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}$")


def normalise_district(value: str) -> str:
    v = value.strip().upper()
    if _FULL.match(v):
        raise InputError("Just the first part of your postcode, please (for example LS6).")
    if not _DISTRICT.match(v):
        raise InputError("That doesn't look like a UK postcode district (for example LS6).")
    return v


def _birth_year_ok(v: int | None) -> int | None:
    if v is not None and not (1900 <= v <= date.today().year):
        raise ValueError("Birth year must be between 1900 and this year.")
    return v


class PersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: Name
    role: Role
    birth_year: int | None = None

    @field_validator("birth_year")
    @classmethod
    def _check_year(cls, v: int | None) -> int | None:
        return _birth_year_ok(v)


class PersonPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: Name | None = None
    role: Role | None = None
    birth_year: int | None = None

    @field_validator("birth_year")
    @classmethod
    def _check_year(cls, v: int | None) -> int | None:
        return _birth_year_ok(v)


class Person(BaseModel):
    id: str
    display_name: str
    role: Role
    birth_year: int | None
    status: Literal["active", "retired"]
    version: int


class HouseholdPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nation: Nation | None = None
    postcode_district: str | None = None
    period_mode: Literal["calendar_month", "pay_cycle"] | None = None
    period_anchor_person_id: str | None = None


class Household(BaseModel):
    nation: Nation | None
    postcode_district: str | None
    currency: str
    period_mode: Literal["calendar_month", "pay_cycle"]
    period_anchor_person_id: str | None
    version: int


class HouseholdService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self) -> Household:
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO household (id, created_at, updated_at) VALUES (1, ?, ?)", [now, now]
            )
            row = conn.execute("SELECT * FROM household WHERE id = 1").fetchone()
        return Household(**{k: row[k] for k in Household.model_fields})

    def update(self, changes: HouseholdPatch, expected_version: int) -> Household:
        self.get()
        data = changes.model_dump(exclude_unset=True)
        if data.get("postcode_district") is not None:
            data["postcode_district"] = normalise_district(data["postcode_district"])
        if data.get("period_anchor_person_id") is not None:
            self.get_person(data["period_anchor_person_id"])
        if data:
            with self.db.transaction() as conn:
                update_versioned(conn, "household", "id", 1, expected_version, data, now=to_iso(utcnow()))
        return self.get()

    def list_people(self, include_retired: bool = False) -> list[Person]:
        sql = "SELECT * FROM person" + ("" if include_retired else " WHERE status = 'active'") + " ORDER BY created_at, rowid"
        with self.db.connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [Person(**{k: r[k] for k in Person.model_fields}) for r in rows]

    def get_person(self, person_id: str) -> Person:
        with self.db.connection() as conn:
            r = conn.execute("SELECT * FROM person WHERE id = ?", [person_id]).fetchone()
        if r is None:
            raise NotFound("person", person_id)
        return Person(**{k: r[k] for k in Person.model_fields})

    def create_person(self, data: PersonIn) -> Person:
        person_id = "p_" + secrets.token_hex(4)
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO person (id, display_name, role, birth_year, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                [person_id, data.display_name, data.role, data.birth_year, now, now],
            )
        return self.get_person(person_id)

    def update_person(self, person_id: str, changes: PersonPatch, expected_version: int) -> Person:
        data = changes.model_dump(exclude_unset=True)
        if data:
            with self.db.transaction() as conn:
                update_versioned(conn, "person", "id", person_id, expected_version, data, now=to_iso(utcnow()))
        return self.get_person(person_id)

    def retire_person(self, person_id: str, expected_version: int) -> Person:
        with self.db.transaction() as conn:
            update_versioned(conn, "person", "id", person_id, expected_version, {"status": "retired"}, now=to_iso(utcnow()))
        return self.get_person(person_id)
```

`src/tuppence/app/routes/household.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.errors import InputError
from tuppence.core.household import Household, HouseholdPatch, Person, PersonIn, PersonPatch
from tuppence.core.timeline import TimelineEntry

router = APIRouter(prefix="/api/household", tags=["household"])


class HouseholdUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class PersonUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class VersionOnly(BaseModel):
    expected_version: int


class TimelineIn(BaseModel):
    subject_type: str
    subject_id: str
    attribute: str
    value: Any
    valid_from: date


def _parse(model: type[BaseModel], data: dict[str, Any]) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        field = ".".join(str(p) for p in err["loc"])
        raise InputError(f"{field}: {err['msg']}") from None


@router.get("")
def get_household(services: Services = Depends(get_services)) -> Household:
    return services.household.get()


@router.patch("")
def update_household(body: HouseholdUpdate, services: Services = Depends(get_services)) -> Household:
    return services.household.update(_parse(HouseholdPatch, body.changes), body.expected_version)


@router.get("/people")
def list_people(include_retired: bool = False, services: Services = Depends(get_services)) -> dict[str, list[Person]]:
    return {"people": services.household.list_people(include_retired=include_retired)}


@router.post("/people", status_code=201)
def create_person(body: dict[str, Any], services: Services = Depends(get_services)) -> Person:
    return services.household.create_person(_parse(PersonIn, body))


@router.patch("/people/{person_id}")
def update_person(person_id: str, body: PersonUpdate, services: Services = Depends(get_services)) -> Person:
    return services.household.update_person(person_id, _parse(PersonPatch, body.changes), body.expected_version)


@router.post("/people/{person_id}/retire")
def retire_person(person_id: str, body: VersionOnly, services: Services = Depends(get_services)) -> Person:
    return services.household.retire_person(person_id, body.expected_version)


@router.get("/timeline")
def timeline(subject_type: str, subject_id: str, services: Services = Depends(get_services)) -> dict[str, list[TimelineEntry]]:
    return {"entries": services.timeline.history(subject_type, subject_id)}


@router.post("/timeline", status_code=201)
def add_timeline(body: TimelineIn, services: Services = Depends(get_services)) -> TimelineEntry:
    return services.timeline.set(body.subject_type, body.subject_id, body.attribute, body.value, body.valid_from)
```

Wire `household: HouseholdService` and `timeline: Timeline` into `Services`/`build_services`, and add `household.router` to `PROTECTED`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`; lint; pyright → clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add household, people and effective-dated profile timeline with API"
```

---

### Task 5: Agent manifests, presets and the config service

**Files:**
- Create: `src/tuppence/config/__init__.py`, `src/tuppence/config/models.py`, `src/tuppence/config/service.py`, `src/tuppence/config/defaults/__init__.py`, `src/tuppence/config/defaults/agents/{categoriser,transfer_matcher,commitments,backlog_sweep,researcher,question_planner,purpose_analyst,life_events,learner,linter,skill_runner,report_writer,coach}.toml`, `src/tuppence/config/defaults/presets/{frugal,balanced,thorough}.toml`, `src/tuppence/core/migrations/0003_config.sql`, `src/tuppence/app/routes/config.py`
- Modify: `src/tuppence/app/services.py` (add `config: ConfigService`), `src/tuppence/app/routes/__init__.py`
- Test: `tests/config/__init__.py`, `tests/config/test_models.py`, `tests/config/test_service.py`, `tests/app/test_config_api.py`

**Interfaces:**
- Consumes: `SettingsStore` (`config.preset`), `Database`, `VersionConflict`, `DataPaths.config`.
- Produces:
  - `tuppence.config.models.Task = Literal["read","categorise","review","research","coach","report","vision"]`
  - `ModelRef(connection_id: str, model_id: str)`, `Budgets(max_llm_calls: int ≥0, max_tokens: int ≥0, max_gbp: float ≥0, max_seconds: float >0)`, `QuestionLimits(max_open: int ≥0, cooldown_days: int ≥0)`
  - `AgentManifest` (extra forbidden): `name`, `description`, `enabled: bool = True`, `task: Task | None = None`, `model_chain: list[ModelRef] = []`, `budgets: Budgets`, `thresholds: dict[str, float] = {}`, `limits: dict[str, int] = {}`, `questions: QuestionLimits | None = None`, `triggers: list[str] = []`, `tools: list[str] = []`, `tone: Literal["plain","detailed"] = "plain"`, `prompt_template: str | None = None`
  - `tuppence.config.service.deep_merge(base: dict, override: dict) -> dict` (dicts merge recursively; lists and scalars replace)
  - `AgentView` (pydantic): `manifest: AgentManifest`, `overridden: list[str]` (dotted paths set in the UI layer), `user_file_error: str | None`, `version: int` (UI-override row version; 0 = none)
  - `ConfigService(db, settings_store, user_dir: Path)` with `agent_names() -> list[str]`, `get(name) -> AgentManifest`, `view(name) -> AgentView`, `views() -> list[AgentView]`, `set_override(name, changes: dict, expected_version: int) -> AgentView`, `reset_field(name, path: str, expected_version: int) -> AgentView`, `presets() -> list[str]`, `export() -> dict` (`{"preset": str, "agents": {name: manifest-dict}}`); unknown agent → `NotFound`; invalid merged result → `InputError` naming the field
  - Table `agent_override(name TEXT PK, value TEXT JSON, version INTEGER, updated_at TEXT)`
  - Routes: `GET /api/config/agents` → `{"agents": [AgentView…], "preset": {"value": str, "version": int}}`; `GET /api/config/agents/{name}` → AgentView; `PATCH /api/config/agents/{name}` `{"changes": {...partial...}, "expected_version": n}`; `POST /api/config/agents/{name}/reset` `{"path": "budgets.max_gbp", "expected_version": n}`; `GET /api/config/presets` → `{"presets": [...]}`; `GET /api/config/export` → JSON with `Content-Disposition: attachment; filename="tuppence-agents.json"`. (The preset itself changes through `PATCH /api/settings/config.preset`.)

- [ ] **Step 1: Write the default manifests**

Each file is a full manifest. Values come from spec §8.2 and §10.1.

`agents/categoriser.toml`:
```toml
name = "categoriser"
description = "Works out what each transaction is: rules and memory first, then the AI for the rest."
task = "categorise"
triggers = ["statement_imported", "rule_changed", "feedback"]
[budgets]
max_llm_calls = 60
max_tokens = 400000
max_gbp = 0.50
max_seconds = 900
[thresholds]
review_below = 0.8
```

`agents/transfer_matcher.toml`:
```toml
name = "transfer_matcher"
description = "Pairs money moving between your own accounts and card repayments. Code only, no AI."
triggers = ["statement_imported"]
[budgets]
max_llm_calls = 0
max_tokens = 0
max_gbp = 0.0
max_seconds = 120
[limits]
max_days_apart = 3
```

`agents/commitments.toml`:
```toml
name = "commitments"
description = "Finds bills, subscriptions and instalments, their timing and price changes."
task = "categorise"
triggers = ["statement_imported"]
[budgets]
max_llm_calls = 20
max_tokens = 100000
max_gbp = 0.15
max_seconds = 300
[limits]
min_occurrences = 2
```

`agents/backlog_sweep.toml`:
```toml
name = "backlog_sweep"
description = "Re-queues transactions Tuppence isn't sure about so they're looked at again."
triggers = ["statement_imported", "knowledge_changed", "daily"]
[budgets]
max_llm_calls = 0
max_tokens = 0
max_gbp = 0.0
max_seconds = 60
[thresholds]
revisit_below_confidence = 0.7
[limits]
max_items_per_run = 200
```

`agents/researcher.toml`:
```toml
name = "researcher"
description = "Identifies unknown merchants using the merchant pack, Companies House and (if allowed) the web."
task = "research"
triggers = ["backlog"]
tools = ["merchant_pack_lookup", "companies_house_lookup", "web_search", "fetch_page"]
[budgets]
max_llm_calls = 60
max_tokens = 300000
max_gbp = 0.50
max_seconds = 600
[limits]
max_merchants_per_run = 20
max_tool_calls_per_merchant = 5
max_page_fetches_per_merchant = 2
```

`agents/question_planner.toml`:
```toml
name = "question_planner"
description = "Chooses the few questions worth asking you, biggest pound impact first."
task = "coach"
triggers = ["analysis_complete"]
[budgets]
max_llm_calls = 10
max_tokens = 40000
max_gbp = 0.05
max_seconds = 120
[thresholds]
material_change = 0.10
[questions]
max_open = 5
cooldown_days = 180
```

`agents/purpose_analyst.toml`:
```toml
name = "purpose_analyst"
description = "Asks 'why?' about your biggest outgoings so advice fits your life."
task = "coach"
triggers = ["analysis_complete", "answer"]
[budgets]
max_llm_calls = 10
max_tokens = 60000
max_gbp = 0.08
max_seconds = 180
[limits]
max_depth = 5
max_open_conversations = 3
```

`agents/life_events.toml`:
```toml
name = "life_events"
description = "Notices changes such as a new job, a new baby or moving home, and asks you to confirm."
task = "coach"
triggers = ["statement_imported", "daily"]
[budgets]
max_llm_calls = 5
max_tokens = 30000
max_gbp = 0.04
max_seconds = 120
```

`agents/learner.toml`:
```toml
name = "learner"
description = "Turns your corrections into rules and offers to apply them to similar past transactions."
task = "review"
triggers = ["feedback"]
[budgets]
max_llm_calls = 5
max_tokens = 40000
max_gbp = 0.05
max_seconds = 120
```

`agents/linter.toml`:
```toml
name = "linter"
description = "Checks past records for inconsistencies and fixes what your rules cover."
task = "review"
triggers = ["knowledge_changed", "daily"]
[budgets]
max_llm_calls = 20
max_tokens = 150000
max_gbp = 0.15
max_seconds = 600
[limits]
max_items_per_run = 500
```

`agents/skill_runner.toml`:
```toml
name = "skill_runner"
description = "Runs the advisor skills (debt, bills, tax, benefits, goals…) when their data changes."
task = "report"
triggers = ["analysis_complete", "life_event", "daily"]
[budgets]
max_llm_calls = 20
max_tokens = 120000
max_gbp = 0.15
max_seconds = 600
```

`agents/report_writer.toml`:
```toml
name = "report_writer"
description = "Writes the commentary on your monthly report. Every number is checked against the facts."
task = "report"
triggers = ["analysis_complete", "monthly"]
[budgets]
max_llm_calls = 8
max_tokens = 60000
max_gbp = 0.10
max_seconds = 300
[thresholds]
money_tolerance_gbp = 1.0
percent_tolerance = 0.5
```

`agents/coach.toml`:
```toml
name = "coach"
description = "Chats with you about your money, using the other agents and skills as tools."
task = "coach"
tone = "plain"
[budgets]
max_llm_calls = 9
max_tokens = 200000
max_gbp = 0.25
max_seconds = 60
[limits]
max_tool_calls_per_turn = 8
local_max_seconds = 180
max_rows_per_tool_result = 50
recent_turns_kept = 8
```

`presets/balanced.toml`: `# Default behaviour: no overrides.` (a comment only — an empty table set).

`presets/frugal.toml`:
```toml
# Least AI use: good for small local models and tight budgets.
[agents.researcher]
enabled = false
[agents.categoriser.budgets]
max_llm_calls = 30
max_gbp = 0.20
[agents.coach.limits]
max_tool_calls_per_turn = 4
[agents.linter.budgets]
max_llm_calls = 5
```

`presets/thorough.toml`:
```toml
# More research and review: best with a capable cloud model.
[agents.researcher.limits]
max_merchants_per_run = 40
[agents.researcher.budgets]
max_gbp = 1.00
[agents.categoriser.thresholds]
review_below = 0.9
[agents.linter.budgets]
max_llm_calls = 40
```

`0003_config.sql`:

```sql
CREATE TABLE agent_override (
  name TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
```

- [ ] **Step 2: Write the failing tests**

`tests/config/__init__.py`: empty.

`tests/config/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from tuppence.config.models import AgentManifest


def test_manifest_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(
            {"name": "x", "description": "d", "budgets": {"max_llm_calls": 1, "max_tokens": 1, "max_gbp": 0, "max_seconds": 1}, "colour": "red"}
        )


def test_budgets_must_be_non_negative():
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(
            {"name": "x", "description": "d", "budgets": {"max_llm_calls": -1, "max_tokens": 1, "max_gbp": 0, "max_seconds": 1}}
        )
```

`tests/config/test_service.py`:

```python
import pytest

from tuppence.config.service import ConfigService, deep_merge
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.migrate import migrate
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.settings_store import SettingsStore

EXPECTED = {
    "categoriser", "transfer_matcher", "commitments", "backlog_sweep", "researcher", "question_planner",
    "purpose_analyst", "life_events", "learner", "linter", "skill_runner", "report_writer", "coach",
}


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    store = SettingsStore(db)
    user_dir = tmp_path / "config"
    (user_dir / "agents").mkdir(parents=True)
    return ConfigService(db, store, user_dir), store, user_dir


def test_deep_merge():
    assert deep_merge({"a": {"b": 1, "c": 2}, "l": [1]}, {"a": {"b": 9}, "l": [2]}) == {"a": {"b": 9, "c": 2}, "l": [2]}


def test_all_default_agents_load_with_spec_values(env):
    cfg, _, _ = env
    assert set(cfg.agent_names()) == EXPECTED
    r = cfg.get("researcher")
    assert (r.limits["max_merchants_per_run"], r.limits["max_tool_calls_per_merchant"], r.limits["max_page_fetches_per_merchant"]) == (20, 5, 2)
    p = cfg.get("purpose_analyst")
    assert (p.limits["max_depth"], p.limits["max_open_conversations"]) == (5, 3)
    q = cfg.get("question_planner")
    assert q.questions is not None and q.questions.max_open == 5
    c = cfg.get("coach")
    assert c.limits["max_tool_calls_per_turn"] == 8 and c.budgets.max_seconds == 60 and c.limits["local_max_seconds"] == 180
    assert cfg.get("backlog_sweep").thresholds["revisit_below_confidence"] == 0.7


def test_preset_layer(env):
    cfg, store, _ = env
    store.set("config.preset", "frugal", expected_version=0)
    assert cfg.get("researcher").enabled is False
    assert cfg.get("coach").limits["max_tool_calls_per_turn"] == 4
    assert cfg.get("coach").limits["max_rows_per_tool_result"] == 50  # untouched keys survive


def test_user_file_layer_and_ui_layer_precedence(env):
    cfg, _, user_dir = env
    (user_dir / "agents" / "researcher.toml").write_text("[limits]\nmax_merchants_per_run = 30\n", encoding="utf-8")
    assert cfg.get("researcher").limits["max_merchants_per_run"] == 30
    view = cfg.set_override("researcher", {"limits": {"max_merchants_per_run": 12}}, expected_version=0)
    assert view.manifest.limits["max_merchants_per_run"] == 12
    assert view.overridden == ["limits.max_merchants_per_run"] and view.version == 1


def test_bad_user_file_is_reported_and_ignored(env):
    cfg, _, user_dir = env
    (user_dir / "agents" / "coach.toml").write_text("tone = 'shouty'\n", encoding="utf-8")
    view = cfg.view("coach")
    assert view.manifest.tone == "plain"
    assert view.user_file_error and "coach.toml" in view.user_file_error
    (user_dir / "agents" / "learner.toml").write_text("this is [not toml", encoding="utf-8")
    assert cfg.view("learner").user_file_error


def test_invalid_override_rejected(env):
    cfg, _, _ = env
    with pytest.raises(InputError) as exc:
        cfg.set_override("coach", {"budgets": {"max_gbp": -5}}, expected_version=0)
    assert "budgets.max_gbp" in str(exc.value)
    with pytest.raises(InputError):
        cfg.set_override("coach", {"name": "renamed"}, expected_version=0)


def test_reset_field_and_versions(env):
    cfg, _, _ = env
    cfg.set_override("coach", {"tone": "detailed", "budgets": {"max_gbp": 0.5}}, expected_version=0)
    with pytest.raises(VersionConflict):
        cfg.set_override("coach", {"tone": "plain"}, expected_version=0)
    v = cfg.reset_field("coach", "budgets.max_gbp", expected_version=1)
    assert v.manifest.budgets.max_gbp == 0.25 and v.overridden == ["tone"] and v.version == 2


def test_unknown_agent(env):
    cfg, _, _ = env
    with pytest.raises(NotFound):
        cfg.get("nope")


def test_export(env):
    cfg, _, _ = env
    out = cfg.export()
    assert out["preset"] == "balanced" and set(out["agents"]) == EXPECTED
```

`tests/app/test_config_api.py`:

```python
def test_agents_api(client):
    r = client.get("/api/config/agents")
    assert r.status_code == 200
    body = r.json()
    assert body["preset"] == {"value": "balanced", "version": 0}
    names = [a["manifest"]["name"] for a in body["agents"]]
    assert "researcher" in names

    upd = client.patch("/api/config/agents/researcher", json={"changes": {"limits": {"max_merchants_per_run": 10}}, "expected_version": 0})
    assert upd.status_code == 200 and upd.json()["manifest"]["limits"]["max_merchants_per_run"] == 10

    bad = client.patch("/api/config/agents/researcher", json={"changes": {"budgets": {"max_gbp": -1}}, "expected_version": 1})
    assert bad.status_code == 422

    reset = client.post("/api/config/agents/researcher/reset", json={"path": "limits.max_merchants_per_run", "expected_version": 1})
    assert reset.status_code == 200 and reset.json()["manifest"]["limits"]["max_merchants_per_run"] == 20

    assert client.get("/api/config/presets").json() == {"presets": ["balanced", "frugal", "thorough"]}
    exp = client.get("/api/config/export")
    assert exp.status_code == 200 and "attachment" in exp.headers["content-disposition"]


def test_unknown_agent_404(client):
    assert client.get("/api/config/agents/nope").status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/config tests/app/test_config_api.py -q` → FAIL.

- [ ] **Step 4: Implement**

`src/tuppence/config/models.py`:

```python
"""Agent manifests: every knob an open-source user may want to turn (spec §11.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Task = Literal["read", "categorise", "review", "research", "coach", "report", "vision"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRef(_Strict):
    connection_id: str
    model_id: str


class Budgets(_Strict):
    max_llm_calls: int = Field(ge=0)
    max_tokens: int = Field(ge=0)
    max_gbp: float = Field(ge=0)
    max_seconds: float = Field(gt=0)


class QuestionLimits(_Strict):
    max_open: int = Field(ge=0)
    cooldown_days: int = Field(ge=0)


class AgentManifest(_Strict):
    name: str
    description: str
    enabled: bool = True
    task: Task | None = None
    model_chain: list[ModelRef] = Field(default_factory=list)
    budgets: Budgets
    thresholds: dict[str, float] = Field(default_factory=dict)
    limits: dict[str, int] = Field(default_factory=dict)
    questions: QuestionLimits | None = None
    triggers: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    tone: Literal["plain", "detailed"] = "plain"
    prompt_template: str | None = None
```

`src/tuppence/config/service.py`:

```python
"""Merge manifest layers: defaults < preset < user TOML < UI overrides."""

from __future__ import annotations

import copy
import json
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from tuppence.config.models import AgentManifest
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.settings_store import SettingsStore

DEFAULTS = "tuppence.config.defaults"
LOCKED_FIELDS = {"name"}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _paths(data: dict[str, Any], prefix: str = "") -> list[str]:
    found: list[str] = []
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            found.extend(_paths(value, path + "."))
        else:
            found.append(path)
    return sorted(found)


def _remove_path(data: dict[str, Any], path: str) -> dict[str, Any]:
    """Remove one dotted key and any parent tables it leaves empty."""
    out = copy.deepcopy(data)

    def remove(node: Any, parts: list[str]) -> None:
        if not isinstance(node, dict):
            return
        if len(parts) == 1:
            node.pop(parts[0], None)
            return
        child = node.get(parts[0])
        remove(child, parts[1:])
        if isinstance(child, dict) and not child:
            node.pop(parts[0], None)

    remove(out, path.split("."))
    return out


def _load_toml_resource(*parts: str) -> dict[str, Any]:
    node = resources.files(DEFAULTS)
    for part in parts:
        node = node.joinpath(part)
    return tomllib.loads(node.read_text(encoding="utf-8"))


class AgentView(BaseModel):
    manifest: AgentManifest
    overridden: list[str]
    user_file_error: str | None
    version: int


class ConfigService:
    def __init__(self, db: Database, settings: SettingsStore, user_dir: Path) -> None:
        self.db = db
        self.settings = settings
        self.user_dir = user_dir

    def agent_names(self) -> list[str]:
        folder = resources.files(DEFAULTS).joinpath("agents")
        return sorted(p.name[:-5] for p in folder.iterdir() if p.name.endswith(".toml"))

    def presets(self) -> list[str]:
        folder = resources.files(DEFAULTS).joinpath("presets")
        return sorted(p.name[:-5] for p in folder.iterdir() if p.name.endswith(".toml"))

    def _defaults(self, name: str) -> dict[str, Any]:
        if name not in self.agent_names():
            raise NotFound("agent", name)
        return _load_toml_resource("agents", f"{name}.toml")

    def _preset_layer(self, name: str) -> dict[str, Any]:
        preset = self.settings.get("config.preset")
        return _load_toml_resource("presets", f"{preset}.toml").get("agents", {}).get(name, {})

    def _user_layer(self, name: str, base: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        path = self.user_dir / "agents" / f"{name}.toml"
        if not path.is_file():
            return {}, None
        try:
            layer = tomllib.loads(path.read_text(encoding="utf-8"))
            AgentManifest.model_validate(deep_merge(base, layer))
        except (tomllib.TOMLDecodeError, ValidationError, UnicodeDecodeError) as exc:
            return {}, f"{path.name}: {exc}".splitlines()[0][:300]
        return layer, None

    def _ui_layer(self, name: str) -> tuple[dict[str, Any], int]:
        with self.db.connection() as conn:
            row = conn.execute("SELECT value, version FROM agent_override WHERE name = ?", [name]).fetchone()
        return ({}, 0) if row is None else (json.loads(row["value"]), int(row["version"]))

    def view(self, name: str) -> AgentView:
        base = deep_merge(self._defaults(name), self._preset_layer(name))
        user, error = self._user_layer(name, base)
        merged = deep_merge(base, user)
        ui, version = self._ui_layer(name)
        manifest = AgentManifest.model_validate(deep_merge(merged, ui))
        return AgentView(manifest=manifest, overridden=_paths(ui), user_file_error=error, version=version)

    def views(self) -> list[AgentView]:
        return [self.view(n) for n in self.agent_names()]

    def get(self, name: str) -> AgentManifest:
        return self.view(name).manifest

    def _write_ui(self, name: str, layer: dict[str, Any], expected_version: int) -> AgentView:
        if LOCKED_FIELDS & set(layer):
            raise InputError("An agent's name can't be changed.")
        base = deep_merge(self._defaults(name), self._preset_layer(name))
        user, _ = self._user_layer(name, base)
        try:
            AgentManifest.model_validate(deep_merge(deep_merge(base, user), layer))
        except ValidationError as exc:
            err = exc.errors()[0]
            field = ".".join(str(p) for p in err["loc"])
            raise InputError(f"{field}: {err['msg']}") from None
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            row = conn.execute("SELECT version FROM agent_override WHERE name = ?", [name]).fetchone()
            current = 0 if row is None else int(row["version"])
            if current != expected_version:
                raise VersionConflict("agent_override", name, expected_version, current)
            payload = json.dumps(layer)
            if row is None:
                conn.execute(
                    "INSERT INTO agent_override (name, value, version, updated_at) VALUES (?, ?, 1, ?)", [name, payload, now]
                )
            else:
                conn.execute(
                    "UPDATE agent_override SET value = ?, version = version + 1, updated_at = ? WHERE name = ?",
                    [payload, now, name],
                )
        return self.view(name)

    def set_override(self, name: str, changes: dict[str, Any], expected_version: int) -> AgentView:
        current, _ = self._ui_layer(name)
        return self._write_ui(name, deep_merge(current, changes), expected_version)

    def reset_field(self, name: str, path: str, expected_version: int) -> AgentView:
        current, _ = self._ui_layer(name)
        return self._write_ui(name, _remove_path(current, path), expected_version)

    def export(self) -> dict[str, Any]:
        return {
            "preset": self.settings.get("config.preset"),
            "agents": {v.manifest.name: v.manifest.model_dump(mode="json") for v in self.views()},
        }
```

> Note: `_defaults` raising `NotFound` before anything else gives a 404 for unknown agents in every route.

`src/tuppence/app/routes/config.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.config.service import AgentView

router = APIRouter(prefix="/api/config", tags=["config"])


class OverrideIn(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class ResetIn(BaseModel):
    path: str
    expected_version: int


@router.get("/agents")
def list_agents(services: Services = Depends(get_services)) -> dict[str, Any]:
    preset = services.settings.entry("config.preset")
    return {"agents": services.config.views(), "preset": {"value": preset.value, "version": preset.version}}


@router.get("/agents/{name}")
def get_agent(name: str, services: Services = Depends(get_services)) -> AgentView:
    return services.config.view(name)


@router.patch("/agents/{name}")
def override_agent(name: str, body: OverrideIn, services: Services = Depends(get_services)) -> AgentView:
    return services.config.set_override(name, body.changes, body.expected_version)


@router.post("/agents/{name}/reset")
def reset_agent_field(name: str, body: ResetIn, services: Services = Depends(get_services)) -> AgentView:
    return services.config.reset_field(name, body.path, body.expected_version)


@router.get("/presets")
def presets(services: Services = Depends(get_services)) -> dict[str, list[str]]:
    return {"presets": services.config.presets()}


@router.get("/export")
def export(services: Services = Depends(get_services)) -> JSONResponse:
    return JSONResponse(
        services.config.export(), headers={"Content-Disposition": 'attachment; filename="tuppence-agents.json"'}
    )
```

Wire `config=ConfigService(db, settings_store, paths.config)` into `Services`; add `config.router` to `PROTECTED`. Ensure `src/tuppence/config/defaults/__init__.py` exists (empty) so `importlib.resources` resolves the package.

- [ ] **Step 5: Run tests, then commit**

Run: `uv run pytest -q`; lint; pyright → clean.

```bash
git add -A
git commit -m "Add configurable agent manifests with presets, user files and UI overrides"
```

---

### Task 6: Job queue, worker and daily backups

**Files:**
- Create: `src/tuppence/core/migrations/0004_jobs.sql`, `src/tuppence/core/jobs.py`, `src/tuppence/app/routes/jobs.py`
- Modify: `src/tuppence/app/services.py` (queue, worker, periodic; `start()`/`stop()`), `src/tuppence/app/factory.py` (lifespan), `src/tuppence/app/routes/__init__.py`
- Test: `tests/core/test_jobs.py`, `tests/app/test_lifespan.py`

**Interfaces:**
- Consumes: `Database`, `transaction`, `utcnow/to_iso/from_iso`, `daily_backup`.
- Produces:
  - `Job` (frozen dataclass): `id: int`, `kind: str`, `scope_key: str`, `payload: dict`, `status: str`, `attempts: int`, `max_attempts: int`, `run_after: datetime`, `error: str | None`, `result: dict | None`
  - `JobQueue(db, *, clock=utcnow)`: `enqueue(kind, *, scope_key="", payload=None, debounce_s=0.0, max_attempts=3, merge=None) -> int`; `claim(*, exclusive_kinds: frozenset[str] = frozenset()) -> Job | None`; `complete(job_id, result=None)`; `fail(job_id, error, *, retry=True)`; `defer(job_id, reason, *, delay_s)`; `recover_running() -> int`; `get(job_id) -> Job`; `list(*, status=None, limit=50) -> list[Job]`
  - Retry backoff: `5 * 4 ** (attempts - 1)` seconds while `attempts < max_attempts`, else status `failed`
  - `DeferJob(reason: str, delay_s: float)` exception handlers raise to postpone a job
  - `Worker(queue, handlers: dict[str, Callable[[Job], dict | None]], *, exclusive_kinds=frozenset(), threads=2, poll_interval=0.5)`: `run_once() -> bool`, `start()`, `stop(timeout=5.0)`
  - `Periodic(queue, kind, *, scope_key, interval_s)`: `start()`, `stop()` — enqueues immediately, then every `interval_s`
  - Job kind `maintenance.daily_backup` (handler → `daily_backup(paths.db, paths.backups, today)`)
  - `Services.queue`, `Services.worker`, `Services.start()`, `Services.stop()`; FastAPI lifespan calls them; `EXCLUSIVE_KINDS = frozenset({"analysis"})`
  - Route: `GET /api/jobs?status=` → `{"jobs": [...]}` (read-only)

- [ ] **Step 1: Write the failing tests**

`tests/core/test_jobs.py`:

```python
from datetime import timedelta

import pytest

from tuppence.core.clock import utcnow
from tuppence.core.db import Database
from tuppence.core.jobs import DeferJob, JobQueue, Worker
from tuppence.core.migrate import migrate


class Clock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    clock = Clock()
    return JobQueue(db, clock=clock), clock


def test_enqueue_coalesces_same_scope(env):
    q, _ = env
    a = q.enqueue("analysis", scope_key="acct-1", payload={"n": 1})
    b = q.enqueue("analysis", scope_key="acct-1", payload={"n": 2})
    assert a == b and q.get(a).payload == {"n": 2}
    c = q.enqueue("analysis", scope_key="acct-2")
    assert c != a


def test_merge_function_combines_payloads(env):
    q, _ = env
    merge = lambda old, new: {"merchants": sorted(set(old["merchants"]) | set(new["merchants"]))}  # noqa: E731
    jid = q.enqueue("rereview", payload={"merchants": ["a"]}, merge=merge)
    q.enqueue("rereview", payload={"merchants": ["b"]}, merge=merge)
    assert q.get(jid).payload == {"merchants": ["a", "b"]}


def test_debounce_delays_claim(env):
    q, clock = env
    q.enqueue("analysis", debounce_s=30)
    assert q.claim() is None
    clock.advance(31)
    assert q.claim() is not None


def test_exclusive_kind_runs_one_at_a_time(env):
    q, _ = env
    q.enqueue("analysis", scope_key="a")
    q.enqueue("analysis", scope_key="b")
    q.enqueue("other")
    ex = frozenset({"analysis"})
    first = q.claim(exclusive_kinds=ex)
    second = q.claim(exclusive_kinds=ex)
    assert first.kind == "analysis" and second.kind == "other"
    assert q.claim(exclusive_kinds=ex) is None
    q.complete(first.id)
    assert q.claim(exclusive_kinds=ex).scope_key == "b"


def test_failure_retries_with_backoff_then_fails(env):
    q, clock = env
    jid = q.enqueue("x", max_attempts=2)
    q.fail(q.claim().id, "boom")
    job = q.get(jid)
    assert job.status == "queued" and job.attempts == 1 and job.error == "boom"
    clock.advance(6)
    q.fail(q.claim().id, "boom again")
    assert q.get(jid).status == "failed"


def test_running_job_requeued_after_restart(env):
    q, _ = env
    jid = q.enqueue("analysis")
    q.claim()
    assert q.get(jid).status == "running"
    assert q.recover_running() == 1
    assert q.get(jid).status == "queued"


def test_requeue_does_not_clash_with_newer_queued_job(env):
    q, _ = env
    first = q.enqueue("analysis", scope_key="s")
    q.claim()
    second = q.enqueue("analysis", scope_key="s")
    assert second != first
    q.recover_running()
    assert q.get(first).status == "cancelled" and q.get(second).status == "queued"


def test_worker_runs_handlers_and_records_results(env):
    q, clock = env
    seen = []
    ok = q.enqueue("echo", payload={"v": 1})
    bad = q.enqueue("explode", max_attempts=1)
    later = q.enqueue("later")
    unknown = q.enqueue("mystery", max_attempts=1)

    def defer(job):
        raise DeferJob("waiting for data", delay_s=60)

    w = Worker(q, {"echo": lambda j: seen.append(j.payload) or {"ok": True}, "explode": lambda j: 1 / 0, "later": defer})
    while w.run_once():
        pass
    assert seen == [{"v": 1}] and q.get(ok).status == "done" and q.get(ok).result == {"ok": True}
    assert q.get(bad).status == "failed" and "division by zero" in q.get(bad).error
    assert q.get(later).status == "queued" and q.get(later).error == "waiting for data"
    assert q.get(unknown).status == "failed" and "No handler" in q.get(unknown).error


def test_worker_thread_start_stop(env):
    import time

    q, _ = env
    done = []
    w = Worker(q, {"echo": lambda j: done.append(j.id)}, poll_interval=0.05)
    w.start()
    jid = q.enqueue("echo")
    deadline = time.monotonic() + 5
    while not done and time.monotonic() < deadline:
        time.sleep(0.05)
    w.stop()
    assert done == [jid]
```

`tests/app/test_lifespan.py`:

```python
import time

from fastapi.testclient import TestClient


def test_daily_backup_job_runs_on_startup(tmp_path, make_app):
    app = make_app("server")
    with TestClient(app):
        backups = tmp_path / "data" / "backups"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(backups.glob("daily-*.db")):
            time.sleep(0.1)
        assert any(backups.glob("daily-*.db"))


def test_jobs_endpoint_lists_jobs(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200 and "jobs" in r.json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_jobs.py tests/app/test_lifespan.py -q` → FAIL.

- [ ] **Step 3: Implement**

`0004_jobs.sql`:

```sql
CREATE TABLE job (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  scope_key TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  run_after TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  result TEXT
);
CREATE UNIQUE INDEX ux_job_queued_scope ON job (kind, scope_key) WHERE status = 'queued';
CREATE INDEX ix_job_ready ON job (status, run_after);
```

`src/tuppence/core/jobs.py`:

```python
"""A small SQLite-backed job queue with coalescing (spec §8.3, §10.1)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from tuppence.core.clock import from_iso, to_iso, utcnow
from tuppence.core.db import Database

log = logging.getLogger("tuppence.jobs")
Handler = Callable[["Job"], "dict[str, Any] | None"]
Merge = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    scope_key: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    run_after: datetime
    error: str | None
    result: dict[str, Any] | None


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"], kind=row["kind"], scope_key=row["scope_key"], payload=json.loads(row["payload"]),
        status=row["status"], attempts=row["attempts"], max_attempts=row["max_attempts"],
        run_after=from_iso(row["run_after"]), error=row["error"],
        result=json.loads(row["result"]) if row["result"] else None,
    )


class DeferJob(Exception):
    def __init__(self, reason: str, delay_s: float) -> None:
        super().__init__(reason)
        self.reason, self.delay_s = reason, delay_s


class JobQueue:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.clock = clock

    def enqueue(
        self, kind: str, *, scope_key: str = "", payload: dict[str, Any] | None = None,
        debounce_s: float = 0.0, max_attempts: int = 3, merge: Merge | None = None,
    ) -> int:
        payload = payload or {}
        now = self.clock()
        run_after = to_iso(now + timedelta(seconds=debounce_s))
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id, payload, run_after FROM job WHERE kind = ? AND scope_key = ? AND status = 'queued'",
                [kind, scope_key],
            ).fetchone()
            if row is not None:
                merged = merge(json.loads(row["payload"]), payload) if merge else payload
                conn.execute(
                    "UPDATE job SET payload = ?, run_after = max(run_after, ?) WHERE id = ?",
                    [json.dumps(merged), run_after, row["id"]],
                )
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO job (kind, scope_key, payload, status, max_attempts, run_after, created_at)"
                " VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                [kind, scope_key, json.dumps(payload), max_attempts, run_after, to_iso(now)],
            )
            return int(cur.lastrowid or 0)

    def claim(self, *, exclusive_kinds: frozenset[str] = frozenset()) -> Job | None:
        now = to_iso(self.clock())
        clauses = ["j.status = 'queued'", "j.run_after <= ?"]
        params: list[Any] = [now]
        if exclusive_kinds:
            marks = ",".join("?" for _ in exclusive_kinds)
            clauses.append(
                f"NOT (j.kind IN ({marks}) AND EXISTS"
                " (SELECT 1 FROM job r WHERE r.status = 'running' AND r.kind = j.kind))"
            )
            params.extend(sorted(exclusive_kinds))
        sql = (
            "UPDATE job SET status = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ("
            f"SELECT j.id FROM job j WHERE {' AND '.join(clauses)} ORDER BY j.run_after, j.id LIMIT 1) RETURNING *"
        )  # noqa: S608 - only '?' placeholders are interpolated
        with self.db.transaction() as conn:
            row = conn.execute(sql, [now, *params]).fetchone()
        return None if row is None else _job(row)

    def complete(self, job_id: int, result: dict[str, Any] | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE job SET status = 'done', finished_at = ?, result = ?, error = NULL WHERE id = ?",
                [to_iso(self.clock()), json.dumps(result) if result is not None else None, job_id],
            )

    def fail(self, job_id: int, error: str, *, retry: bool = True) -> None:
        now = self.clock()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT attempts, max_attempts FROM job WHERE id = ?", [job_id]).fetchone()
            if retry and row["attempts"] < row["max_attempts"]:
                delay = 5 * 4 ** (row["attempts"] - 1)
                self._requeue(conn, job_id, to_iso(now + timedelta(seconds=delay)), error)
            else:
                conn.execute(
                    "UPDATE job SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                    [to_iso(now), error[:2000], job_id],
                )

    def defer(self, job_id: int, reason: str, *, delay_s: float) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE job SET attempts = max(attempts - 1, 0) WHERE id = ?", [job_id])
            self._requeue(conn, job_id, to_iso(self.clock() + timedelta(seconds=delay_s)), reason)

    def _requeue(self, conn: sqlite3.Connection, job_id: int, run_after: str, note: str) -> None:
        try:
            conn.execute(
                "UPDATE job SET status = 'queued', run_after = ?, error = ? WHERE id = ?", [run_after, note[:2000], job_id]
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE job SET status = 'cancelled', finished_at = ?, error = ? WHERE id = ?",
                [to_iso(self.clock()), "Superseded by a newer queued job", job_id],
            )

    def recover_running(self) -> int:
        with self.db.transaction() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM job WHERE status = 'running'")]
            for job_id in ids:
                self._requeue(conn, job_id, to_iso(self.clock()), "Restarted after an interruption")
        return len(ids)

    def get(self, job_id: int) -> Job:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM job WHERE id = ?", [job_id]).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job(row)

    def list(self, *, status: str | None = None, limit: int = 50) -> list[Job]:
        sql, params = "SELECT * FROM job", []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        with self.db.connection() as conn:
            rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", [*params, limit]).fetchall()
        return [_job(r) for r in rows]
```

Worker and Periodic (same file):

```python
class Worker:
    def __init__(
        self, queue: JobQueue, handlers: dict[str, Handler], *, exclusive_kinds: frozenset[str] = frozenset(),
        threads: int = 2, poll_interval: float = 0.5,
    ) -> None:
        self.queue, self.handlers = queue, handlers
        self.exclusive_kinds, self.threads, self.poll_interval = exclusive_kinds, threads, poll_interval
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def run_once(self) -> bool:
        job = self.queue.claim(exclusive_kinds=self.exclusive_kinds)
        if job is None:
            return False
        handler = self.handlers.get(job.kind)
        if handler is None:
            self.queue.fail(job.id, f"No handler for job kind '{job.kind}'", retry=False)
            return True
        try:
            result = handler(job)
        except DeferJob as exc:
            self.queue.defer(job.id, exc.reason, delay_s=exc.delay_s)
        except Exception as exc:  # noqa: BLE001 - a job failure must never kill the worker
            log.exception("job %s (%s) failed", job.id, job.kind)
            self.queue.fail(job.id, f"{type(exc).__name__}: {exc}")
        else:
            self.queue.complete(job.id, result)
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                worked = False
            if not worked:
                self._stop.wait(self.poll_interval)

    def start(self) -> None:
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._loop, name=f"tuppence-worker-{i}", daemon=True) for i in range(self.threads)
        ]
        for t in self._threads:
            t.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)


class Periodic:
    def __init__(self, queue: JobQueue, kind: str, *, scope_key: str, interval_s: float) -> None:
        self.queue, self.kind, self.scope_key, self.interval_s = queue, kind, scope_key, interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"tuppence-periodic-{kind}", daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.queue.enqueue(self.kind, scope_key=self.scope_key)
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2.0)
```

In `services.py`: add `queue: JobQueue`, `worker: Worker`, `periodic: list[Periodic]`; `EXCLUSIVE_KINDS = frozenset({"analysis"})`; build the worker with handler `{"maintenance.daily_backup": lambda job: {"backup": str(p) if (p := daily_backup(paths.db, paths.backups, date.today())) else None}}`; `start()` calls `queue.recover_running()`, `worker.start()`, and starts `Periodic(queue, "maintenance.daily_backup", scope_key="daily", interval_s=3600)`; `stop()` stops periodic then worker.

In `factory.py`, create the app with a lifespan:

```python
from contextlib import asynccontextmanager
...
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        services.start()
        try:
            yield
        finally:
            services.stop()
```

Build `services` before constructing `FastAPI(...)` so the lifespan closure can use it, and pass `lifespan=lifespan` to `FastAPI(...)`.

`src/tuppence/app/routes/jobs.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.clock import to_iso

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(status: str | None = None, services: Services = Depends(get_services)) -> dict[str, list[dict[str, Any]]]:
    return {
        "jobs": [
            {"id": j.id, "kind": j.kind, "scope_key": j.scope_key, "status": j.status, "attempts": j.attempts,
             "run_after": to_iso(j.run_after), "error": j.error}
            for j in services.queue.list(status=status)
        ]
    }
```

Add `jobs.router` to `PROTECTED`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`; lint; pyright → clean. Also run the desktop launcher test and the CLI manual check (server starts, a `daily-*.db` appears in the data folder's `backups/`).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add coalescing job queue, worker, restart recovery and daily backups"
```

---

### Task 7: App shell UI — sign-in, navigation, Household and Agents settings

**Files:**
- Create: `web/src/lib/api.ts`, `web/src/lib/session.svelte.ts`, `web/src/lib/router.svelte.ts`, `web/src/components/Nav.svelte`, `web/src/components/Notice.svelte`, `web/src/pages/Home.svelte`, `web/src/pages/Login.svelte`, `web/src/pages/Setup.svelte`, `web/src/pages/LaunchExpired.svelte`, `web/src/pages/NotFound.svelte`, `web/src/pages/settings/Household.svelte`, `web/src/pages/settings/Agents.svelte`, `web/src/app.css` (replace)
- Modify: `web/src/App.svelte`, `web/src/App.test.ts`
- Test: `web/src/lib/api.test.ts`, `web/src/lib/router.test.ts`, `web/src/pages/Login.test.ts`, `web/src/pages/settings/Household.test.ts`, `web/src/pages/settings/Agents.test.ts`

**Interfaces:**
- Consumes (HTTP): `/health`, `/api/auth/session|setup|login|logout`, `/api/household`, `/api/household/people…`, `/api/config/agents…`, `/api/config/presets`, `/api/config/export`, `/api/settings/config.preset`.
- Produces:
  - `api.ts`: `class ApiError extends Error { status: number; detail: string; currentVersion?: number }`; `setCsrf(token: string | null)`; `api<T>(path: string, opts?: {method?: string; body?: unknown}) -> Promise<T>` — JSON in/out, `credentials: 'same-origin'`, adds `X-CSRF-Token` on unsafe methods, throws `ApiError` (detail from JSON `detail`), calls the registered `onUnauthorised()` on 401
  - `session.svelte.ts`: `session` reactive object `{ loaded, authenticated, mode, needsSetup, user, csrfToken }`; `loadSession()`; `signOut()`
  - `router.svelte.ts`: `router` reactive `{ path }`; `navigate(path: string)`; links use `<a href=… onclick={link}>`
  - Routes: `/` Home, `/settings/household`, `/settings/agents`, `/login`, `/setup`; unknown → NotFound
  - Accessible forms: every input has a `<label>`; errors in an `aria-live="polite"` region (`Notice.svelte`)

- [ ] **Step 1: Write the failing tests**

`web/src/lib/api.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, onUnauthorised, setCsrf } from './api'

afterEach(() => { vi.unstubAllGlobals(); setCsrf(null) })

function stubFetch(status: number, body: unknown) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('api', () => {
  it('sends CSRF header on unsafe methods only', async () => {
    const fn = stubFetch(200, { ok: true })
    setCsrf('tok')
    await api('/api/x', { method: 'PATCH', body: { a: 1 } })
    await api('/api/x')
    const patchHeaders = new Headers(fn.mock.calls[0][1]!.headers)
    const getHeaders = new Headers(fn.mock.calls[1][1]!.headers)
    expect(patchHeaders.get('X-CSRF-Token')).toBe('tok')
    expect(getHeaders.get('X-CSRF-Token')).toBeNull()
  })

  it('throws ApiError with detail and current version', async () => {
    stubFetch(409, { detail: 'This was changed somewhere else. Reload and try again.', current_version: 3 })
    await expect(api('/api/x', { method: 'PATCH', body: {} })).rejects.toMatchObject({
      status: 409, detail: 'This was changed somewhere else. Reload and try again.', currentVersion: 3,
    })
  })

  it('calls the unauthorised handler on 401', async () => {
    stubFetch(401, { detail: 'Sign in required.' })
    const handler = vi.fn()
    onUnauthorised(handler)
    await expect(api('/api/x')).rejects.toBeInstanceOf(ApiError)
    expect(handler).toHaveBeenCalledOnce()
  })

  it('returns undefined for 204', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })))
    await expect(api('/api/x', { method: 'POST' })).resolves.toBeUndefined()
  })
})
```

`web/src/lib/router.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { navigate, router } from './router.svelte'

describe('router', () => {
  it('tracks pushState navigation', () => {
    navigate('/settings/household')
    expect(router.path).toBe('/settings/household')
    expect(window.location.pathname).toBe('/settings/household')
  })
})
```

`web/src/pages/Login.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Login from './Login.svelte'

afterEach(() => vi.unstubAllGlobals())

it('shows the server error message on wrong password', async () => {
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify({ detail: 'Wrong username or password.' }), { status: 401 })))
  render(Login)
  await fireEvent.input(screen.getByLabelText('Username'), { target: { value: 'alex' } })
  await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'nope-nope-nope' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByText('Wrong username or password.')).toBeInTheDocument()
})
```

`web/src/pages/settings/Household.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Household from './Household.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

it('lists people and adds a new person', async () => {
  const people: unknown[] = [{ id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }]
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
    if (url.startsWith('/api/household/people') && (!init || !init.method || init.method === 'GET')) return json({ people })
    if (url === '/api/household/people' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string)
      const p = { id: 'p_2', status: 'active', version: 1, birth_year: null, ...body }
      people.push(p)
      return json(p, 201)
    }
    return json({ detail: 'unexpected' }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(Household)
  expect(await screen.findByText('Alex Example')).toBeInTheDocument()
  await fireEvent.input(screen.getByLabelText('Name'), { target: { value: 'Kid A' } })
  await fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'child' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add person' }))
  expect(await screen.findByText('Kid A')).toBeInTheDocument()
})

it('shows the friendly error for a full postcode', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/household' && init?.method === 'PATCH')
      return json({ detail: 'Just the first part of your postcode, please (for example LS6).' }, 422)
    if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
    return json({ people: [] })
  }))
  render(Household)
  await fireEvent.input(await screen.findByLabelText('Postcode district'), { target: { value: 'LS6 2AB' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save household' }))
  expect(await screen.findByText(/first part of your postcode/)).toBeInTheDocument()
})
```

`web/src/pages/settings/Agents.test.ts`:

```ts
import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Agents from './Agents.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const researcher = (max: number, version: number, overridden: string[] = []) => ({
  manifest: {
    name: 'researcher', description: 'Identifies unknown merchants.', enabled: true, task: 'research', model_chain: [],
    budgets: { max_llm_calls: 60, max_tokens: 300000, max_gbp: 0.5, max_seconds: 600 },
    thresholds: {}, limits: { max_merchants_per_run: max, max_tool_calls_per_merchant: 5, max_page_fetches_per_merchant: 2 },
    questions: null, triggers: [], tools: [], tone: 'plain', prompt_template: null,
  },
  overridden, user_file_error: null, version,
})

it('edits a limit and shows it as overridden', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/config/agents') return json({ agents: [researcher(20, 0)], preset: { value: 'balanced', version: 0 } })
    if (url === '/api/config/presets') return json({ presets: ['balanced', 'frugal', 'thorough'] })
    if (url === '/api/config/agents/researcher' && init?.method === 'PATCH') {
      const body = JSON.parse(init.body as string)
      expect(body).toEqual({ changes: { limits: { max_merchants_per_run: 12 } }, expected_version: 0 })
      return json(researcher(12, 1, ['limits.max_merchants_per_run']))
    }
    return json({ detail: 'unexpected' }, 500)
  }))
  render(Agents)
  const card = await screen.findByRole('region', { name: 'researcher' })
  const input = within(card).getByLabelText('max_merchants_per_run')
  await fireEvent.input(input, { target: { value: '12' } })
  await fireEvent.click(within(card).getByRole('button', { name: 'Save researcher' }))
  expect(await within(card).findByText('Changed by you')).toBeInTheDocument()
})
```

Update `web/src/App.test.ts`: the App now loads `/api/auth/session` first. Keep the two M0 assertions but render `Home` directly for the health status test (`render(Home)` with the `/health` mock), and add an App test: when the session mock returns `{authenticated: false, mode: 'server', needs_setup: true, …}`, App shows the heading "Set up Tuppence".

Run: `npm --prefix web test` → FAIL (components missing).

- [ ] **Step 2: Implement the library modules**

`web/src/lib/api.ts`:

```ts
export class ApiError extends Error {
  constructor(public status: number, public detail: string, public currentVersion?: number) {
    super(detail)
  }
}

let csrf: string | null = null
let unauthorised: () => void = () => {}

export function setCsrf(token: string | null) { csrf = token }
export function onUnauthorised(handler: () => void) { unauthorised = handler }

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = (opts.method ?? 'GET').toUpperCase()
  const headers = new Headers({ Accept: 'application/json' })
  if (opts.body !== undefined) headers.set('Content-Type', 'application/json')
  if (UNSAFE.has(method) && csrf) headers.set('X-CSRF-Token', csrf)
  const res = await fetch(path, {
    method, headers, credentials: 'same-origin',
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  })
  if (res.status === 204) return undefined as T
  let data: any = null
  try { data = await res.json() } catch { data = null }
  if (!res.ok) {
    if (res.status === 401) unauthorised()
    const detail = typeof data?.detail === 'string' ? data.detail : `Request failed (${res.status})`
    throw new ApiError(res.status, detail, data?.current_version)
  }
  return data as T
}
```

`web/src/lib/session.svelte.ts`:

```ts
import { api, onUnauthorised, setCsrf } from './api'

export type SessionInfo = {
  authenticated: boolean; mode: 'local' | 'desktop' | 'server'; needs_setup: boolean
  user: { username: string; is_admin: boolean } | null; csrf_token: string | null
}

export const session = $state({
  loaded: false, authenticated: false, mode: 'local' as SessionInfo['mode'], needsSetup: false,
  user: null as SessionInfo['user'], csrfToken: null as string | null,
})

export function applySession(info: SessionInfo) {
  session.loaded = true
  session.authenticated = info.authenticated
  session.mode = info.mode
  session.needsSetup = info.needs_setup
  session.user = info.user
  session.csrfToken = info.csrf_token
  setCsrf(info.csrf_token)
}

export async function loadSession() {
  applySession(await api<SessionInfo>('/api/auth/session'))
}

export async function signOut() {
  await api('/api/auth/logout', { method: 'POST' })
  await loadSession()
}

onUnauthorised(() => { session.authenticated = false; setCsrf(null) })
```

`web/src/lib/router.svelte.ts`:

```ts
export const router = $state({ path: typeof window === 'undefined' ? '/' : window.location.pathname })

export function navigate(path: string) {
  if (path !== window.location.pathname) window.history.pushState({}, '', path)
  router.path = path
}

export function link(event: MouseEvent) {
  const anchor = event.currentTarget as HTMLAnchorElement
  if (event.metaKey || event.ctrlKey || event.shiftKey || anchor.target === '_blank') return
  event.preventDefault()
  navigate(anchor.getAttribute('href') ?? '/')
}

if (typeof window !== 'undefined') {
  window.addEventListener('popstate', () => { router.path = window.location.pathname })
}
```

- [ ] **Step 3: Implement components and pages**

`web/src/components/Notice.svelte`:

```svelte
<script lang="ts">
  let { message = '', kind = 'error' }: { message?: string; kind?: 'error' | 'ok' } = $props()
</script>

<div class="notice {kind}" role={kind === 'error' ? 'alert' : 'status'} aria-live="polite">
  {#if message}{message}{/if}
</div>

<style>
  .notice:empty { display: none; }
  .notice { margin: .75rem 0; padding: .6rem .8rem; border-radius: 8px; }
  .error { background: var(--danger-bg); color: var(--danger); }
  .ok { background: var(--ok-bg); color: var(--ok); }
</style>
```

`web/src/components/Nav.svelte`:

```svelte
<script lang="ts">
  import { link, router } from '../lib/router.svelte'
  import { session, signOut } from '../lib/session.svelte'
  const items = [
    { href: '/', label: 'Home' },
    { href: '/settings/household', label: 'Household' },
    { href: '/settings/agents', label: 'Agents' },
  ]
</script>

<nav aria-label="Main">
  <a class="brand" href="/" onclick={link}>Tuppence</a>
  <ul>
    {#each items as item}
      <li><a href={item.href} onclick={link} aria-current={router.path === item.href ? 'page' : undefined}>{item.label}</a></li>
    {/each}
  </ul>
  {#if session.mode === 'server' && session.user}
    <button class="link" onclick={signOut}>Sign out {session.user.username}</button>
  {/if}
</nav>

<style>
  nav { display: flex; gap: 1.5rem; align-items: center; padding: .75rem 1.25rem; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .brand { font-weight: 700; font-size: 1.15rem; text-decoration: none; color: var(--ink); }
  ul { display: flex; gap: 1rem; list-style: none; margin: 0; padding: 0; flex: 1; }
  a[aria-current='page'] { font-weight: 600; text-decoration-thickness: 2px; }
  .link { background: none; border: none; color: var(--accent); cursor: pointer; font: inherit; }
</style>
```

`web/src/pages/Home.svelte` (keeps the M0 health status):

```svelte
<script lang="ts">
  import { onMount } from 'svelte'
  import { fetchHealth, type Health } from '../lib/health'
  let health = $state<Health | null>(null)
  let error = $state(false)
  onMount(async () => { try { health = await fetchHealth() } catch { error = true } })
</script>

<section>
  <h1>Tuppence</h1>
  <p class="tagline">A private AI money coach for UK households. Your statements never leave your machine.</p>
  {#if health}<p class="status ok">Connected · v{health.version} · {health.mode}</p>
  {:else if error}<p class="status err">Can't reach the Tuppence service. Is it running?</p>
  {:else}<p class="status">Connecting…</p>{/if}
  <p>Start by adding the people in your household in <a href="/settings/household">Household</a>.</p>
</section>
```

`web/src/pages/Setup.svelte`:

```svelte
<script lang="ts">
  import Notice from '../components/Notice.svelte'
  import { api, ApiError } from '../lib/api'
  import { applySession, type SessionInfo } from '../lib/session.svelte'
  let username = $state('')
  let password = $state('')
  let error = $state('')
  let busy = $state(false)
  async function submit(e: SubmitEvent) {
    e.preventDefault(); error = ''; busy = true
    try { applySession(await api<SessionInfo>('/api/auth/setup', { method: 'POST', body: { username, password } })) }
    catch (err) { error = err instanceof ApiError ? err.detail : 'Something went wrong.' }
    finally { busy = false }
  }
</script>

<section class="card narrow">
  <h1>Set up Tuppence</h1>
  <p>Create the administrator account for this household. There are no default passwords.</p>
  <form onsubmit={submit}>
    <label for="su-user">Username</label>
    <input id="su-user" autocomplete="username" required bind:value={username} />
    <label for="su-pass">Password</label>
    <input id="su-pass" type="password" autocomplete="new-password" minlength="10" required bind:value={password} />
    <p class="hint">At least 10 characters. A short sentence works well.</p>
    <Notice message={error} />
    <button type="submit" disabled={busy}>Create account</button>
  </form>
</section>
```

`web/src/pages/Login.svelte`: same structure as Setup — heading "Sign in", fields labelled "Username" and "Password" (`autocomplete="current-password"`), button "Sign in", POST `/api/auth/login`, `applySession` on success, `Notice` shows `err.detail` (covers 401 and 429 messages).

`web/src/pages/LaunchExpired.svelte`: heading "Open Tuppence again", text "This window lost its sign-in. Close it and open Tuppence again from your apps or terminal." (shown when not authenticated in local/desktop mode).

`web/src/pages/NotFound.svelte`: heading "Page not found" and a link home.

`web/src/pages/settings/Household.svelte`:

```svelte
<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import { api, ApiError } from '../../lib/api'

  type Person = { id: string; display_name: string; role: 'adult' | 'child' | 'dependent_adult'; birth_year: number | null; status: string; version: number }
  type Household = { nation: string | null; postcode_district: string | null; currency: string; period_mode: string; period_anchor_person_id: string | null; version: number }

  const ROLES = { adult: 'Adult', child: 'Child', dependent_adult: 'Dependent adult' }
  const NATIONS = { england: 'England', wales: 'Wales', scotland: 'Scotland', northern_ireland: 'Northern Ireland' }

  let household = $state<Household | null>(null)
  let people = $state<Person[]>([])
  let nation = $state('')
  let district = $state('')
  let name = $state('')
  let role = $state<Person['role']>('adult')
  let birthYear = $state('')
  let error = $state('')
  let saved = $state('')

  const fail = (err: unknown) => { saved = ''; error = err instanceof ApiError ? err.detail : 'Something went wrong.' }

  async function load() {
    household = await api<Household>('/api/household')
    nation = household.nation ?? ''
    district = household.postcode_district ?? ''
    people = (await api<{ people: Person[] }>('/api/household/people')).people
  }
  onMount(() => { load().catch(fail) })

  async function saveHousehold(e: SubmitEvent) {
    e.preventDefault(); error = ''
    try {
      household = await api<Household>('/api/household', {
        method: 'PATCH',
        body: { changes: { nation: nation || null, postcode_district: district || null }, expected_version: household!.version },
      })
      district = household.postcode_district ?? ''
      saved = 'Household saved.'
    } catch (err) { fail(err) }
  }

  async function addPerson(e: SubmitEvent) {
    e.preventDefault(); error = ''
    try {
      const body: Record<string, unknown> = { display_name: name, role }
      if (birthYear) body.birth_year = Number(birthYear)
      const p = await api<Person>('/api/household/people', { method: 'POST', body })
      people = [...people, p]; name = ''; birthYear = ''; role = 'adult'
      saved = `${p.display_name} added.`
    } catch (err) { fail(err) }
  }

  async function retire(p: Person) {
    error = ''
    try {
      await api(`/api/household/people/${p.id}/retire`, { method: 'POST', body: { expected_version: p.version } })
      people = people.filter((x) => x.id !== p.id)
      saved = `${p.display_name} removed from the household.`
    } catch (err) { fail(err) }
  }
</script>

<section>
  <h1>Household</h1>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />

  <form class="card" onsubmit={saveHousehold}>
    <h2>Where you live</h2>
    <label for="hh-nation">Nation</label>
    <select id="hh-nation" bind:value={nation}>
      <option value="">Choose…</option>
      {#each Object.entries(NATIONS) as [value, label]}<option {value}>{label}</option>{/each}
    </select>
    <label for="hh-district">Postcode district</label>
    <input id="hh-district" placeholder="e.g. LS6" bind:value={district} autocomplete="off" />
    <p class="hint">Only the first part of your postcode. Tuppence never needs your full address.</p>
    <button type="submit">Save household</button>
  </form>

  <div class="card">
    <h2>People</h2>
    {#if people.length === 0}<p>No one added yet.</p>{/if}
    <ul class="people">
      {#each people as p (p.id)}
        <li>
          <span class="name">{p.display_name}</span>
          <span class="meta">{ROLES[p.role]}{p.birth_year ? ` · born ${p.birth_year}` : ''}</span>
          <button class="link" onclick={() => retire(p)} aria-label={`Remove ${p.display_name}`}>Remove</button>
        </li>
      {/each}
    </ul>
    <form onsubmit={addPerson} class="row">
      <div><label for="np-name">Name</label><input id="np-name" required maxlength="60" bind:value={name} /></div>
      <div>
        <label for="np-role">Role</label>
        <select id="np-role" bind:value={role}>
          {#each Object.entries(ROLES) as [value, label]}<option {value}>{label}</option>{/each}
        </select>
      </div>
      <div><label for="np-year">Birth year (children)</label><input id="np-year" inputmode="numeric" bind:value={birthYear} /></div>
      <button type="submit">Add person</button>
    </form>
  </div>
</section>
```

`web/src/pages/settings/Agents.svelte`:

```svelte
<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import { api, ApiError } from '../../lib/api'

  type Manifest = {
    name: string; description: string; enabled: boolean; task: string | null
    budgets: Record<string, number>; thresholds: Record<string, number>; limits: Record<string, number>
    [key: string]: unknown
  }
  type View = { manifest: Manifest; overridden: string[]; user_file_error: string | null; version: number }

  let agents = $state<View[]>([])
  let presets = $state<string[]>([])
  let preset = $state({ value: 'balanced', version: 0 })
  let drafts = $state<Record<string, Record<string, unknown>>>({})
  let error = $state('')
  let saved = $state('')

  const fail = (err: unknown) => { saved = ''; error = err instanceof ApiError ? err.detail : 'Something went wrong.' }

  async function load() {
    const res = await api<{ agents: View[]; preset: { value: string; version: number } }>('/api/config/agents')
    agents = res.agents; preset = res.preset; drafts = {}
    presets = (await api<{ presets: string[] }>('/api/config/presets')).presets
  }
  onMount(() => { load().catch(fail) })

  function setDraft(agent: string, section: string, key: string, value: unknown) {
    const d = drafts[agent] ?? {}
    const s = (d[section] as Record<string, unknown>) ?? {}
    drafts[agent] = { ...d, [section]: { ...s, [key]: value } }
  }

  async function save(view: View) {
    const changes = drafts[view.manifest.name]
    if (!changes) return
    error = ''
    try {
      const updated = await api<View>(`/api/config/agents/${view.manifest.name}`, {
        method: 'PATCH', body: { changes, expected_version: view.version },
      })
      agents = agents.map((a) => (a.manifest.name === updated.manifest.name ? updated : a))
      delete drafts[view.manifest.name]
      saved = `${view.manifest.name} saved.`
    } catch (err) { fail(err) }
  }

  async function reset(view: View, path: string) {
    try {
      const updated = await api<View>(`/api/config/agents/${view.manifest.name}/reset`, {
        method: 'POST', body: { path, expected_version: view.version },
      })
      agents = agents.map((a) => (a.manifest.name === updated.manifest.name ? updated : a))
    } catch (err) { fail(err) }
  }

  async function choosePreset(value: string) {
    try {
      const entry = await api<{ value: string; version: number }>('/api/settings/config.preset', {
        method: 'PATCH', body: { value, expected_version: preset.version },
      })
      preset = { value: entry.value, version: entry.version }
      await load()
      saved = `Preset changed to ${value}.`
    } catch (err) { fail(err) }
  }
</script>

<section>
  <h1>Agents</h1>
  <p>Tuppence is open source: every agent's limits and budgets can be tuned here.</p>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />

  <div class="card">
    <label for="preset">Preset</label>
    <select id="preset" value={preset.value} onchange={(e) => choosePreset((e.currentTarget as HTMLSelectElement).value)}>
      {#each presets as p}<option value={p}>{p}</option>{/each}
    </select>
    <a href="/api/config/export" download>Export settings</a>
  </div>

  {#each agents as view (view.manifest.name)}
    {@const m = view.manifest}
    <section class="card" role="region" aria-label={m.name}>
      <h2>{m.name}</h2>
      <p>{m.description}</p>
      {#if view.user_file_error}<p class="warn">Your config file was ignored: {view.user_file_error}</p>{/if}
      <label><input type="checkbox" checked={m.enabled} onchange={(e) => {
        const d = drafts[m.name] ?? {}
        drafts[m.name] = { ...d, enabled: (e.currentTarget as HTMLInputElement).checked }
      }} /> Enabled</label>
      {#each ['budgets', 'limits', 'thresholds'] as section}
        {#if Object.keys(m[section] as Record<string, number>).length}
          <fieldset>
            <legend>{section}</legend>
            {#each Object.entries(m[section] as Record<string, number>) as [key, value]}
              {@const path = `${section}.${key}`}
              <div class="field">
                <label for={`${m.name}-${path}`}>{key}</label>
                <input id={`${m.name}-${path}`} type="number" step="any" value={value}
                  oninput={(e) => setDraft(m.name, section, key, Number((e.currentTarget as HTMLInputElement).value))} />
                {#if view.overridden.includes(path)}
                  <span class="badge">Changed by you</span>
                  <button class="link" onclick={() => reset(view, path)}>Reset</button>
                {/if}
              </div>
            {/each}
          </fieldset>
        {/if}
      {/each}
      <button onclick={() => save(view)} disabled={!drafts[m.name]}>Save {m.name}</button>
    </section>
  {/each}
</section>
```

`web/src/App.svelte`:

```svelte
<script lang="ts">
  import { onMount } from 'svelte'
  import Nav from './components/Nav.svelte'
  import { router } from './lib/router.svelte'
  import { loadSession, session } from './lib/session.svelte'
  import Home from './pages/Home.svelte'
  import LaunchExpired from './pages/LaunchExpired.svelte'
  import Login from './pages/Login.svelte'
  import NotFound from './pages/NotFound.svelte'
  import Setup from './pages/Setup.svelte'
  import Agents from './pages/settings/Agents.svelte'
  import Household from './pages/settings/Household.svelte'

  const routes: Record<string, typeof Home> = { '/': Home, '/settings/household': Household, '/settings/agents': Agents }
  let failed = $state(false)
  onMount(() => { loadSession().catch(() => { failed = true }) })
  const Page = $derived(routes[router.path] ?? NotFound)
</script>

{#if failed}
  <main><p class="status err">Can't reach the Tuppence service. Is it running?</p></main>
{:else if !session.loaded}
  <main><p class="status">Loading…</p></main>
{:else if !session.authenticated}
  <main>
    {#if session.mode !== 'server'}<LaunchExpired />
    {:else if session.needsSetup}<Setup />
    {:else}<Login />{/if}
  </main>
{:else}
  <Nav />
  <main><Page /></main>
{/if}
```

`web/src/app.css` — design tokens with light/dark, base element styles shared by the pages:

```css
:root {
  color-scheme: light dark;
  --bg: #fbfaf7; --panel: #ffffff; --ink: #1d1b16; --muted: #5f5a4f; --line: #e3ded3;
  --accent: #8a5a12; --danger: #a02020; --danger-bg: #fbe9e9; --ok: #1b6b3a; --ok-bg: #e7f4ec;
  font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; line-height: 1.5;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #16150f; --panel: #1f1d17; --ink: #f2efe6; --muted: #b3ad9f; --line: #37332a;
    --accent: #e3b04b; --danger: #ff8f8f; --danger-bg: #3a1d1d; --ok: #8fd6a8; --ok-bg: #18301f; }
}
body { margin: 0; background: var(--bg); color: var(--ink); }
main { max-width: 56rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
a { color: var(--accent); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 1rem 1.25rem; margin: 1rem 0; }
.narrow { max-width: 26rem; margin: 3rem auto; }
label { display: block; font-weight: 600; margin: .75rem 0 .25rem; }
input, select { font: inherit; padding: .45rem .6rem; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--ink); }
button { font: inherit; padding: .5rem .9rem; border-radius: 8px; border: 1px solid var(--accent); background: var(--accent); color: var(--panel); cursor: pointer; margin-top: .75rem; }
button:disabled { opacity: .5; cursor: default; }
button.link { background: none; border: none; color: var(--accent); padding: 0; margin: 0; text-decoration: underline; }
:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.hint, .meta { color: var(--muted); font-size: .9rem; }
.status.ok { color: var(--ok); } .status.err { color: var(--danger); }
.people { list-style: none; padding: 0; } .people li { display: flex; gap: 1rem; align-items: baseline; padding: .35rem 0; border-bottom: 1px solid var(--line); }
.row { display: flex; gap: 1rem; flex-wrap: wrap; align-items: end; }
.badge { font-size: .8rem; background: var(--ok-bg); color: var(--ok); border-radius: 999px; padding: .1rem .5rem; margin-left: .5rem; }
.warn { color: var(--danger); }
fieldset { border: 1px solid var(--line); border-radius: 8px; margin: .75rem 0; }
.field { display: flex; gap: .75rem; align-items: center; flex-wrap: wrap; }
.field label { min-width: 16rem; font-weight: 500; }
```

Make sure `main.ts` imports `./app.css`.

- [ ] **Step 4: Run tests and build**

Run: `npm --prefix web test` → all pass. `npm --prefix web run check` → 0 errors. `npm --prefix web run build` → OK.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add app shell UI: sign-in, navigation, Household and Agents settings"
```

---

### Task 8: End-to-end tests in a real browser and M1a verification

**Files:**
- Create: `web/playwright.config.ts`, `web/e2e/helpers.ts`, `web/e2e/auth.spec.ts`, `web/e2e/household.spec.ts`, `web/e2e/agents.spec.ts`, `scripts/e2e.sh`
- Modify: `web/package.json` (add `@playwright/test` dev dependency, scripts `"e2e": "playwright test"`), `.gitignore` (`web/test-results/`, `web/playwright-report/`), `CONTRIBUTING.md` (how to run e2e), `.github/workflows/ci.yml` (e2e job)

**Interfaces:**
- Consumes: the built UI (`src/tuppence/web_dist`), CLI `tuppence serve --mode server`.
- Produces: `scripts/e2e.sh` — builds the UI, starts a server-mode instance on a free port with a fresh temp data dir, runs Playwright against it, stops it; exit code = Playwright's. CI job `e2e` runs it on ubuntu.

- [ ] **Step 1: Install Playwright**

```bash
cd web && npm install -D @playwright/test && npx playwright install chromium && cd ..
```

`web/playwright.config.ts`:

```ts
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.TUPPENCE_URL ?? 'http://127.0.0.1:18080',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
```

`scripts/e2e.sh` (chmod +x):

```bash
#!/usr/bin/env bash
# Build the UI, start a fresh server-mode Tuppence, run Playwright against it.
set -euo pipefail
cd "$(dirname "$0")/.."
npm --prefix web run build
DATA="$(mktemp -d)"
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
uv run tuppence serve --mode server --host 127.0.0.1 --port "$PORT" --data-dir "$DATA" >"$DATA/server.log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true; rm -rf "$DATA"' EXIT
uv run --quiet python scripts/smoke_http.py "http://127.0.0.1:$PORT" --timeout 60 --expect-mode server
cd web
TUPPENCE_URL="http://127.0.0.1:$PORT" npx playwright test "$@" || { cat "$DATA/server.log"; exit 1; }
```

- [ ] **Step 2: Write the end-to-end tests**

Tests run in file order against one fresh server: `auth.spec.ts` (setup happens here) → `household.spec.ts` → `agents.spec.ts`. Name files with numeric prefixes to fix the order: `01-auth.spec.ts`, `02-household.spec.ts`, `03-agents.spec.ts`.

`web/e2e/helpers.ts`:

```ts
import { expect, type Page } from '@playwright/test'

export const ADMIN = { username: 'alex', password: 'a-long-test-passphrase' }

export async function signIn(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.getByLabel('Username').fill(ADMIN.username)
  await page.getByLabel('Password').fill(ADMIN.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
}
```

`web/e2e/01-auth.spec.ts`:

```ts
import { expect, test } from '@playwright/test'
import { ADMIN, signIn } from './helpers'

test('first run asks for an admin account, then signs in', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Set up Tuppence' })).toBeVisible()
  await page.getByLabel('Username').fill(ADMIN.username)
  await page.getByLabel('Password').fill('short')
  await page.getByRole('button', { name: 'Create account' }).click()
  // browser-side minlength blocks submission; type a real password
  await page.getByLabel('Password').fill(ADMIN.password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Tuppence', level: 1 })).toBeVisible()
  await expect(page.getByText(/Connected · v.* · server/)).toBeVisible()
})

test('sign out, wrong password, then sign in', async ({ page }) => {
  await signIn(page)
  await page.getByRole('button', { name: /Sign out/ }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.getByLabel('Username').fill(ADMIN.username)
  await page.getByLabel('Password').fill('wrong-password-xx')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toHaveText('Wrong username or password.')
  await page.getByLabel('Password').fill(ADMIN.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
})

test('session cookie is HttpOnly and SameSite=Strict', async ({ page, context }) => {
  await signIn(page)
  const cookie = (await context.cookies()).find((c) => c.name === 'tuppence_session')!
  expect(cookie.httpOnly).toBe(true)
  expect(cookie.sameSite).toBe('Strict')
})
```

`web/e2e/02-household.spec.ts`:

```ts
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('add people, set district, reject a full postcode, remove a person', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: 'Household' }).click()
  await expect(page.getByRole('heading', { name: 'Household', level: 1 })).toBeVisible()

  await page.getByLabel('Nation').selectOption('england')
  await page.getByLabel('Postcode district').fill('LS6 2AB')
  await page.getByRole('button', { name: 'Save household' }).click()
  await expect(page.getByRole('alert')).toContainText('first part of your postcode')
  await page.getByLabel('Postcode district').fill('ls6')
  await page.getByRole('button', { name: 'Save household' }).click()
  await expect(page.getByText('Household saved.')).toBeVisible()
  await expect(page.getByLabel('Postcode district')).toHaveValue('LS6')

  await page.getByLabel('Name').fill('Alex Example')
  await page.getByRole('button', { name: 'Add person' }).click()
  await page.getByLabel('Name').fill('Kid A')
  await page.getByLabel('Role').selectOption('child')
  await page.getByLabel('Birth year (children)').fill('2019')
  await page.getByRole('button', { name: 'Add person' }).click()
  await expect(page.getByText('born 2019')).toBeVisible()

  await page.reload()
  await expect(page.getByText('Alex Example')).toBeVisible()
  await page.getByRole('button', { name: 'Remove Kid A' }).click()
  await expect(page.getByText('Kid A')).toHaveCount(0)
})

test('a stale edit from a second tab gets the conflict message', async ({ browser }) => {
  const ctx = await browser.newContext()
  const a = await ctx.newPage()
  const b = await ctx.newPage()
  await signIn(a)
  await a.goto('/settings/household')
  await b.goto('/settings/household')
  await a.getByLabel('Postcode district').fill('LS7')
  await a.getByRole('button', { name: 'Save household' }).click()
  await expect(a.getByText('Household saved.')).toBeVisible()
  await b.getByLabel('Postcode district').fill('LS8')
  await b.getByRole('button', { name: 'Save household' }).click()
  await expect(b.getByRole('alert')).toHaveText('This was changed somewhere else. Reload and try again.')
  await ctx.close()
})
```

`web/e2e/03-agents.spec.ts`:

```ts
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('tune an agent, reset it, and switch preset', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: 'Agents' }).click()
  const researcher = page.getByRole('region', { name: 'researcher' })
  await researcher.getByLabel('max_merchants_per_run').fill('12')
  await researcher.getByRole('button', { name: 'Save researcher' }).click()
  await expect(researcher.getByText('Changed by you')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('region', { name: 'researcher' }).getByLabel('max_merchants_per_run')).toHaveValue('12')

  await page.getByRole('region', { name: 'researcher' }).getByRole('button', { name: 'Reset' }).click()
  await expect(page.getByRole('region', { name: 'researcher' }).getByLabel('max_merchants_per_run')).toHaveValue('20')

  await page.getByLabel('Preset').selectOption('frugal')
  await expect(page.getByText('Preset changed to frugal.')).toBeVisible()
  await expect(page.getByRole('region', { name: 'researcher' }).getByLabel('Enabled')).not.toBeChecked()
  await page.getByLabel('Preset').selectOption('balanced')
})
```

- [ ] **Step 3: Run end-to-end tests**

Run: `bash scripts/e2e.sh` → Expected: all Playwright tests pass. Fix real bugs found (in the code under test, not by weakening tests). If a selector is ambiguous, prefer improving the UI's accessible names over brittle selectors.

- [ ] **Step 4: Add the CI job and docs**

Append to `.github/workflows/ci.yml`:

```yaml
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm, cache-dependency-path: web/package-lock.json }
      - run: uv sync --frozen
      - run: npm --prefix web ci
      - run: cd web && npx playwright install --with-deps chromium
      - run: bash scripts/e2e.sh
```

In `CONTRIBUTING.md` "Tests", add: `bash scripts/e2e.sh` — browser end-to-end tests against a fresh server (first run: `cd web && npx playwright install chromium`).

- [ ] **Step 5: Full M1a verification**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest -q
npm --prefix web test && npm --prefix web run check
bash scripts/e2e.sh
uv run pytest -m slow -q        # docker, wheel, desktop smoke still pass with auth + migrations
uv run python scripts/denylist_guard.py --require
```

Also confirm the wheel contains the migrations and default manifests:
`python3 -c "import zipfile,glob; n=zipfile.ZipFile(sorted(glob.glob('dist/tuppence-*.whl'))[-1]).namelist(); assert any(x.endswith('0004_jobs.sql') for x in n); assert any(x.endswith('agents/coach.toml') for x in n); print('wheel ok')"`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Add Playwright end-to-end tests for sign-in, household and agents"
```
