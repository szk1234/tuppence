"""Which model handles which task (spec §4.3)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.settings_store import SettingsStore
from tuppence.llm.connections import Connection, ConnectionRegistry, ModelInfo
from tuppence.llm.types import NoModelConfigured

TASKS: tuple[str, ...] = ("read", "categorise", "review", "research", "coach", "report", "vision")
TASK_TIMEOUTS: dict[str, float] = {
    "read": 400,
    "categorise": 120,
    "review": 120,
    "research": 120,
    "coach": 60,
    "report": 120,
    "vision": 300,
}


def timeout_for(task: str, is_local: bool) -> float:
    return TASK_TIMEOUTS.get(task, 120) * (3 if is_local else 1)


class TaskRoute(BaseModel):
    chain: list[dict[str, str]]
    local_only: bool
    version: int


class RoutingView(BaseModel):
    mode: str
    mode_version: int
    simple_model: dict[str, str] | None
    simple_version: int
    tasks: dict[str, TaskRoute]


class TaskRouter:
    def __init__(
        self, db: Database, settings: SettingsStore, connections: ConnectionRegistry
    ) -> None:
        self.db, self.settings, self.connections = db, settings, connections

    def _routes(self) -> dict[str, TaskRoute]:
        with self.db.connection() as conn:
            rows = {r["task"]: r for r in conn.execute("SELECT * FROM llm_route")}
        return {
            t: TaskRoute(
                chain=json.loads(rows[t]["chain"]),
                local_only=bool(rows[t]["local_only"]),
                version=rows[t]["version"],
            )
            if t in rows
            else TaskRoute(chain=[], local_only=False, version=0)
            for t in TASKS
        }

    def view(self) -> RoutingView:
        mode = self.settings.entry("llm.mode")
        simple = self.settings.entry("llm.simple_model")
        return RoutingView(
            mode=mode.value,
            mode_version=mode.version,
            simple_model=simple.value,
            simple_version=simple.version,
            tasks=self._routes(),
        )

    def set_task(
        self, task: str, chain: list[dict[str, Any]], local_only: bool, expected_version: int
    ) -> RoutingView:
        if task not in TASKS:
            raise InputError(f"Unknown task '{task}'.")
        clean = []
        for ref in chain:
            self.connections.model(
                str(ref["connection_id"]), str(ref["model_id"])
            )  # NotFound if bogus
            clean.append(
                {"connection_id": str(ref["connection_id"]), "model_id": str(ref["model_id"])}
            )
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            row = conn.execute("SELECT version FROM llm_route WHERE task = ?", [task]).fetchone()
            current = 0 if row is None else int(row["version"])
            if current != expected_version:
                raise VersionConflict("llm_route", task, expected_version, current)
            conn.execute(
                "INSERT INTO llm_route (task, chain, local_only, version, updated_at)"
                " VALUES (?, ?, ?, 1, ?)"
                " ON CONFLICT(task) DO UPDATE SET chain = excluded.chain,"
                " local_only = excluded.local_only,"
                " version = llm_route.version + 1, updated_at = excluded.updated_at",
                [task, json.dumps(clean), int(local_only), now],
            )
        return self.view()

    def chain_for(self, task: str) -> list[tuple[Connection, ModelInfo]]:
        routes = self._routes()
        route = routes.get(task, TaskRoute(chain=[], local_only=False, version=0))
        refs: list[dict[str, str]] = []
        if self.settings.get("llm.mode") == "advanced" and route.chain:
            refs = route.chain
        elif self.settings.get("llm.simple_model"):
            refs = [self.settings.get("llm.simple_model")]
        resolved: list[tuple[Connection, ModelInfo]] = []
        for ref in refs:
            try:
                conn = self.connections.get(ref["connection_id"])
                model = self.connections.model(ref["connection_id"], ref["model_id"])
            except NotFound:
                continue
            if not conn.enabled or (route.local_only and not conn.is_local):
                continue
            if task == "vision" and not model.supports_vision:
                continue
            resolved.append((conn, model))
        if not resolved:
            raise NoModelConfigured("Choose an AI model in Settings › AI.")
        return resolved
