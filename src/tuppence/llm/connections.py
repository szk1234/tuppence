"""Saved LLM connections and their models (spec §4.1, §4.2).

Secrets (API keys and custom header values) live in the secret store; the database only
holds references and header names. Secret-store failures (`SecretError`) propagate with
their own user-facing message for the API layer to show.
"""

from __future__ import annotations

import json
import secrets as pysecrets
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, update_versioned
from tuppence.core.secrets import SecretStore, SecretUnreadable
from tuppence.llm.catalogue import CatalogueEntry, ModelCatalogue
from tuppence.llm.presets import PRESETS, normalise_base_url
from tuppence.llm.providers import build_provider
from tuppence.llm.types import Provider, ProviderModel
from tuppence.net.client import CallContext
from tuppence.net.hosts import is_local_host

ClientFactory = Callable[[CallContext, float], httpx.Client]

DEFAULT_CONTEXT_LOCAL = 4096
DEFAULT_CONTEXT_CLOUD = 32768


class Connection(BaseModel):
    id: str
    preset: str
    name: str
    api_style: str
    base_url: str
    is_local: bool
    has_key: bool
    header_names: list[str]
    needs_notice: bool
    notice_acknowledged_at: str | None
    enabled: bool
    version: int


class ModelInfo(BaseModel):
    connection_id: str
    model_id: str
    display_name: str | None
    context_window: int
    max_output_tokens: int | None
    supports_tools: bool
    supports_json_schema: bool
    supports_vision: bool
    price_in_usd_per_mtok: float | None
    price_out_usd_per_mtok: float | None
    source: str
    version: int = 1


class DetectedServer(BaseModel):
    preset: str
    base_url: str
    model_count: int


def _locality(preset_id: str, base_url: str) -> bool:
    if PRESETS[preset_id].kind == "cloud":
        return False
    return is_local_host(httpx.URL(base_url).host)


def _new_ref() -> str:
    return "sec_" + pysecrets.token_hex(8)


def _clean_headers(headers: dict[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in (headers or {}).items():
        name, value = str(name).strip(), str(value)
        if not name:
            continue
        if not name.isascii() or any(c in name for c in ":\r\n ") or "\r" in value or "\n" in value:
            raise InputError(f"'{name}' isn't a valid header.")
        out[name] = value
    return out


def _header_meta(raw: str) -> dict[str, Any]:
    """Tolerant parse of the `headers` column: {"names": [...], "ref": str | None}."""
    try:
        data = json.loads(raw)
    except ValueError:
        return {"names": [], "ref": None}
    if isinstance(data, dict) and isinstance(data.get("names"), list):
        return {"names": [str(n) for n in data["names"]], "ref": data.get("ref")}
    return {"names": [], "ref": None}


class _Picker:
    """Provider value, else catalogue value, else default."""

    def __init__(self, provider: ProviderModel, entry: CatalogueEntry | None) -> None:
        self.provider, self.entry = provider, entry

    def __call__(self, attr: str, default: Any) -> Any:
        value = getattr(self.provider, attr)
        if value is not None:
            return value
        if self.entry is not None and getattr(self.entry, attr) is not None:
            return getattr(self.entry, attr)
        return default


class ConnectionRegistry:
    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        catalogue: ModelCatalogue,
        *,
        client_factory: ClientFactory,
    ) -> None:
        self.db, self.secrets, self.catalogue, self.client_factory = (
            db,
            secrets,
            catalogue,
            client_factory,
        )

    def _row_to_connection(self, r: Any) -> Connection:
        return Connection(
            id=r["id"],
            preset=r["preset"],
            name=r["name"],
            api_style=r["api_style"],
            base_url=r["base_url"],
            is_local=bool(r["is_local"]),
            has_key=r["secret_ref"] is not None,
            header_names=_header_meta(r["headers"])["names"],
            needs_notice=not r["is_local"] and r["notice_acknowledged_at"] is None,
            notice_acknowledged_at=r["notice_acknowledged_at"],
            enabled=bool(r["enabled"]),
            version=r["version"],
        )

    def _row(self, connection_id: str) -> Any:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM llm_connection WHERE id = ?", [connection_id]
            ).fetchone()
        if row is None:
            raise NotFound("llm_connection", connection_id)
        return row

    def list(self) -> list[Connection]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_connection ORDER BY created_at, rowid"
            ).fetchall()
        return [self._row_to_connection(r) for r in rows]

    def get(self, connection_id: str) -> Connection:
        return self._row_to_connection(self._row(connection_id))

    def _store_headers(self, headers: dict[str, str]) -> str:
        """Header values are secrets. Returns the JSON for the `headers` column."""
        if not headers:
            return json.dumps({"names": [], "ref": None})
        ref = self.secrets.put(json.dumps(headers), _new_ref())
        return json.dumps({"names": sorted(headers), "ref": ref})

    def _drop(self, *refs: str | None) -> None:
        for ref in refs:
            if ref:
                self.secrets.delete(ref)

    def create(
        self,
        preset: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Connection:
        if preset not in PRESETS:
            raise InputError(f"Unknown provider '{preset}'.")
        p = PRESETS[preset]
        raw_url = base_url or p.base_url
        if not raw_url:
            raise InputError("Enter the server's base URL.")
        url = normalise_base_url(raw_url, p.api_style)
        key = (api_key or "").strip() or None
        if p.key_required and not key:
            raise InputError(f"An API key is needed for {p.label}.")
        clean = _clean_headers(headers)
        connection_id = "c_" + pysecrets.token_hex(4)
        secret_ref = self.secrets.put(key) if key else None
        try:
            header_json = self._store_headers(clean)
        except Exception:
            self._drop(secret_ref)
            raise
        now = to_iso(utcnow())
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO llm_connection (id, preset, name, api_style, base_url,"
                    " secret_ref, headers, is_local, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        connection_id,
                        preset,
                        (name or p.label).strip() or p.label,
                        p.api_style,
                        url,
                        secret_ref,
                        header_json,
                        int(_locality(preset, url)),
                        now,
                        now,
                    ],
                )
        except Exception:
            self._drop(secret_ref, _header_meta(header_json)["ref"])
            raise
        return self.get(connection_id)

    def update(
        self, connection_id: str, changes: dict[str, Any], expected_version: int
    ) -> Connection:
        row = self._row(connection_id)
        allowed = {"name", "base_url", "api_key", "enabled", "headers"}
        unknown = set(changes) - allowed
        if unknown:
            raise InputError(f"Can't change: {', '.join(sorted(unknown))}")
        data: dict[str, Any] = {}
        if "name" in changes:
            data["name"] = str(changes["name"]).strip() or row["name"]
        if "base_url" in changes:
            data["base_url"] = normalise_base_url(str(changes["base_url"]), row["api_style"])
            data["is_local"] = int(_locality(row["preset"], data["base_url"]))
            if not data["is_local"] and row["is_local"]:
                data["notice_acknowledged_at"] = None
        if "enabled" in changes:
            data["enabled"] = int(bool(changes["enabled"]))
        old_refs: list[str | None] = []
        new_refs: list[str | None] = []
        try:
            if "api_key" in changes:
                key = str(changes["api_key"] or "").strip()
                new_ref = self.secrets.put(key) if key else None
                new_refs.append(new_ref)
                old_refs.append(row["secret_ref"])
                data["secret_ref"] = new_ref
            if "headers" in changes:
                header_json = self._store_headers(_clean_headers(changes["headers"]))
                new_refs.append(_header_meta(header_json)["ref"])
                old_refs.append(_header_meta(row["headers"])["ref"])
                data["headers"] = header_json
            if data:
                # Version-checked write first; a 409 must not have rotated anything.
                with self.db.transaction() as conn:
                    update_versioned(
                        conn,
                        "llm_connection",
                        "id",
                        connection_id,
                        expected_version,
                        data,
                        now=to_iso(utcnow()),
                    )
        except Exception:
            self._drop(*new_refs)
            raise
        self._drop(*old_refs)
        return self.get(connection_id)

    def delete(self, connection_id: str) -> None:
        row = self._row(connection_id)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM llm_connection WHERE id = ?", [connection_id])
        self._drop(row["secret_ref"], _header_meta(row["headers"])["ref"])

    def forget_keys(self) -> None:
        """Forget every saved key and header value; connections stay, with `has_key` false."""
        self.secrets.forget_all()
        with self.db.transaction() as conn:
            conn.execute("UPDATE llm_connection SET secret_ref = NULL")
            for r in conn.execute("SELECT id, headers FROM llm_connection").fetchall():
                names = _header_meta(r["headers"])["names"]
                conn.execute(
                    "UPDATE llm_connection SET headers = ? WHERE id = ?",
                    [json.dumps({"names": names, "ref": None}), r["id"]],
                )

    def api_key(self, connection_id: str) -> str | None:
        ref = self._row(connection_id)["secret_ref"]
        if not ref:
            return None
        value = self.secrets.get(ref)
        if value is None:
            raise SecretUnreadable("This saved key is missing. Re-enter it.")
        return value

    def _header_values(self, row: Any) -> dict[str, str]:
        meta = _header_meta(row["headers"])
        if not meta["names"]:
            return {}
        if not meta["ref"]:
            raise SecretUnreadable("The saved header values are missing. Re-enter them.")
        raw = self.secrets.get(meta["ref"])
        if raw is None:
            raise SecretUnreadable("The saved header values are missing. Re-enter them.")
        try:
            values = json.loads(raw)
        except ValueError:
            raise SecretUnreadable(
                "The saved header values can't be read. Re-enter them."
            ) from None
        return {str(k): str(v) for k, v in values.items()} if isinstance(values, dict) else {}

    def acknowledge_notice(self, connection_id: str) -> Connection:
        self._row(connection_id)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE llm_connection SET notice_acknowledged_at = ? WHERE id = ?",
                [to_iso(utcnow()), connection_id],
            )
        return self.get(connection_id)

    def provider(
        self,
        connection_id: str,
        *,
        task: str | None = None,
        redactions: int = 0,
        timeout: float = 30.0,
    ) -> Provider:
        row = self._row(connection_id)
        api_key = self.api_key(connection_id)
        headers = self._header_values(row)
        ctx = CallContext(
            purpose="llm",
            task=task,
            connection_id=connection_id,
            local=bool(row["is_local"]),
            redactions=redactions,
        )
        client = self.client_factory(ctx, timeout)
        preset = PRESETS[row["preset"]]
        return build_provider(
            row["api_style"], client, row["base_url"], api_key, headers, preset.max_tokens_param
        )

    def test(self, connection_id: str) -> list[ModelInfo]:
        row = self._row(connection_id)
        local = bool(row["is_local"])
        provider = self.provider(connection_id, timeout=20.0)
        try:
            listed = provider.list_models()
        finally:
            provider.client.close()
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            for pm in listed:
                existing = conn.execute(
                    "SELECT context_window, source FROM llm_model"
                    " WHERE connection_id = ? AND model_id = ?",
                    [connection_id, pm.id],
                ).fetchone()
                entry = self.catalogue.lookup(pm.id)
                pick = _Picker(pm, entry)
                if pm.context_window:
                    source = "provider"
                else:
                    source = "catalogue" if entry and entry.context_window else "default"
                context = pick(
                    "context_window", DEFAULT_CONTEXT_LOCAL if local else DEFAULT_CONTEXT_CLOUD
                )
                if existing is not None and existing["source"] == "user":
                    context, source = existing["context_window"], "user"
                price_default = 0.0 if local else None
                price_in = 0.0 if local else pick("price_in_usd_per_mtok", price_default)
                price_out = 0.0 if local else pick("price_out_usd_per_mtok", price_default)
                conn.execute(
                    "INSERT INTO llm_model (connection_id, model_id, display_name,"
                    " context_window, max_output_tokens, supports_tools, supports_json_schema,"
                    " supports_vision, price_in_usd_per_mtok, price_out_usd_per_mtok, source,"
                    " fetched_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(connection_id, model_id) DO UPDATE SET"
                    " display_name = excluded.display_name,"
                    " context_window = excluded.context_window,"
                    " max_output_tokens = excluded.max_output_tokens,"
                    " supports_tools = excluded.supports_tools,"
                    " supports_json_schema = excluded.supports_json_schema,"
                    " supports_vision = excluded.supports_vision,"
                    " price_in_usd_per_mtok = excluded.price_in_usd_per_mtok,"
                    " price_out_usd_per_mtok = excluded.price_out_usd_per_mtok,"
                    " source = excluded.source, fetched_at = excluded.fetched_at,"
                    " updated_at = excluded.updated_at, version = llm_model.version + 1",
                    [
                        connection_id,
                        pm.id,
                        pm.display_name,
                        context,
                        pick("max_output_tokens", None),
                        int(bool(pick("supports_tools", False))),
                        int(bool(pick("supports_json_schema", False))),
                        int(bool(pick("supports_vision", False))),
                        price_in,
                        price_out,
                        source,
                        now,
                        now,
                    ],
                )
            listed_ids = [pm.id for pm in listed]
            if listed_ids:
                marks = ",".join("?" for _ in listed_ids)
                conn.execute(
                    f"DELETE FROM llm_model WHERE connection_id = ? AND model_id NOT IN ({marks})",  # noqa: S608
                    [connection_id, *listed_ids],
                )
        return self.models(connection_id)

    def _model_row(self, r: Any) -> ModelInfo:
        return ModelInfo(
            connection_id=r["connection_id"],
            model_id=r["model_id"],
            display_name=r["display_name"],
            context_window=r["context_window"],
            max_output_tokens=r["max_output_tokens"],
            supports_tools=bool(r["supports_tools"]),
            supports_json_schema=bool(r["supports_json_schema"]),
            supports_vision=bool(r["supports_vision"]),
            price_in_usd_per_mtok=r["price_in_usd_per_mtok"],
            price_out_usd_per_mtok=r["price_out_usd_per_mtok"],
            source=r["source"],
            version=r["version"],
        )

    def models(self, connection_id: str | None = None) -> list[ModelInfo]:
        sql, params = "SELECT * FROM llm_model", []
        if connection_id:
            sql += " WHERE connection_id = ?"
            params.append(connection_id)
        with self.db.connection() as conn:
            rows = conn.execute(sql + " ORDER BY connection_id, model_id", params).fetchall()
        return [self._model_row(r) for r in rows]

    def model(self, connection_id: str, model_id: str) -> ModelInfo:
        with self.db.connection() as conn:
            r = conn.execute(
                "SELECT * FROM llm_model WHERE connection_id = ? AND model_id = ?",
                [connection_id, model_id],
            ).fetchone()
        if r is None:
            raise NotFound("llm_model", f"{connection_id}/{model_id}")
        return self._model_row(r)

    def set_context_window(
        self, connection_id: str, model_id: str, value: int, expected_version: int | None = None
    ) -> ModelInfo:
        """Override a model's context window (source `user`). Without `expected_version` the
        write applies to the current row; with it, a stale write raises `VersionConflict`."""
        if value < 512:
            raise InputError("The context window must be at least 512 tokens.")
        version = (
            expected_version
            if expected_version is not None
            else self.model(connection_id, model_id).version
        )
        with self.db.transaction() as conn:
            update_versioned(
                conn,
                "llm_model",
                ("connection_id", "model_id"),
                (connection_id, model_id),
                version,
                {"context_window": value, "source": "user"},
                now=to_iso(utcnow()),
            )
        return self.model(connection_id, model_id)


def _probe(client_factory: ClientFactory, preset: Any) -> int | None:
    from tuppence.llm.providers.openai_compat import OpenAICompatProvider

    client: httpx.Client | None = None
    try:
        client = client_factory(CallContext(purpose="llm", local=True), 0.5)
        return len(OpenAICompatProvider(client, preset.base_url, None).list_models())
    except Exception:  # noqa: BLE001 - detection never raises; a silent server just isn't listed
        return None
    finally:
        if client is not None:
            client.close()


def detect_local(client_factory: ClientFactory) -> list[DetectedServer]:
    found: list[DetectedServer] = []
    for preset in PRESETS.values():
        if preset.kind != "local":
            continue
        count = _probe(client_factory, preset)
        if count is not None:
            found.append(
                DetectedServer(preset=preset.id, base_url=preset.base_url, model_count=count)
            )
    return found
