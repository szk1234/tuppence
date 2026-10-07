"""Typed, versioned app settings stored as JSON in `app_settings`."""

from __future__ import annotations

import json
import logging
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


log = logging.getLogger("tuppence")

SETTINGS: dict[str, SettingDef] = {}


def define(key: str, type_: Any, default: Any, description: str) -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(type_)
    SETTINGS[key] = SettingDef(key, adapter.validate_python(default), adapter, description)


define(
    "privacy.local_only",
    bool,
    False,
    "Keep AI and research calls on this device or your home network.",
)
define(
    "privacy.pseudonymise",
    bool,
    False,
    "Swap names and account numbers for stand-ins before cloud AI calls.",
)
define(
    "privacy.hidden_names",
    list[str],
    [],
    "Other names to hide from cloud AI (e.g. your landlord).",
)
define(
    "privacy.research_lookups",
    bool,
    False,
    "Let the agent look up merchant names online (never amounts or your details).",
)
define(
    "privacy.live_market_data",
    bool,
    True,
    "Fetch interest rates, inflation, exchange rates and prices.",
)
define(
    "privacy.datapack_updates", bool, True, "Download updated UK data packs (tax, benefits, rents)."
)
# Must match the preset files in tuppence/config/defaults/presets (tested).
PresetName = Literal["frugal", "balanced", "thorough"]

define(
    "config.preset",
    PresetName,
    "balanced",
    "How much work the agents do.",
)
define(
    "llm.mode",
    Literal["simple", "advanced"],
    "simple",
    "Simple: one AI model for everything. Advanced: choose models per task.",
)
define(
    "llm.simple_model",
    dict[str, str] | None,
    None,
    "The AI model used for every task in simple mode.",
)
define(
    "llm.monthly_cap_gbp",
    Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)],
    10.0,
    "Monthly AI spending cap in pounds.",
)
define(
    "llm.run_cap_gbp",
    Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)],
    1.0,
    "Spending cap for one analysis run, in pounds.",
)
define(
    "llm.usd_to_gbp",
    Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)],
    0.75,
    "Exchange rate used to price AI usage.",
)


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
            row = conn.execute(
                "SELECT value, version FROM app_settings WHERE key = ?", [key]
            ).fetchone()
        if row is None:
            return SettingEntry(
                key=key, value=d.default, default=d.default, version=0, description=d.description
            )
        try:
            value = d.adapter.validate_python(json.loads(row["value"]))
        except (ValueError, ValidationError):
            log.warning("Stored value for setting %s is invalid; using the default", key)
            value = d.default
        return SettingEntry(
            key=key,
            value=value,
            default=d.default,
            version=row["version"],
            description=d.description,
        )

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
        payload = json.dumps(d.adapter.dump_python(clean, mode="json"), allow_nan=False)
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            row = conn.execute("SELECT version FROM app_settings WHERE key = ?", [key]).fetchone()
            current = 0 if row is None else int(row["version"])
            if current != expected_version:
                raise VersionConflict("app_settings", key, expected_version, current)
            if row is None:
                conn.execute(
                    "INSERT INTO app_settings (key, value, version, updated_at) "
                    "VALUES (?, ?, 1, ?)",
                    [key, payload, now],
                )
            else:
                conn.execute(
                    "UPDATE app_settings SET value = ?, version = version + 1, "
                    "updated_at = ? WHERE key = ?",
                    [payload, now, key],
                )
        return self.entry(key)
