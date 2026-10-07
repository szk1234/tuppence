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
    """Dicts merge recursively; lists and scalars replace."""
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
        layer: dict[str, Any] = _load_toml_resource("presets", f"{preset}.toml").get("agents", {})
        return layer.get(name, {})

    def _user_layer(self, name: str, base: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        path = self.user_dir / "agents" / f"{name}.toml"
        if not path.is_file():
            return {}, None
        try:
            layer = tomllib.loads(path.read_text(encoding="utf-8"))
            AgentManifest.model_validate(deep_merge(base, layer))
        except (tomllib.TOMLDecodeError, ValidationError, UnicodeDecodeError, OSError) as exc:
            return {}, f"{path.name}: {exc}".splitlines()[0][:300]
        return layer, None

    def _ui_layer(self, name: str) -> tuple[dict[str, Any], int]:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT value, version FROM agent_override WHERE name = ?", [name]
            ).fetchone()
        return ({}, 0) if row is None else (json.loads(row["value"]), int(row["version"]))

    def _base(self, name: str) -> dict[str, Any]:
        return deep_merge(self._defaults(name), self._preset_layer(name))

    def view(self, name: str) -> AgentView:
        base = self._base(name)
        user, error = self._user_layer(name, base)
        ui, version = self._ui_layer(name)
        manifest = AgentManifest.model_validate(deep_merge(deep_merge(base, user), ui))
        return AgentView(
            manifest=manifest, overridden=_paths(ui), user_file_error=error, version=version
        )

    def views(self) -> list[AgentView]:
        return [self.view(n) for n in self.agent_names()]

    def get(self, name: str) -> AgentManifest:
        return self.view(name).manifest

    def _write_ui(self, name: str, layer: dict[str, Any], expected_version: int) -> AgentView:
        if LOCKED_FIELDS & set(layer):
            raise InputError("An agent's name can't be changed.")
        base = self._base(name)
        user, _ = self._user_layer(name, base)
        try:
            AgentManifest.model_validate(deep_merge(deep_merge(base, user), layer))
        except ValidationError as exc:
            err = exc.errors()[0]
            field = ".".join(str(p) for p in err["loc"])
            raise InputError(f"{field}: {err['msg']}") from None
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT version FROM agent_override WHERE name = ?", [name]
            ).fetchone()
            current = 0 if row is None else int(row["version"])
            if current != expected_version:
                raise VersionConflict("agent_override", name, expected_version, current)
            payload = json.dumps(layer)
            if row is None:
                conn.execute(
                    "INSERT INTO agent_override (name, value, version, updated_at) "
                    "VALUES (?, ?, 1, ?)",
                    [name, payload, now],
                )
            else:
                conn.execute(
                    "UPDATE agent_override SET value = ?, version = version + 1, "
                    "updated_at = ? WHERE name = ?",
                    [payload, now, name],
                )
        return self.view(name)

    def set_override(self, name: str, changes: dict[str, Any], expected_version: int) -> AgentView:
        self._defaults(name)  # unknown agent -> NotFound
        current, _ = self._ui_layer(name)
        return self._write_ui(name, deep_merge(current, changes), expected_version)

    def reset_field(self, name: str, path: str, expected_version: int) -> AgentView:
        self._defaults(name)
        current, _ = self._ui_layer(name)
        return self._write_ui(name, _remove_path(current, path), expected_version)

    def export(self) -> dict[str, Any]:
        return {
            "preset": self.settings.get("config.preset"),
            "agents": {v.manifest.name: v.manifest.model_dump(mode="json") for v in self.views()},
        }
