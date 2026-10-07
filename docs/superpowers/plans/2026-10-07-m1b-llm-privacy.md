# Tuppence M1b — LLM Layer and Privacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plug-and-play AI: any number of LLM connections (local servers and cloud providers with the user's own keys) behind one client that routes each task to a model chain, enforces budgets, retries and falls back safely, gets structured JSON reliably, optionally pseudonymises before cloud calls, honours the "Local only" switch, and logs every outbound call — with Settings › AI, Settings › Privacy and Usage pages, proven end to end against fake provider servers.

**Architecture:** Three wire adapters (OpenAI-compatible, Anthropic, Gemini) share one request/response model. Every outbound HTTP call goes through `tuppence.net.make_client`, whose transport enforces Local only and writes the privacy log. `LLMClient` resolves a task's chain via `TaskRouter`, then for each candidate checks the circuit breaker, cloud notice and budgets, optionally pseudonymises, calls the adapter with retries, records usage and falls back on failure.

**Tech Stack:** Python 3.12, httpx 0.28 (no vendor SDKs, no LiteLLM), pydantic 2, keyring 25, cryptography 50 (Fernet), FastAPI; Svelte 5; Playwright.

**Spec:** `docs/superpowers/specs/2026-10-07-tuppence-design.md` §4 (all), §2 (privacy claim), §14.2 (prompt injection, keys), §16 M1 exit criteria. Builds on M1a (`docs/superpowers/plans/2026-10-07-m1a-foundation-core.md`).

## Global Constraints

- Everything in the M0 and M1a Global Constraints still applies.
- Dependencies for the LLM layer: `httpx` and `pydantic` only (plus `keyring`, `cryptography` for secrets). **No vendor SDKs and no LiteLLM** (spec §4.1, D8).
- Adapters: `openai` (OpenAI-compatible: OpenAI, OpenRouter, Mistral, Groq, Together, xAI, DeepSeek, Qwen, Kimi, GLM, Ollama, LM Studio, llama.cpp, vLLM, Jan, Foundry Local, custom), `anthropic`, `gemini`.
- Anthropic wire format: `POST {base}/v1/messages`, headers `x-api-key`, `anthropic-version: 2023-06-01`; structured output via `output_config: {"format": {"type": "json_schema", "schema": …}}`; **never send `temperature`** (rejected on current models) and **never force `tool_choice`** (rejected on current models); skip `thinking` content blocks; `stop_reason == "refusal"` is a refusal; models via `GET {base}/v1/models` (`max_input_tokens`, `max_tokens`, `capabilities`).
- Tasks: `read`, `categorise`, `review`, `research`, `coach`, `report`, `vision`. Simple mode = one model for everything; advanced = per-task ordered fallback chain; any task can be pinned local-only (spec §4.3).
- Default per-task timeouts (seconds, cloud): read 400, categorise 120, review 120, research 120, coach 60, report 120, vision 300; local connections get 3× (so coach is 180 s local, matching spec §10.1).
- Retries: retryable errors are 429, 5xx, timeouts and connection errors; at most 2 retries; wait `Retry-After` (capped at 30 s) or 1 s then 4 s. Circuit breaker opens after 3 consecutive failures on a connection for 60 s.
- Prompt sizing: input estimate (characters ÷ 4) plus `max_tokens` must fit within the model's context window; inputs above 60% of the window raise `ContextTooLarge` before any call (spec §4.5).
- Budgets: per-run caps (calls, tokens, £, seconds) and a monthly £ cap (`llm.monthly_cap_gbp`, default £10); cost = tokens × catalogue USD price × `llm.usd_to_gbp`; local models cost £0.
- Privacy (spec §4.6): **Local only** (default off) blocks LLM and research calls to anything that isn't local, at the HTTP layer, and logs the block; **cloud notice** shown once per cloud connection before its first chat call; **Pseudonymise** (default off) swaps names/numbers for consistent stand-ins and restores them locally; merchant names, amounts and dates are never masked; **privacy log** records every outbound call (time, purpose, task, connection, host, path without query, bytes out/in, status, redactions, outcome sent/blocked/error) and never stores keys or request bodies.
- "Local" host = loopback, RFC 1918/ULA private, link-local, `100.64.0.0/10` (CGNAT/Tailscale), single-label names (e.g. Docker service `ollama`), or names ending `.local`, `.lan`, `.internal`, `.home.arpa`, `.localhost`. Cloud presets are always treated as cloud even if pointed at a local address.
- Secrets: desktop/local modes use the OS keychain via `keyring` when a real backend exists; otherwise (and always in Docker/server mode) Fernet-encrypted rows in SQLite with the key from `TUPPENCE_SECRET_KEY_FILE` or a generated `secret.key` (mode 0600) in the data folder. API responses never include keys (only `has_key`).
- Model IDs are never invented in code: model lists come from the provider; the bundled catalogue only enriches them with context windows, capabilities and prices.

## Review Focus

1. **Small local model that ignores JSON instructions** (wraps JSON in prose or code fences, or returns invalid JSON) — structured calls must extract, validate, try one repair, then fail with a clear error, never crash or loop. Test in Task 8.
2. **Cloud provider rate limit mid-run** (429 with `Retry-After: 2`) — wait and retry, then fall back to the next model; never hammer. Test in Task 8.
3. **User turns on Local only while a cloud connection is the simple model** — every call is blocked before any byte leaves, the UI explains why, and the privacy log shows the block. Test in Task 8 and Task 11.
4. **Pasted base URL variants** (`https://api.openai.com`, `…/v1/`, trailing spaces) — normalised so the connection still works. Test in Task 6.
5. **Huge prompt for a small local model** (Ollama default 4,096-token context) — fail fast with `ContextTooLarge` explaining the model is too small, rather than silently truncating. Test in Task 8.

---

## File Structure

```
src/tuppence/net/__init__.py
src/tuppence/net/hosts.py              is_local_host()
src/tuppence/net/privacy_log.py        PrivacyLog, PrivacyEvent
src/tuppence/net/client.py             CallContext, LocalOnlyBlocked, GuardedTransport, make_client()
src/tuppence/core/secrets.py           SecretStore, KeyringStore, EncryptedDbStore, choose_secret_store()
src/tuppence/core/migrations/0005_privacy.sql   privacy_log
src/tuppence/core/migrations/0006_llm.sql       llm_connection, llm_model, llm_route, llm_usage, secret
src/tuppence/llm/__init__.py
src/tuppence/llm/types.py              Message, ToolSpec, ToolCall, ChatRequest, ChatResponse, Usage, ProviderModel, errors
src/tuppence/llm/jsonextract.py        extract_json(), to_strict_schema()
src/tuppence/llm/providers/__init__.py build_provider()
src/tuppence/llm/providers/base.py     post_json(), get_json(), parse_retry_after()
src/tuppence/llm/providers/openai_compat.py
src/tuppence/llm/providers/anthropic.py
src/tuppence/llm/providers/gemini.py
src/tuppence/llm/presets.py            Preset, PRESETS
src/tuppence/llm/catalogue.py          CatalogueEntry, ModelCatalogue, normalise_model_id()
src/tuppence/datapacks/__init__.py
src/tuppence/datapacks/baseline/model-catalogue.json
scripts/build_model_catalogue.py       regenerates the baseline JSON from OpenRouter's public list
src/tuppence/llm/connections.py        Connection, ModelInfo, ConnectionRegistry, detect_local()
src/tuppence/llm/pseudonymise.py       Pseudonymiser
src/tuppence/llm/routing.py            TaskRouter, RoutingView
src/tuppence/llm/budget.py             RunBudget, UsageLedger, BreakerBoard, estimate_tokens(), cost_gbp()
src/tuppence/llm/client.py             LLMClient (chat, structured)
src/tuppence/app/routes/llm.py, privacy.py, usage.py
web/src/pages/settings/{AI,Privacy}.svelte, web/src/pages/Usage.svelte, web/src/components/Modal.svelte
tests/fakes/fake_llm.py                fake OpenAI/Anthropic/Gemini server (FastAPI) + ServerThread helper
tests/net/*, tests/llm/*, tests/app/test_llm_api.py, tests/live/test_live_providers.py
web/e2e/04-ai-privacy.spec.ts
```

---

### Task 1: Outbound client, Local only guard and the privacy log

**Files:**
- Create: `src/tuppence/net/__init__.py`, `src/tuppence/net/hosts.py`, `src/tuppence/net/privacy_log.py`, `src/tuppence/net/client.py`, `src/tuppence/core/migrations/0005_privacy.sql`, `src/tuppence/app/routes/privacy.py`
- Modify: `src/tuppence/app/services.py` (add `privacy_log: PrivacyLog`), `src/tuppence/app/routes/__init__.py`
- Test: `tests/net/__init__.py`, `tests/net/test_hosts.py`, `tests/net/test_client.py`, `tests/app/test_privacy_api.py`

**Interfaces:**
- Produces:
  - `is_local_host(host: str, *, resolve: Callable[[str], list[str]] | None = None) -> bool`
  - `PrivacyEvent` (pydantic): `ts`, `purpose: Literal["llm","research","market","datapack"]`, `task: str | None`, `connection_id: str | None`, `destination: str`, `method: str`, `path: str`, `bytes_out: int`, `bytes_in: int`, `status: int | None`, `redactions: int`, `outcome: Literal["sent","blocked","error"]`, `note: str | None`; `PrivacyLog(db)` with `record(event) -> None`, `list(limit=100, before_id=None) -> list[PrivacyLogEntry]` (`PrivacyLogEntry` = event + `id`)
  - `CallContext` (dataclass): `purpose`, `task: str | None = None`, `connection_id: str | None = None`, `local: bool = False`, `redactions: int = 0`
  - `LocalOnlyBlocked(Exception)` with `.host`
  - `make_client(ctx, *, privacy_log, local_only: Callable[[], bool], timeout: float, transport: httpx.BaseTransport | None = None) -> httpx.Client`
  - Route `GET /api/privacy/log?limit=100&before_id=` → `{"entries": [...]}` (protected)

- [ ] **Step 1: Write the failing tests**

`tests/net/test_hosts.py`:

```python
import pytest

from tuppence.net.hosts import is_local_host


@pytest.mark.parametrize("host", [
    "127.0.0.1", "localhost", "::1", "[::1]", "192.168.1.20", "10.0.0.5", "172.16.4.4", "100.100.1.1",
    "ollama", "gpu-box.local", "nas.lan", "host.docker.internal", "router.home.arpa", "fe80::1",
])
def test_local_hosts(host):
    assert is_local_host(host, resolve=lambda h: pytest.fail(f"should not resolve {h}"))


@pytest.mark.parametrize("host", ["8.8.8.8", "2606:4700::1111"])
def test_public_ips(host):
    assert not is_local_host(host)


def test_dns_names_resolve_and_must_all_be_private():
    assert is_local_host("box.example.com", resolve=lambda h: ["192.168.1.9"])
    assert not is_local_host("api.openai.com", resolve=lambda h: ["104.18.1.1"])
    assert not is_local_host("mixed.example.com", resolve=lambda h: ["192.168.1.9", "104.18.1.1"])
    assert not is_local_host("nxdomain.example.com", resolve=lambda h: [])
```

`tests/net/test_client.py`:

```python
import httpx
import pytest

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.net.client import CallContext, LocalOnlyBlocked, make_client
from tuppence.net.privacy_log import PrivacyLog


@pytest.fixture
def log(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return PrivacyLog(db)


def ok_transport():
    return httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))


def test_logs_sent_calls_without_query_or_body(log):
    ctx = CallContext(purpose="llm", task="coach", connection_id="c1", local=False, redactions=2)
    with make_client(ctx, privacy_log=log, local_only=lambda: False, timeout=5, transport=ok_transport()) as c:
        r = c.post("https://api.example.com/v1/chat?key=SECRET", json={"hello": "world"})
    assert r.status_code == 200
    [e] = log.list()
    assert (e.purpose, e.task, e.connection_id, e.destination, e.path, e.outcome, e.redactions) == (
        "llm", "coach", "c1", "api.example.com", "/v1/chat", "sent", 2)
    assert e.bytes_out > 0 and e.bytes_in > 0 and e.status == 200
    assert "SECRET" not in e.model_dump_json()


def test_local_only_blocks_cloud_and_logs_block(log):
    ctx = CallContext(purpose="llm", local=False)
    with make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()) as c:
        with pytest.raises(LocalOnlyBlocked):
            c.get("https://api.example.com/v1/models")
    [e] = log.list()
    assert e.outcome == "blocked" and e.bytes_out == 0 and "Local only" in e.note


def test_local_only_allows_local_connection_on_local_host(log):
    ctx = CallContext(purpose="llm", local=True)
    with make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()) as c:
        assert c.get("http://127.0.0.1:11434/v1/models").status_code == 200


def test_local_flag_alone_is_not_enough_for_public_host(log):
    ctx = CallContext(purpose="llm", local=True)
    with make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()) as c:
        with pytest.raises(LocalOnlyBlocked):
            c.get("http://8.8.8.8/v1/models")


def test_market_data_not_affected_by_local_only(log):
    ctx = CallContext(purpose="market")
    with make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()) as c:
        assert c.get("https://api.example.org/rates").status_code == 200


def test_transport_errors_are_logged_and_reraised(log):
    def boom(req):
        raise httpx.ConnectError("refused", request=req)

    ctx = CallContext(purpose="llm")
    with make_client(ctx, privacy_log=log, local_only=lambda: False, timeout=5, transport=httpx.MockTransport(boom)) as c:
        with pytest.raises(httpx.ConnectError):
            c.get("https://api.example.com/x")
    [e] = log.list()
    assert e.outcome == "error" and "refused" in e.note
```

`tests/app/test_privacy_api.py`:

```python
def test_privacy_log_endpoint(client):
    services = client.app.state.services
    from tuppence.net.privacy_log import PrivacyEvent

    services.privacy_log.record(PrivacyEvent(
        ts="2026-10-07T00:00:00Z", purpose="llm", task="coach", connection_id=None, destination="api.example.com",
        method="POST", path="/v1/chat", bytes_out=10, bytes_in=20, status=200, redactions=0, outcome="sent", note=None))
    r = client.get("/api/privacy/log")
    assert r.status_code == 200 and r.json()["entries"][0]["destination"] == "api.example.com"


def test_privacy_log_requires_sign_in(anon_client):
    assert anon_client.get("/api/privacy/log").status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/net tests/app/test_privacy_api.py -q` → FAIL (modules missing).

- [ ] **Step 3: Implement**

`src/tuppence/core/migrations/0005_privacy.sql`:

```sql
CREATE TABLE privacy_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('llm', 'research', 'market', 'datapack')),
  task TEXT,
  connection_id TEXT,
  destination TEXT NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  bytes_out INTEGER NOT NULL DEFAULT 0,
  bytes_in INTEGER NOT NULL DEFAULT 0,
  status INTEGER,
  redactions INTEGER NOT NULL DEFAULT 0,
  outcome TEXT NOT NULL CHECK (outcome IN ('sent', 'blocked', 'error')),
  note TEXT
);
CREATE INDEX ix_privacy_log_ts ON privacy_log (ts);
```

`src/tuppence/net/hosts.py`:

```python
"""Is a host 'local' (this device or the user's own network)? Spec §4.6."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable

_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localhost")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _system_resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []


def _private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 4 and ip in _CGNAT:
        return True
    return ip.is_loopback or ip.is_private or ip.is_link_local


def is_local_host(host: str, *, resolve: Callable[[str], list[str]] | None = None) -> bool:
    h = host.strip().strip("[]").lower().rstrip(".")
    if not h:
        return False
    if h == "localhost" or h.endswith(_SUFFIXES):
        return True
    try:
        ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        if "." not in h:
            return True  # single-label names: Docker services, hosts on the LAN
        addresses = (resolve or _system_resolve)(h)
        return bool(addresses) and all(_private(a) for a in addresses)
    return _private(h)
```

`src/tuppence/net/privacy_log.py`:

```python
"""Every outbound call, so users can see exactly what left their machine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tuppence.core.db import Database

Purpose = Literal["llm", "research", "market", "datapack"]


class PrivacyEvent(BaseModel):
    ts: str
    purpose: Purpose
    task: str | None
    connection_id: str | None
    destination: str
    method: str
    path: str
    bytes_out: int
    bytes_in: int
    status: int | None
    redactions: int
    outcome: Literal["sent", "blocked", "error"]
    note: str | None


class PrivacyLogEntry(PrivacyEvent):
    id: int


_COLS = list(PrivacyEvent.model_fields)


class PrivacyLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, event: PrivacyEvent) -> None:
        data = event.model_dump()
        with self.db.transaction() as conn:
            conn.execute(
                f"INSERT INTO privacy_log ({', '.join(_COLS)}) VALUES ({', '.join('?' for _ in _COLS)})",  # noqa: S608
                [data[c] for c in _COLS],
            )

    def list(self, limit: int = 100, before_id: int | None = None) -> list[PrivacyLogEntry]:
        sql, params = "SELECT * FROM privacy_log", []
        if before_id is not None:
            sql += " WHERE id < ?"
            params.append(before_id)
        with self.db.connection() as conn:
            rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", [*params, min(limit, 500)]).fetchall()
        return [PrivacyLogEntry(**{k: r[k] for k in PrivacyLogEntry.model_fields}) for r in rows]
```

`src/tuppence/net/client.py`:

```python
"""The only way Tuppence talks to the outside world (spec §4.6)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from tuppence.core.clock import to_iso, utcnow
from tuppence.net.hosts import is_local_host
from tuppence.net.privacy_log import PrivacyEvent, PrivacyLog, Purpose

GUARDED_PURPOSES = {"llm", "research"}


class LocalOnlyBlocked(Exception):
    def __init__(self, host: str) -> None:
        super().__init__(f"Local only is on, so Tuppence didn't contact {host}.")
        self.host = host


@dataclass
class CallContext:
    purpose: Purpose
    task: str | None = None
    connection_id: str | None = None
    local: bool = False
    redactions: int = 0


class GuardedTransport(httpx.BaseTransport):
    def __init__(
        self, inner: httpx.BaseTransport, ctx: CallContext, log: PrivacyLog, local_only: Callable[[], bool]
    ) -> None:
        self.inner, self.ctx, self.log, self.local_only = inner, ctx, log, local_only

    def _event(self, request: httpx.Request, **kw: object) -> PrivacyEvent:
        base = {
            "ts": to_iso(utcnow()), "purpose": self.ctx.purpose, "task": self.ctx.task,
            "connection_id": self.ctx.connection_id, "destination": request.url.host, "method": request.method,
            "path": request.url.path, "bytes_out": 0, "bytes_in": 0, "status": None,
            "redactions": self.ctx.redactions, "outcome": "sent", "note": None,
        }
        base.update(kw)
        return PrivacyEvent.model_validate(base)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if self.ctx.purpose in GUARDED_PURPOSES and self.local_only():
            if not (self.ctx.local and is_local_host(host)):
                self.log.record(self._event(request, outcome="blocked", note="Local only is on"))
                raise LocalOnlyBlocked(host)
        body = request.read()
        try:
            response = self.inner.handle_request(request)
            response.read()
        except Exception as exc:
            self.log.record(self._event(request, bytes_out=len(body), outcome="error", note=f"{type(exc).__name__}: {exc}"[:300]))
            raise
        self.log.record(
            self._event(request, bytes_out=len(body), bytes_in=len(response.content), status=response.status_code)
        )
        return response

    def close(self) -> None:
        self.inner.close()


def make_client(
    ctx: CallContext,
    *,
    privacy_log: PrivacyLog,
    local_only: Callable[[], bool],
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    inner = transport or httpx.HTTPTransport(retries=0)
    return httpx.Client(
        transport=GuardedTransport(inner, ctx, privacy_log, local_only),
        timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
        follow_redirects=False,
        trust_env=not ctx.local,
    )
```

`src/tuppence/app/routes/privacy.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.net.privacy_log import PrivacyLogEntry

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


@router.get("/log")
def privacy_log(limit: int = 100, before_id: int | None = None, services: Services = Depends(get_services)) -> dict[str, list[PrivacyLogEntry]]:
    return {"entries": services.privacy_log.list(limit=limit, before_id=before_id)}
```

Add `privacy_log: PrivacyLog` to `Services`; add `privacy.router` to `PROTECTED`.

- [ ] **Step 4: Run tests, lint, pyright; commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add -A && git commit -m "Add guarded outbound client with Local only switch and privacy log"
```

---

### Task 2: Secret storage (OS keychain or encrypted database)

**Files:**
- Create: `src/tuppence/core/secrets.py`, `src/tuppence/core/migrations/0006_llm.sql` (all LLM tables; later tasks use them)
- Modify: `pyproject.toml` (`uv add keyring cryptography`), `src/tuppence/app/services.py` (add `secrets: SecretStore`)
- Test: `tests/core/test_secrets.py`

**Interfaces:**
- Produces:
  - `SecretStore` (Protocol): `put(value: str, ref: str | None = None) -> str`, `get(ref: str) -> str | None`, `delete(ref: str) -> None`, attribute `kind: str`
  - `KeyringStore(service="Tuppence")` (`kind="keychain"`), `EncryptedDbStore(db, key: bytes)` (`kind="encrypted-db"`)
  - `load_or_create_key(data_dir: Path, env: Mapping[str, str] = os.environ) -> bytes`
  - `keyring_usable() -> bool`
  - `choose_secret_store(mode: str, db: Database, data_dir: Path) -> SecretStore`
  - Tables in `0006_llm.sql`: `llm_connection`, `llm_model`, `llm_route`, `llm_usage`, `secret` (DDL below)

- [ ] **Step 1: Write the failing tests**

`tests/core/test_secrets.py`:

```python
import os
import stat
import sys

import keyring
import pytest
from keyring.backend import KeyringBackend

from tuppence.core import secrets as sec
from tuppence.core.db import Database
from tuppence.core.migrate import migrate


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.data = {}

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def get_password(self, service, username):
        return self.data.get((service, username))

    def delete_password(self, service, username):
        self.data.pop((service, username), None)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    migrate(d, tmp_path / "b")
    return d


def test_encrypted_db_roundtrip_and_ciphertext(db, tmp_path):
    store = sec.EncryptedDbStore(db, sec.load_or_create_key(tmp_path, env={}))
    ref = store.put("sk-test-123")
    assert store.get(ref) == "sk-test-123"
    with db.connection() as conn:
        blob = conn.execute("SELECT ciphertext FROM secret WHERE ref = ?", [ref]).fetchone()[0]
    assert b"sk-test-123" not in blob
    store.put("sk-new", ref)
    assert store.get(ref) == "sk-new"
    store.delete(ref)
    assert store.get(ref) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_generated_key_file_is_private(tmp_path):
    sec.load_or_create_key(tmp_path, env={})
    mode = stat.S_IMODE(os.stat(tmp_path / "secret.key").st_mode)
    assert mode == 0o600
    assert sec.load_or_create_key(tmp_path, env={}) == (tmp_path / "secret.key").read_bytes().strip()


def test_key_file_from_env(tmp_path):
    from cryptography.fernet import Fernet

    keyfile = tmp_path / "docker-secret"
    keyfile.write_bytes(Fernet.generate_key())
    assert sec.load_or_create_key(tmp_path / "data", env={"TUPPENCE_SECRET_KEY_FILE": str(keyfile)}) == keyfile.read_bytes().strip()


def test_keyring_store_roundtrip():
    keyring.set_keyring(MemoryKeyring())
    store = sec.KeyringStore()
    ref = store.put("abc")
    assert store.get(ref) == "abc"
    store.delete(ref)
    assert store.get(ref) is None


def test_choose_store(db, tmp_path, monkeypatch):
    monkeypatch.setattr(sec, "keyring_usable", lambda: True)
    assert sec.choose_secret_store("desktop", db, tmp_path).kind == "keychain"
    assert sec.choose_secret_store("server", db, tmp_path).kind == "encrypted-db"
    monkeypatch.setattr(sec, "keyring_usable", lambda: False)
    assert sec.choose_secret_store("local", db, tmp_path).kind == "encrypted-db"
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

`src/tuppence/core/migrations/0006_llm.sql`:

```sql
CREATE TABLE llm_connection (
  id TEXT PRIMARY KEY,
  preset TEXT NOT NULL,
  name TEXT NOT NULL,
  api_style TEXT NOT NULL CHECK (api_style IN ('openai', 'anthropic', 'gemini')),
  base_url TEXT NOT NULL,
  secret_ref TEXT,
  headers TEXT NOT NULL DEFAULT '{}',
  is_local INTEGER NOT NULL,
  notice_acknowledged_at TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE llm_model (
  connection_id TEXT NOT NULL REFERENCES llm_connection(id) ON DELETE CASCADE,
  model_id TEXT NOT NULL,
  display_name TEXT,
  context_window INTEGER NOT NULL,
  max_output_tokens INTEGER,
  supports_tools INTEGER NOT NULL DEFAULT 0,
  supports_json_schema INTEGER NOT NULL DEFAULT 0,
  supports_vision INTEGER NOT NULL DEFAULT 0,
  price_in_usd_per_mtok REAL,
  price_out_usd_per_mtok REAL,
  source TEXT NOT NULL CHECK (source IN ('provider', 'catalogue', 'default', 'user')),
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (connection_id, model_id)
);

CREATE TABLE llm_route (
  task TEXT PRIMARY KEY CHECK (task IN ('read', 'categorise', 'review', 'research', 'coach', 'report', 'vision')),
  chain TEXT NOT NULL DEFAULT '[]',
  local_only INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE llm_usage (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  run_id TEXT,
  task TEXT NOT NULL,
  connection_id TEXT,
  model_id TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_gbp REAL,
  ok INTEGER NOT NULL,
  error TEXT
);
CREATE INDEX ix_llm_usage_ts ON llm_usage (ts);

CREATE TABLE secret (
  ref TEXT PRIMARY KEY,
  ciphertext BLOB NOT NULL,
  created_at TEXT NOT NULL
);
```

`src/tuppence/core/secrets.py`:

```python
"""API keys: OS keychain on desktops, encrypted database rows elsewhere (spec §4.6)."""

from __future__ import annotations

import os
import secrets as pysecrets
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database

SERVICE = "Tuppence"


class SecretStore(Protocol):
    kind: str

    def put(self, value: str, ref: str | None = None) -> str: ...
    def get(self, ref: str) -> str | None: ...
    def delete(self, ref: str) -> None: ...


def _new_ref() -> str:
    return "sec_" + pysecrets.token_hex(8)


class KeyringStore:
    kind = "keychain"

    def __init__(self, service: str = SERVICE) -> None:
        self.service = service

    def put(self, value: str, ref: str | None = None) -> str:
        import keyring

        ref = ref or _new_ref()
        keyring.set_password(self.service, ref, value)
        return ref

    def get(self, ref: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, ref)

    def delete(self, ref: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.service, ref)
        except PasswordDeleteError:
            pass


class EncryptedDbStore:
    kind = "encrypted-db"

    def __init__(self, db: Database, key: bytes) -> None:
        self.db = db
        self.fernet = Fernet(key)

    def put(self, value: str, ref: str | None = None) -> str:
        ref = ref or _new_ref()
        token = self.fernet.encrypt(value.encode())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO secret (ref, ciphertext, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT(ref) DO UPDATE SET ciphertext = excluded.ciphertext",
                [ref, token, to_iso(utcnow())],
            )
        return ref

    def get(self, ref: str) -> str | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT ciphertext FROM secret WHERE ref = ?", [ref]).fetchone()
        if row is None:
            return None
        try:
            return self.fernet.decrypt(bytes(row[0])).decode()
        except InvalidToken:
            return None

    def delete(self, ref: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM secret WHERE ref = ?", [ref])


def load_or_create_key(data_dir: Path, env: Mapping[str, str] = os.environ) -> bytes:
    configured = env.get("TUPPENCE_SECRET_KEY_FILE")
    if configured:
        return Path(configured).read_bytes().strip()
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_bytes().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def keyring_usable() -> bool:
    try:
        import keyring
        from keyring.backends import chainer, fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return False
        if isinstance(backend, chainer.ChainerBackend) and not backend.backends:
            return False
        backend.get_password(SERVICE, "__probe__")
    except Exception:  # noqa: BLE001 - any keyring failure means "don't use it"
        return False
    return True


def choose_secret_store(mode: str, db: Database, data_dir: Path) -> SecretStore:
    if mode in ("local", "desktop") and keyring_usable():
        return KeyringStore()
    return EncryptedDbStore(db, load_or_create_key(data_dir))
```

Wire `secrets=choose_secret_store(runtime.mode, db, paths.root)` into `Services`.

- [ ] **Step 4: Tests, lint, pyright, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add -A && git commit -m "Add secret storage: OS keychain or Fernet-encrypted database rows"
```

---

### Task 3: LLM types, JSON helpers and the OpenAI-compatible adapter

**Files:**
- Create: `src/tuppence/llm/__init__.py`, `src/tuppence/llm/types.py`, `src/tuppence/llm/jsonextract.py`, `src/tuppence/llm/providers/__init__.py`, `src/tuppence/llm/providers/base.py`, `src/tuppence/llm/providers/openai_compat.py`
- Test: `tests/llm/__init__.py`, `tests/llm/test_jsonextract.py`, `tests/llm/test_openai_compat.py`

**Interfaces:**
- Produces (`tuppence.llm.types`):
  - `Role = Literal["system","user","assistant","tool"]`
  - `ToolCall(id: str, name: str, arguments: dict[str, Any])`
  - `Message(role, content: str = "", tool_calls: list[ToolCall] = [], tool_call_id: str | None = None, name: str | None = None)` — for `role="tool"`, `name` is the tool's name and `tool_call_id` the call it answers
  - `ToolSpec(name, description, parameters: dict)` (JSON Schema)
  - `ChatRequest(model, messages, tools=[], json_schema: dict | None = None, schema_name="result", max_tokens=4096, temperature: float | None = 0.0)`
  - `Usage(input_tokens=0, output_tokens=0)`; `ChatResponse(text: str, tool_calls: list[ToolCall] = [], usage: Usage, model: str, finish_reason: str | None = None)`
  - `ProviderModel(id, display_name: str | None = None, context_window: int | None = None, max_output_tokens: int | None = None, supports_tools: bool | None = None, supports_json_schema: bool | None = None, supports_vision: bool | None = None, price_in_usd_per_mtok: float | None = None, price_out_usd_per_mtok: float | None = None)`
  - Errors: `LLMError(message, retryable=False)`; `LLMHTTPError(status, body, retry_after)` (retryable when 429 or ≥500); `LLMConnectionError` (retryable); `LLMTimeout` (retryable); `LLMRefused`; `LLMBadResponse`; `NoticeRequired`; `BudgetExceeded`; `ContextTooLarge`; `NoModelConfigured`; `AllModelsFailed(attempts: list[str])`
  - `Provider` Protocol: `api_style: str`, `client: httpx.Client` (callers close it when done), `list_models() -> list[ProviderModel]`, `chat(req: ChatRequest) -> ChatResponse`
- Produces (`tuppence.llm.jsonextract`): `extract_json(text: str) -> Any` (raises `ValueError`), `to_strict_schema(schema: dict) -> dict` (inlines `$defs`/`$ref`, sets `additionalProperties: false` and `required` = all properties on every object, drops `title`/`default`)
- Produces (`tuppence.llm.providers.base`): `post_json(client, url, *, headers, body) -> dict`, `get_json(client, url, *, headers, params=None) -> dict` (map `httpx.TimeoutException` → `LLMTimeout`, `httpx.TransportError` → `LLMConnectionError`, non-2xx → `LLMHTTPError` with body truncated to 500 chars and parsed `Retry-After`, invalid JSON → `LLMBadResponse`; `LocalOnlyBlocked` propagates unchanged); `parse_retry_after(value: str | None) -> float | None`; `join_url(base, path) -> str`
- Produces: `OpenAICompatProvider(client, base_url, api_key, *, extra_headers=None, max_tokens_param="max_tokens")`

- [ ] **Step 1: Write the failing tests**

`tests/llm/test_jsonextract.py`:

```python
import pytest

from tuppence.llm.jsonextract import extract_json, to_strict_schema


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('Sure! Here it is:\n```json\n{"a": [1, 2]}\n```\nAnything else?', {"a": [1, 2]}),
    ('noise [1, {"b": "x}"}] trailing', [1, {"b": "x}"}]),
    ('<think>hmm {not json}</think>{"ok": true}', {"ok": True}),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_raises_when_absent():
    with pytest.raises(ValueError):
        extract_json("no json at all")


def test_to_strict_schema_inlines_refs_and_requires_all():
    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        qty: int = 1

    class Order(BaseModel):
        items: list[Item]
        note: str | None = None

    s = to_strict_schema(Order.model_json_schema())
    assert "$defs" not in s and "$ref" not in str(s)
    assert s["additionalProperties"] is False and set(s["required"]) == {"items", "note"}
    item = s["properties"]["items"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == {"name", "qty"}
    assert "title" not in s and "default" not in str(item)
```

`tests/llm/test_openai_compat.py`:

```python
import json

import httpx
import pytest

from tuppence.llm.providers.openai_compat import OpenAICompatProvider
from tuppence.llm.types import ChatRequest, LLMBadResponse, LLMHTTPError, LLMTimeout, Message, ToolCall, ToolSpec


def make(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(client, "https://api.example.com/v1", "sk-test", **kw)


def test_chat_request_shape_and_parse():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "model": "m1",
            "choices": [{"message": {"content": "hi", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": '{"q": "tesco"}'}}]},
                "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        })

    p = make(handler, max_tokens_param="max_completion_tokens")
    req = ChatRequest(
        model="m1",
        messages=[
            Message(role="system", content="sys"),
            Message(role="user", content="hello"),
            Message(role="assistant", content="", tool_calls=[ToolCall(id="c0", name="lookup", arguments={"q": "a"})]),
            Message(role="tool", content='{"r": 1}', tool_call_id="c0", name="lookup"),
        ],
        tools=[ToolSpec(name="lookup", description="Look up", parameters={"type": "object", "properties": {}})],
        json_schema={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False},
        max_tokens=100,
    )
    r = p.chat(req)
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["max_completion_tokens"] == 100 and "max_tokens" not in body
    assert body["messages"][2]["tool_calls"][0]["function"] == {"name": "lookup", "arguments": '{"q": "a"}'}
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c0", "content": '{"r": 1}'}
    assert body["tools"][0]["type"] == "function"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert r.text == "hi" and r.tool_calls == [ToolCall(id="c1", name="lookup", arguments={"q": "tesco"})]
    assert (r.usage.input_tokens, r.usage.output_tokens, r.finish_reason) == (12, 3, "tool_calls")


def test_keyless_local_server_sends_no_auth_header():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    OpenAICompatProvider(client, "http://127.0.0.1:11434/v1", None).chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert seen["auth"] is None


def test_list_models_with_openrouter_extras():
    def handler(req):
        assert str(req.url) == "https://api.example.com/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "plain-model"},
            {"id": "vendor/rich", "name": "Rich", "context_length": 128000,
             "pricing": {"prompt": "0.000002", "completion": "0.000008"},
             "supported_parameters": ["tools", "response_format", "structured_outputs"],
             "architecture": {"input_modalities": ["text", "image"]}},
        ]})

    models = make(handler).list_models()
    assert models[0].id == "plain-model" and models[0].context_window is None
    rich = models[1]
    assert (rich.context_window, rich.price_in_usd_per_mtok, rich.price_out_usd_per_mtok) == (128000, 2.0, 8.0)
    assert rich.supports_tools and rich.supports_json_schema and rich.supports_vision


def test_errors_are_mapped():
    p = make(lambda req: httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "slow down"}))
    with pytest.raises(LLMHTTPError) as exc:
        p.chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert exc.value.status == 429 and exc.value.retryable and exc.value.retry_after == 2.0

    def timeout(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(LLMTimeout):
        make(timeout).chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))

    with pytest.raises(LLMBadResponse):
        make(lambda req: httpx.Response(200, text="<html>")).chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))

    bad_args = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "f", "arguments": "{not json"}}]}}]}
    with pytest.raises(LLMBadResponse):
        make(lambda req: httpx.Response(200, json=bad_args)).chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))

    p400 = make(lambda req: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(LLMHTTPError) as exc400:
        p400.chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert not exc400.value.retryable
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

`src/tuppence/llm/types.py`:

```python
"""Provider-neutral chat types and errors."""

from __future__ import annotations

from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    schema_name: str = "result"
    max_tokens: int = 4096
    temperature: float | None = 0.0


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ChatResponse(BaseModel):
    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str
    finish_reason: str | None = None


class ProviderModel(BaseModel):
    id: str
    display_name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool | None = None
    supports_json_schema: bool | None = None
    supports_vision: bool | None = None
    price_in_usd_per_mtok: float | None = None
    price_out_usd_per_mtok: float | None = None


class Provider(Protocol):
    api_style: str
    client: httpx.Client

    def list_models(self) -> list[ProviderModel]: ...
    def chat(self, req: ChatRequest) -> ChatResponse: ...


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMHTTPError(LLMError):
    def __init__(self, status: int, body: str, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}: {body}", retryable=status == 429 or status >= 500)
        self.status, self.body, self.retry_after = status, body, retry_after


class LLMConnectionError(LLMError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class LLMTimeout(LLMError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class LLMRefused(LLMError):
    pass


class LLMBadResponse(LLMError):
    pass


class NoticeRequired(LLMError):
    pass


class BudgetExceeded(LLMError):
    pass


class ContextTooLarge(LLMError):
    pass


class NoModelConfigured(LLMError):
    pass


class AllModelsFailed(LLMError):
    def __init__(self, attempts: list[str]) -> None:
        super().__init__("No AI model could answer: " + "; ".join(attempts))
        self.attempts = attempts
```

`src/tuppence/llm/jsonextract.py`:

```python
"""Find JSON in model output; make schemas acceptable to strict structured-output modes."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any:
    text = _THINK.sub("", text)
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    decoder = json.JSONDecoder()
    for chunk in candidates:
        for i, ch in enumerate(chunk):
            if ch in "{[":
                try:
                    value, _ = decoder.raw_decode(chunk[i:])
                except json.JSONDecodeError:
                    continue
                return value
    raise ValueError("No JSON found in the model's reply")


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            return _resolve(copy.deepcopy(defs[name]), defs)
        out = {k: _resolve(v, defs) for k, v in node.items() if k not in ("$defs", "title", "default")}
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out
    if isinstance(node, list):
        return [_resolve(v, defs) for v in node]
    return node


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return _resolve(schema, schema.get("$defs", {}))
```

`src/tuppence/llm/providers/base.py`:

```python
"""HTTP helpers shared by adapters: one error vocabulary for all providers."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from tuppence.llm.types import LLMBadResponse, LLMConnectionError, LLMHTTPError, LLMTimeout


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _send(client: httpx.Client, method: str, url: str, **kw: Any) -> dict[str, Any]:
    try:
        response = client.request(method, url, **kw)
    except httpx.TimeoutException as exc:
        raise LLMTimeout(f"Timed out talking to {httpx.URL(url).host}") from exc
    except httpx.TransportError as exc:
        raise LLMConnectionError(f"Couldn't reach {httpx.URL(url).host}: {exc}") from exc
    if response.status_code >= 400:
        raise LLMHTTPError(response.status_code, response.text[:500], parse_retry_after(response.headers.get("retry-after")))
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMBadResponse(f"{httpx.URL(url).host} returned something that isn't JSON") from exc
    if not isinstance(data, dict):
        raise LLMBadResponse("Unexpected response shape")
    return data


def post_json(client: httpx.Client, url: str, *, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    return _send(client, "POST", url, headers=headers, json=body)


def get_json(client: httpx.Client, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None) -> dict[str, Any]:
    return _send(client, "GET", url, headers=headers, params=params)
```

`src/tuppence/llm/providers/openai_compat.py`:

```python
"""OpenAI-compatible Chat Completions — covers most providers and every local server."""

from __future__ import annotations

import json
from typing import Any

import httpx

from tuppence.llm.providers.base import get_json, join_url, post_json
from tuppence.llm.types import ChatRequest, ChatResponse, LLMBadResponse, Message, ProviderModel, ToolCall, Usage


def _message(m: Message) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in m.tool_calls
        ]
    return out


def _per_mtok(value: Any) -> float | None:
    try:
        return round(float(value) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


class OpenAICompatProvider:
    api_style = "openai"

    def __init__(
        self, client: httpx.Client, base_url: str, api_key: str | None, *,
        extra_headers: dict[str, str] | None = None, max_tokens_param: str = "max_tokens",
    ) -> None:
        self.client, self.base_url = client, base_url
        self.headers = {"Accept": "application/json", **(extra_headers or {})}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self.max_tokens_param = max_tokens_param

    def list_models(self) -> list[ProviderModel]:
        data = get_json(self.client, join_url(self.base_url, "models"), headers=self.headers)
        models: list[ProviderModel] = []
        for item in data.get("data", []):
            params = set(item.get("supported_parameters") or [])
            modalities = set((item.get("architecture") or {}).get("input_modalities") or [])
            pricing = item.get("pricing") or {}
            models.append(ProviderModel(
                id=item["id"], display_name=item.get("name"), context_window=item.get("context_length"),
                supports_tools=("tools" in params) if params else None,
                supports_json_schema=bool(params & {"response_format", "structured_outputs"}) if params else None,
                supports_vision=("image" in modalities) if modalities else None,
                price_in_usd_per_mtok=_per_mtok(pricing.get("prompt")),
                price_out_usd_per_mtok=_per_mtok(pricing.get("completion")),
            ))
        return models

    def chat(self, req: ChatRequest) -> ChatResponse:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [_message(m) for m in req.messages],
            self.max_tokens_param: req.max_tokens,
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in req.tools
            ]
        if req.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": req.schema_name, "schema": req.json_schema, "strict": True},
            }
        data = post_json(self.client, join_url(self.base_url, "chat/completions"), headers=self.headers, body=body)
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBadResponse("Response had no choices") from exc
        calls: list[ToolCall] = []
        for i, raw in enumerate(message.get("tool_calls") or []):
            fn = raw.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise LLMBadResponse(f"Tool call arguments weren't valid JSON for {fn.get('name')}") from exc
            calls.append(ToolCall(id=raw.get("id") or f"call_{i}", name=fn.get("name", ""), arguments=args))
        usage = data.get("usage") or {}
        return ChatResponse(
            text=message.get("content") or "", tool_calls=calls, model=data.get("model") or req.model,
            usage=Usage(input_tokens=usage.get("prompt_tokens", 0) or 0, output_tokens=usage.get("completion_tokens", 0) or 0),
            finish_reason=choice.get("finish_reason"),
        )
```

`src/tuppence/llm/providers/__init__.py` — leave a placeholder `build_provider` raising `NotImplementedError` until Task 6 wires presets and connections (`def build_provider(*args, **kwargs): raise NotImplementedError`).

- [ ] **Step 4: Tests, lint, pyright, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add -A && git commit -m "Add provider-neutral LLM types, JSON helpers and OpenAI-compatible adapter"
```

---

### Task 4: Anthropic adapter

**Files:**
- Create: `src/tuppence/llm/providers/anthropic.py`
- Test: `tests/llm/test_anthropic.py`

**Interfaces:**
- Consumes: types, `post_json`, `get_json`, `join_url`.
- Produces: `AnthropicProvider(client, base_url="https://api.anthropic.com", api_key, *, extra_headers=None)` implementing `Provider`; `ANTHROPIC_VERSION = "2023-06-01"`.

- [ ] **Step 1: Write the failing tests**

`tests/llm/test_anthropic.py`:

```python
import json

import httpx
import pytest

from tuppence.llm.providers.anthropic import AnthropicProvider
from tuppence.llm.types import ChatRequest, LLMRefused, Message, ToolCall, ToolSpec


def make(handler):
    return AnthropicProvider(httpx.Client(transport=httpx.MockTransport(handler)), "https://api.anthropic.com", "sk-ant-test")


def test_request_shape():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "model": "claude-x", "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"a": "b"}'}],
            "usage": {"input_tokens": 20, "output_tokens": 5},
        })

    r = make(handler).chat(ChatRequest(
        model="claude-x",
        messages=[
            Message(role="system", content="Be brief."),
            Message(role="user", content="q1"),
            Message(role="assistant", content="", tool_calls=[ToolCall(id="tu1", name="lookup", arguments={"q": "x"})]),
            Message(role="tool", content="result-1", tool_call_id="tu1", name="lookup"),
            Message(role="system", content="Second system note."),
        ],
        tools=[ToolSpec(name="lookup", description="d", parameters={"type": "object", "properties": {}})],
        json_schema={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False},
        max_tokens=300,
        temperature=0.0,
    ))
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant-test" and seen["headers"]["anthropic-version"] == "2023-06-01"
    body = seen["body"]
    assert body["system"] == "Be brief.\n\nSecond system note."
    assert "temperature" not in body and "tool_choice" not in body
    assert body["max_tokens"] == 300
    assert body["messages"][0] == {"role": "user", "content": "q1"}
    assert body["messages"][1]["content"] == [{"type": "tool_use", "id": "tu1", "name": "lookup", "input": {"q": "x"}}]
    assert body["messages"][2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "result-1"}]}
    assert body["tools"] == [{"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert r.text == '{"a": "b"}' and r.usage.input_tokens == 20 and r.finish_reason == "end_turn"


def test_tool_use_response_and_consecutive_tool_results_merge():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "model": "claude-x", "stop_reason": "tool_use",
            "content": [{"type": "text", "text": "Let me check."}, {"type": "tool_use", "id": "t9", "name": "spend", "input": {"m": "2026-09"}}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    r = make(handler).chat(ChatRequest(model="claude-x", messages=[
        Message(role="user", content="q"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="a", name="f", arguments={}), ToolCall(id="b", name="g", arguments={})]),
        Message(role="tool", content="ra", tool_call_id="a", name="f"),
        Message(role="tool", content="rb", tool_call_id="b", name="g"),
    ]))
    assert len(seen["body"]["messages"][2]["content"]) == 2  # both results in ONE user message
    assert r.tool_calls == [ToolCall(id="t9", name="spend", arguments={"m": "2026-09"})] and r.text == "Let me check."


def test_refusal_raises():
    with pytest.raises(LLMRefused):
        make(lambda req: httpx.Response(200, json={
            "model": "m", "stop_reason": "refusal", "content": [], "usage": {"input_tokens": 1, "output_tokens": 0},
            "stop_details": {"type": "refusal", "category": "cyber", "explanation": "x"}})).chat(
            ChatRequest(model="m", messages=[Message(role="user", content="x")]))


def test_trailing_assistant_message_rejected():
    with pytest.raises(ValueError):
        make(lambda req: httpx.Response(200, json={})).chat(
            ChatRequest(model="m", messages=[Message(role="user", content="x"), Message(role="assistant", content="prefill")]))


def test_list_models_paginates_and_reads_capabilities():
    pages = {
        None: {"data": [{"id": "claude-a", "display_name": "A", "max_input_tokens": 1000000, "max_tokens": 128000,
                         "capabilities": {"structured_outputs": {"supported": True}, "image_input": {"supported": True}}}],
               "has_more": True, "last_id": "claude-a"},
        "claude-a": {"data": [{"id": "claude-b", "display_name": "B", "max_input_tokens": 200000, "max_tokens": 64000,
                               "capabilities": {"structured_outputs": {"supported": False}, "image_input": {"supported": False}}}],
                     "has_more": False, "last_id": "claude-b"},
    }

    def handler(req):
        return httpx.Response(200, json=pages[req.url.params.get("after_id")])

    models = make(handler).list_models()
    assert [m.id for m in models] == ["claude-a", "claude-b"]
    assert models[0].context_window == 1000000 and models[0].supports_json_schema and models[0].supports_tools
    assert models[1].supports_json_schema is False
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

`src/tuppence/llm/providers/anthropic.py`:

```python
"""Anthropic Messages API over raw HTTP (spec §4.1, D8)."""

from __future__ import annotations

from typing import Any

import httpx

from tuppence.llm.providers.base import get_json, join_url, post_json
from tuppence.llm.types import ChatRequest, ChatResponse, LLMBadResponse, LLMRefused, Message, ProviderModel, ToolCall, Usage

ANTHROPIC_VERSION = "2023-06-01"


def _convert(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
            continue
        if m.role == "tool":
            pending_results.append({"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content})
            continue
        flush()
        if m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = [{"type": "text", "text": m.content}] if m.content else []
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content})
    flush()
    if out and out[-1]["role"] == "assistant":
        raise ValueError("Anthropic models don't accept a trailing assistant message (prefill)")
    return "\n\n".join(p for p in system_parts if p), out


class AnthropicProvider:
    api_style = "anthropic"

    def __init__(
        self, client: httpx.Client, base_url: str = "https://api.anthropic.com", api_key: str | None = None, *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.client, self.base_url = client, base_url
        self.headers = {"anthropic-version": ANTHROPIC_VERSION, "Accept": "application/json", **(extra_headers or {})}
        if api_key:
            self.headers["x-api-key"] = api_key

    def list_models(self) -> list[ProviderModel]:
        models: list[ProviderModel] = []
        after: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"limit": 1000}
            if after:
                params["after_id"] = after
            data = get_json(self.client, join_url(self.base_url, "v1/models"), headers=self.headers, params=params)
            for item in data.get("data", []):
                caps = item.get("capabilities") or {}
                models.append(ProviderModel(
                    id=item["id"], display_name=item.get("display_name"),
                    context_window=item.get("max_input_tokens"), max_output_tokens=item.get("max_tokens"),
                    supports_tools=True,
                    supports_json_schema=(caps.get("structured_outputs") or {}).get("supported"),
                    supports_vision=(caps.get("image_input") or {}).get("supported"),
                ))
            if not data.get("has_more"):
                break
            after = data.get("last_id")
        return models

    def chat(self, req: ChatRequest) -> ChatResponse:
        system, messages = _convert(req.messages)
        body: dict[str, Any] = {"model": req.model, "max_tokens": req.max_tokens, "messages": messages}
        if system:
            body["system"] = system
        if req.tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in req.tools]
        if req.json_schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": req.json_schema}}
        data = post_json(self.client, join_url(self.base_url, "v1/messages"), headers=self.headers, body=body)
        if data.get("stop_reason") == "refusal":
            details = data.get("stop_details") or {}
            raise LLMRefused(f"The model declined this request ({details.get('category') or 'policy'})")
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content") or []:
            kind = block.get("type")
            if kind == "text":
                texts.append(block.get("text", ""))
            elif kind == "tool_use":
                calls.append(ToolCall(id=block["id"], name=block["name"], arguments=block.get("input") or {}))
        if "content" not in data:
            raise LLMBadResponse("Anthropic response had no content")
        usage = data.get("usage") or {}
        return ChatResponse(
            text="".join(texts), tool_calls=calls, model=data.get("model") or req.model,
            usage=Usage(input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0)),
            finish_reason=data.get("stop_reason"),
        )
```

- [ ] **Step 4: Tests, lint, pyright, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add -A && git commit -m "Add Anthropic Messages adapter (raw HTTP, structured output, tools)"
```

---

### Task 5: Gemini adapter

**Files:**
- Create: `src/tuppence/llm/providers/gemini.py`
- Test: `tests/llm/test_gemini.py`

**Interfaces:**
- Produces: `GeminiProvider(client, base_url="https://generativelanguage.googleapis.com", api_key, *, extra_headers=None)`; requests to `POST {base}/v1beta/models/{model}:generateContent` with header `x-goog-api-key`; JSON via `generationConfig.responseMimeType = "application/json"` + `responseJsonSchema`; if the API answers HTTP 400 mentioning `responseJsonSchema`/`response_json_schema`, retry once **without** the schema (JSON mime type only) so the client's validate-and-repair path takes over; tools as `tools: [{"functionDeclarations": [{"name","description","parametersJsonSchema"}]}]`; thought parts (`"thought": true`) skipped; `finishReason` in {SAFETY, PROHIBITED_CONTENT, BLOCKLIST, SPII, RECITATION} or `promptFeedback.blockReason` → `LLMRefused`; usage = `promptTokenCount`, `candidatesTokenCount + thoughtsTokenCount`; list models via `GET {base}/v1beta/models?pageSize=1000` following `nextPageToken`, keeping models whose `supportedGenerationMethods` include `generateContent`, id = name without `models/`.

- [ ] **Step 1: Write the failing tests**

`tests/llm/test_gemini.py`:

```python
import json

import httpx
import pytest

from tuppence.llm.providers.gemini import GeminiProvider
from tuppence.llm.types import ChatRequest, LLMRefused, Message, ToolCall, ToolSpec


def make(handler):
    return GeminiProvider(httpx.Client(transport=httpx.MockTransport(handler)), api_key="g-key")


OK = {"candidates": [{"content": {"role": "model", "parts": [{"text": "thinking...", "thought": True}, {"text": "Hello"}]},
                      "finishReason": "STOP"}],
      "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2, "thoughtsTokenCount": 5}}


def test_request_shape_and_parse():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=OK)

    r = make(handler).chat(ChatRequest(
        model="gemini-x",
        messages=[
            Message(role="system", content="sys"),
            Message(role="user", content="hi"),
            Message(role="assistant", content="", tool_calls=[ToolCall(id="c1", name="lookup", arguments={"q": 1})]),
            Message(role="tool", content='{"r": 2}', tool_call_id="c1", name="lookup"),
        ],
        tools=[ToolSpec(name="lookup", description="d", parameters={"type": "object", "properties": {}})],
        json_schema={"type": "object", "properties": {}},
        max_tokens=50,
    ))
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    assert seen["key"] == "g-key"
    b = seen["body"]
    assert b["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert b["contents"][0] == {"role": "user", "parts": [{"text": "hi"}]}
    assert b["contents"][1] == {"role": "model", "parts": [{"functionCall": {"name": "lookup", "args": {"q": 1}}}]}
    assert b["contents"][2] == {"role": "user", "parts": [{"functionResponse": {"name": "lookup", "response": {"result": '{"r": 2}'}}}]}
    assert b["generationConfig"]["responseMimeType"] == "application/json"
    assert b["generationConfig"]["responseJsonSchema"] == {"type": "object", "properties": {}}
    assert b["generationConfig"]["maxOutputTokens"] == 50
    assert b["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == {"type": "object", "properties": {}}
    assert r.text == "Hello" and r.usage.input_tokens == 7 and r.usage.output_tokens == 7


def test_schema_rejected_retries_without_schema():
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        if "responseJsonSchema" in body["generationConfig"]:
            return httpx.Response(400, json={"error": {"message": "Unknown name \"responseJsonSchema\""}})
        return httpx.Response(200, json=OK)

    r = make(handler).chat(ChatRequest(model="g", messages=[Message(role="user", content="x")], json_schema={"type": "object"}))
    assert len(calls) == 2 and r.text == "Hello"
    assert calls[1]["generationConfig"]["responseMimeType"] == "application/json"


def test_function_call_and_safety():
    fc = {"candidates": [{"content": {"parts": [{"functionCall": {"name": "spend", "args": {"m": "x"}}}]}, "finishReason": "STOP"}]}
    r = make(lambda req: httpx.Response(200, json=fc)).chat(ChatRequest(model="g", messages=[Message(role="user", content="x")]))
    assert r.tool_calls[0].name == "spend" and r.tool_calls[0].arguments == {"m": "x"}
    blocked = {"candidates": [{"finishReason": "SAFETY"}]}
    with pytest.raises(LLMRefused):
        make(lambda req: httpx.Response(200, json=blocked)).chat(ChatRequest(model="g", messages=[Message(role="user", content="x")]))
    pf = {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}
    with pytest.raises(LLMRefused):
        make(lambda req: httpx.Response(200, json=pf)).chat(ChatRequest(model="g", messages=[Message(role="user", content="x")]))


def test_list_models():
    pages = {
        None: {"models": [
            {"name": "models/gemini-a", "displayName": "A", "inputTokenLimit": 1048576, "outputTokenLimit": 65536,
             "supportedGenerationMethods": ["generateContent", "countTokens"]},
            {"name": "models/embed-x", "supportedGenerationMethods": ["embedContent"]}],
            "nextPageToken": "p2"},
        "p2": {"models": [{"name": "models/gemini-b", "inputTokenLimit": 32768, "supportedGenerationMethods": ["generateContent"]}]},
    }
    models = make(lambda req: httpx.Response(200, json=pages[req.url.params.get("pageToken")])).list_models()
    assert [m.id for m in models] == ["gemini-a", "gemini-b"]
    assert models[0].context_window == 1048576 and models[0].max_output_tokens == 65536
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

`src/tuppence/llm/providers/gemini.py`:

```python
"""Google Gemini generateContent over raw HTTP."""

from __future__ import annotations

from typing import Any

import httpx

from tuppence.llm.providers.base import get_json, join_url, post_json
from tuppence.llm.types import (
    ChatRequest, ChatResponse, LLMBadResponse, LLMHTTPError, LLMRefused, Message, ProviderModel, ToolCall, Usage,
)

BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


def _convert(messages: list[Message]) -> tuple[list[str], list[dict[str, Any]]]:
    system: list[str] = []
    contents: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system.append(m.content)
        elif m.role == "user":
            contents.append({"role": "user", "parts": [{"text": m.content}]})
        elif m.role == "assistant":
            parts: list[dict[str, Any]] = [{"text": m.content}] if m.content else []
            parts += [{"functionCall": {"name": c.name, "args": c.arguments}} for c in m.tool_calls]
            contents.append({"role": "model", "parts": parts})
        else:
            part = {"functionResponse": {"name": m.name or "tool", "response": {"result": m.content}}}
            if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0]:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
    return system, contents


class GeminiProvider:
    api_style = "gemini"

    def __init__(
        self, client: httpx.Client, base_url: str = "https://generativelanguage.googleapis.com",
        api_key: str | None = None, *, extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.client, self.base_url = client, base_url
        self.headers = {"Accept": "application/json", **(extra_headers or {})}
        if api_key:
            self.headers["x-goog-api-key"] = api_key

    def list_models(self) -> list[ProviderModel]:
        models: list[ProviderModel] = []
        token: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"pageSize": 1000}
            if token:
                params["pageToken"] = token
            data = get_json(self.client, join_url(self.base_url, "v1beta/models"), headers=self.headers, params=params)
            for item in data.get("models", []):
                if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                    continue
                models.append(ProviderModel(
                    id=item["name"].removeprefix("models/"), display_name=item.get("displayName"),
                    context_window=item.get("inputTokenLimit"), max_output_tokens=item.get("outputTokenLimit"),
                    supports_tools=True, supports_json_schema=True,
                ))
            token = data.get("nextPageToken")
            if not token:
                break
        return models

    def _body(self, req: ChatRequest, *, with_schema: bool) -> dict[str, Any]:
        system, contents = _convert(req.messages)
        config: dict[str, Any] = {"maxOutputTokens": req.max_tokens}
        if req.temperature is not None:
            config["temperature"] = req.temperature
        if req.json_schema is not None:
            config["responseMimeType"] = "application/json"
            if with_schema:
                config["responseJsonSchema"] = req.json_schema
        body: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        if req.tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parametersJsonSchema": t.parameters} for t in req.tools
            ]}]
        return body

    def chat(self, req: ChatRequest) -> ChatResponse:
        url = join_url(self.base_url, f"v1beta/models/{req.model}:generateContent")
        try:
            data = post_json(self.client, url, headers=self.headers, body=self._body(req, with_schema=True))
        except LLMHTTPError as exc:
            schema_rejected = exc.status == 400 and (
                "responseJsonSchema" in exc.body or "response_json_schema" in exc.body
            )
            if req.json_schema is None or not schema_rejected:
                raise
            data = post_json(self.client, url, headers=self.headers, body=self._body(req, with_schema=False))
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise LLMRefused(f"The model declined this request ({feedback['blockReason']})")
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMBadResponse("Gemini returned no candidates")
        cand = candidates[0]
        if cand.get("finishReason") in BLOCKED:
            raise LLMRefused(f"The model declined this request ({cand['finishReason']})")
        texts: list[str] = []
        calls: list[ToolCall] = []
        for i, part in enumerate((cand.get("content") or {}).get("parts") or []):
            if part.get("thought"):
                continue
            if "text" in part:
                texts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                calls.append(ToolCall(id=f"call_{i}", name=fc.get("name", ""), arguments=fc.get("args") or {}))
        usage = data.get("usageMetadata") or {}
        return ChatResponse(
            text="".join(texts), tool_calls=calls, model=req.model, finish_reason=cand.get("finishReason"),
            usage=Usage(
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
            ),
        )
```

- [ ] **Step 4: Tests, lint, pyright, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add -A && git commit -m "Add Gemini generateContent adapter"
```

---

### Task 6: Presets, model catalogue and the connection registry

**Files:**
- Create: `src/tuppence/llm/presets.py`, `src/tuppence/llm/catalogue.py`, `src/tuppence/datapacks/__init__.py`, `src/tuppence/datapacks/baseline/__init__.py`, `src/tuppence/datapacks/baseline/model-catalogue.json`, `scripts/build_model_catalogue.py`, `src/tuppence/llm/connections.py`
- Modify: `src/tuppence/llm/providers/__init__.py` (real `build_provider`), `src/tuppence/app/services.py` (add `catalogue`, `connections`)
- Test: `tests/llm/test_presets.py`, `tests/llm/test_catalogue.py`, `tests/llm/test_connections.py`

**Interfaces:**
- Produces:
  - `Preset(id, label, api_style, base_url, kind: Literal["local","cloud","custom"], key_required: bool, max_tokens_param="max_tokens", docs_url=None)`; `PRESETS: dict[str, Preset]` with ids: local `ollama` (http://127.0.0.1:11434/v1), `lmstudio` (:1234/v1), `llamacpp` (:8080/v1), `vllm` (:8000/v1), `jan` (:1337/v1), `foundry_local` (base URL required from the user; default `http://127.0.0.1:5273/v1`); cloud `anthropic` (https://api.anthropic.com, api_style anthropic), `openai` (https://api.openai.com/v1, max_tokens_param `max_completion_tokens`), `gemini` (https://generativelanguage.googleapis.com, api_style gemini), `openrouter` (https://openrouter.ai/api/v1), `mistral` (https://api.mistral.ai/v1), `groq` (https://api.groq.com/openai/v1), `together` (https://api.together.xyz/v1), `xai` (https://api.x.ai/v1), `deepseek` (https://api.deepseek.com/v1), `qwen` (https://dashscope-intl.aliyuncs.com/compatible-mode/v1), `kimi` (https://api.moonshot.ai/v1), `glm` (https://api.z.ai/api/paas/v4); `custom` (openai style, kind custom)
  - `normalise_base_url(url: str, api_style: str) -> str` — trims whitespace and trailing `/`; for `openai` style adds `/v1` when the URL has no path at all (e.g. `https://api.openai.com` → `https://api.openai.com/v1`); never alters a URL that already has a path; rejects non-http(s) schemes with `InputError`
  - `CatalogueEntry` (pydantic, same capability/price fields as `ProviderModel` plus `aliases: list[str]`), `ModelCatalogue(entries)` with `lookup(model_id) -> CatalogueEntry | None`; `load_baseline() -> ModelCatalogue`; `normalise_model_id(model_id) -> str` (lowercase; drop `vendor/` prefix, `:free`/`:beta`/`:latest` suffixes and trailing `-YYYYMMDD`; `.` → `-`)
  - `ModelInfo` (pydantic): `connection_id, model_id, display_name, context_window: int, max_output_tokens: int | None, supports_tools: bool, supports_json_schema: bool, supports_vision: bool, price_in_usd_per_mtok: float | None, price_out_usd_per_mtok: float | None, source`
  - Defaults when nothing is known: local `context_window=4096`, cloud `32768`; capabilities `False`; local prices `0.0`, cloud prices `None`
  - `Connection` (pydantic): `id, preset, name, api_style, base_url, is_local, has_key, needs_notice, notice_acknowledged_at, enabled, version`
  - `ConnectionRegistry(db, secrets, catalogue, *, client_factory)` where `client_factory(ctx: CallContext, timeout: float) -> httpx.Client`: `list() -> list[Connection]`, `get(id) -> Connection`, `create(preset, *, name=None, base_url=None, api_key=None, headers=None) -> Connection` (cloud presets require a key: `InputError("An API key is needed for <label>.")`), `update(id, changes: dict, expected_version) -> Connection` (`name`, `base_url`, `api_key` ("" clears), `enabled`, `headers`), `delete(id)`, `api_key(id) -> str | None`, `acknowledge_notice(id) -> Connection`, `provider(id, *, task=None, redactions=0, timeout=30.0) -> Provider`, `test(id) -> list[ModelInfo]` (lists models via the provider, enriches with the catalogue, stores in `llm_model`, returns them), `models(connection_id=None) -> list[ModelInfo]`, `model(connection_id, model_id) -> ModelInfo`, `set_context_window(connection_id, model_id, value: int) -> ModelInfo` (source `user`)
  - `detect_local(client_factory) -> list[DetectedServer]` (`preset, base_url, model_count`) — probes each local preset's default URL `GET {base}/models` with a 0.5 s timeout; never raises
  - `build_provider(api_style, client, base_url, api_key, headers, max_tokens_param) -> Provider`
  - `is_local` rule: cloud presets → False; local and custom presets → `is_local_host(host of base_url)`

- [ ] **Step 1: Generate the baseline catalogue**

`scripts/build_model_catalogue.py`:

```python
"""Regenerate src/tuppence/datapacks/baseline/model-catalogue.json.

Sources: OpenRouter's public model list (context windows, capabilities, USD prices)
plus Anthropic first-party entries maintained below. Run occasionally; the M6 data-pack
pipeline replaces this with signed packs.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "tuppence" / "datapacks" / "baseline" / "model-catalogue.json"

# Anthropic first-party IDs and prices (USD per million tokens), checked 2026-10-07.
ANTHROPIC = [
    ("claude-opus-5-5", 1_000_000, 128_000, 4.0, 20.0),
    ("claude-sonnet-5-5", 1_000_000, 128_000, 2.0, 10.0),
    ("claude-haiku-4-5", 200_000, 64_000, 1.0, 5.0),
    ("claude-fable-5-1", 1_000_000, 128_000, 10.0, 50.0),
]


def main() -> int:
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:  # noqa: S310
        data = json.load(r)["data"]
    entries = []
    for m in data:
        params = set(m.get("supported_parameters") or [])
        pricing = m.get("pricing") or {}

        def per_mtok(v):
            try:
                return round(float(v) * 1_000_000, 6)
            except (TypeError, ValueError):
                return None

        model_part = m["id"].split("/", 1)[-1]
        entries.append({
            "id": m["id"],
            "aliases": sorted({model_part}),
            "context_window": m.get("context_length"),
            "max_output_tokens": (m.get("top_provider") or {}).get("max_completion_tokens"),
            "supports_tools": "tools" in params,
            "supports_json_schema": bool(params & {"response_format", "structured_outputs"}),
            "supports_vision": "image" in ((m.get("architecture") or {}).get("input_modalities") or []),
            "price_in_usd_per_mtok": per_mtok(pricing.get("prompt")),
            "price_out_usd_per_mtok": per_mtok(pricing.get("completion")),
        })
    for mid, ctx, out, pin, pout in ANTHROPIC:
        entries.append({
            "id": f"anthropic/{mid}", "aliases": [mid], "context_window": ctx, "max_output_tokens": out,
            "supports_tools": True, "supports_json_schema": True, "supports_vision": True,
            "price_in_usd_per_mtok": pin, "price_out_usd_per_mtok": pout,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"version": date.today().isoformat(), "models": entries}, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(entries)} models to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `uv run python scripts/build_model_catalogue.py` → writes the JSON (expect a few hundred models). Commit the generated file. Entries added later for the same normalised id must win over earlier ones (the Anthropic list is appended last on purpose).

- [ ] **Step 2: Write the failing tests**

`tests/llm/test_presets.py`:

```python
import pytest

from tuppence.core.errors import InputError
from tuppence.llm.presets import PRESETS, normalise_base_url


def test_all_spec_presets_present():
    expected = {"ollama", "lmstudio", "llamacpp", "vllm", "jan", "foundry_local", "anthropic", "openai", "gemini",
                "openrouter", "mistral", "groq", "together", "xai", "deepseek", "qwen", "kimi", "glm", "custom"}
    assert expected <= set(PRESETS)
    assert PRESETS["anthropic"].api_style == "anthropic" and PRESETS["gemini"].api_style == "gemini"
    assert PRESETS["openai"].max_tokens_param == "max_completion_tokens"
    assert all(p.kind == "local" for k, p in PRESETS.items() if k in {"ollama", "lmstudio", "llamacpp", "vllm", "jan", "foundry_local"})
    assert all(p.key_required for k, p in PRESETS.items() if PRESETS[k].kind == "cloud")


@pytest.mark.parametrize("raw,style,expected", [
    ("https://api.openai.com", "openai", "https://api.openai.com/v1"),
    ("https://api.openai.com/v1/ ", "openai", "https://api.openai.com/v1"),
    ("  http://127.0.0.1:11434/v1  ", "openai", "http://127.0.0.1:11434/v1"),
    ("https://api.z.ai/api/paas/v4", "openai", "https://api.z.ai/api/paas/v4"),
    ("https://api.anthropic.com/", "anthropic", "https://api.anthropic.com"),
])
def test_normalise_base_url(raw, style, expected):
    assert normalise_base_url(raw, style) == expected


def test_rejects_bad_scheme():
    with pytest.raises(InputError):
        normalise_base_url("ftp://x", "openai")
```

`tests/llm/test_catalogue.py`:

```python
from tuppence.llm.catalogue import CatalogueEntry, ModelCatalogue, load_baseline, normalise_model_id


def test_normalise():
    assert normalise_model_id("anthropic/claude-sonnet-4.5") == "claude-sonnet-4-5"
    assert normalise_model_id("claude-3-5-haiku-20241022") == "claude-3-5-haiku"
    assert normalise_model_id("Meta-Llama/Llama-3.1-8B-Instruct:free") == "llama-3-1-8b-instruct"


def test_lookup_exact_alias_and_prefix():
    cat = ModelCatalogue([
        CatalogueEntry(id="vendor/model-a", aliases=["model-a"], context_window=1000),
        CatalogueEntry(id="vendor/model-a-long", aliases=[], context_window=2000),
    ])
    assert cat.lookup("model-a").context_window == 1000
    assert cat.lookup("vendor/model-a-long").context_window == 2000
    assert cat.lookup("model-a-long-2026-preview").context_window == 2000  # longest prefix
    assert cat.lookup("unrelated") is None


def test_baseline_loads_and_has_anthropic():
    cat = load_baseline()
    e = cat.lookup("claude-sonnet-5-5")
    assert e is not None and e.context_window == 1_000_000 and e.price_in_usd_per_mtok == 2.0
```

`tests/llm/test_connections.py`:

```python
import httpx
import pytest

from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.migrate import migrate
from tuppence.core.secrets import EncryptedDbStore, load_or_create_key
from tuppence.llm.catalogue import CatalogueEntry, ModelCatalogue
from tuppence.llm.connections import ConnectionRegistry, detect_local
from tuppence.net.client import CallContext


def fake_server(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": [{"id": "small-1"}, {"id": "vendor/known"}]})
    return httpx.Response(404)


@pytest.fixture
def reg(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    secrets = EncryptedDbStore(db, load_or_create_key(tmp_path, env={}))
    cat = ModelCatalogue([CatalogueEntry(id="vendor/known", aliases=["known"], context_window=64000, supports_json_schema=True,
                                         price_in_usd_per_mtok=1.0, price_out_usd_per_mtok=2.0)])

    def client_factory(ctx: CallContext, timeout: float) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(fake_server), timeout=timeout)

    return ConnectionRegistry(db, secrets, cat, client_factory=client_factory)


def test_create_local_connection_without_key(reg):
    c = reg.create("ollama")
    assert c.is_local and not c.has_key and not c.needs_notice and c.base_url == "http://127.0.0.1:11434/v1"


def test_cloud_requires_key_and_notice(reg):
    with pytest.raises(InputError):
        reg.create("openai")
    c = reg.create("openai", api_key="sk-x")
    assert not c.is_local and c.has_key and c.needs_notice
    assert reg.api_key(c.id) == "sk-x"
    assert "sk-x" not in c.model_dump_json()
    c2 = reg.acknowledge_notice(c.id)
    assert not c2.needs_notice


def test_cloud_preset_pointed_at_localhost_is_still_cloud(reg):
    c = reg.create("openai", api_key="k", base_url="http://127.0.0.1:9999/v1")
    assert not c.is_local


def test_custom_connection_locality_follows_host(reg):
    assert reg.create("custom", base_url="http://192.168.1.50:8080/v1").is_local
    assert not reg.create("custom", base_url="https://8.8.8.8/v1", api_key="k").is_local


def test_test_connection_stores_enriched_models(reg):
    c = reg.create("ollama")
    models = {m.model_id: m for m in reg.test(c.id)}
    assert models["small-1"].context_window == 4096 and models["small-1"].source == "default"
    assert models["small-1"].price_in_usd_per_mtok == 0.0
    known = models["vendor/known"]
    assert known.context_window == 64000 and known.supports_json_schema and known.source == "catalogue"
    assert {m.model_id for m in reg.models(c.id)} == {"small-1", "vendor/known"}


def test_user_context_window_override_survives_retest(reg):
    c = reg.create("ollama")
    reg.test(c.id)
    reg.set_context_window(c.id, "small-1", 16384)
    reg.test(c.id)
    assert reg.model(c.id, "small-1").context_window == 16384


def test_update_and_delete(reg):
    c = reg.create("openai", api_key="old")
    c = reg.update(c.id, {"api_key": "new", "name": "Work OpenAI"}, expected_version=1)
    assert reg.api_key(c.id) == "new" and c.name == "Work OpenAI" and c.version == 2
    reg.delete(c.id)
    assert reg.list() == []


def test_detect_local_reports_responding_servers():
    def factory(ctx, timeout):
        def handler(req):
            if req.url.port == 11434:
                return httpx.Response(200, json={"data": [{"id": "llama"}]})
            raise httpx.ConnectError("refused", request=req)
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    found = detect_local(factory)
    assert [(d.preset, d.model_count) for d in found] == [("ollama", 1)]
```

- [ ] **Step 3: Run to verify failure**, then **Step 4: Implement**

`src/tuppence/llm/presets.py`:

```python
"""Connection presets (spec §4.1). Base URLs only — never model IDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from tuppence.core.errors import InputError

ApiStyle = Literal["openai", "anthropic", "gemini"]


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    api_style: ApiStyle
    base_url: str
    kind: Literal["local", "cloud", "custom"]
    key_required: bool
    max_tokens_param: str = "max_tokens"
    docs_url: str | None = None


def _p(*args: object, **kw: object) -> Preset:
    return Preset(*args, **kw)  # type: ignore[arg-type]


PRESETS: dict[str, Preset] = {p.id: p for p in [
    _p("ollama", "Ollama", "openai", "http://127.0.0.1:11434/v1", "local", False, docs_url="https://ollama.com"),
    _p("lmstudio", "LM Studio", "openai", "http://127.0.0.1:1234/v1", "local", False, docs_url="https://lmstudio.ai"),
    _p("llamacpp", "llama.cpp server", "openai", "http://127.0.0.1:8080/v1", "local", False),
    _p("vllm", "vLLM", "openai", "http://127.0.0.1:8000/v1", "local", False),
    _p("jan", "Jan", "openai", "http://127.0.0.1:1337/v1", "local", False, docs_url="https://jan.ai"),
    _p("foundry_local", "Foundry Local", "openai", "http://127.0.0.1:5273/v1", "local", False),
    _p("anthropic", "Anthropic (Claude)", "anthropic", "https://api.anthropic.com", "cloud", True),
    _p("openai", "OpenAI", "openai", "https://api.openai.com/v1", "cloud", True, "max_completion_tokens"),
    _p("gemini", "Google Gemini", "gemini", "https://generativelanguage.googleapis.com", "cloud", True),
    _p("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1", "cloud", True),
    _p("mistral", "Mistral", "openai", "https://api.mistral.ai/v1", "cloud", True),
    _p("groq", "Groq", "openai", "https://api.groq.com/openai/v1", "cloud", True),
    _p("together", "Together AI", "openai", "https://api.together.xyz/v1", "cloud", True),
    _p("xai", "xAI (Grok)", "openai", "https://api.x.ai/v1", "cloud", True),
    _p("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1", "cloud", True),
    _p("qwen", "Qwen (Alibaba Model Studio)", "openai", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "cloud", True),
    _p("kimi", "Kimi (Moonshot)", "openai", "https://api.moonshot.ai/v1", "cloud", True),
    _p("glm", "GLM (Z.ai)", "openai", "https://api.z.ai/api/paas/v4", "cloud", True),
    _p("custom", "Custom (OpenAI-compatible)", "openai", "", "custom", False),
]}


def normalise_base_url(url: str, api_style: str) -> str:
    raw = url.strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise InputError("The base URL must start with http:// or https://")
    path = parts.path.rstrip("/")
    if api_style == "openai" and path == "":
        path = "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
```

`src/tuppence/llm/catalogue.py`:

```python
"""Model facts the providers don't tell us: context windows, capabilities, prices."""

from __future__ import annotations

import json
import re
from importlib import resources

from pydantic import BaseModel, Field

_DATE = re.compile(r"-\d{8}$")
_SUFFIX = re.compile(r":(free|beta|latest|extended|thinking)$")


def normalise_model_id(model_id: str) -> str:
    m = model_id.strip().lower()
    m = _SUFFIX.sub("", m)
    m = m.split("/", 1)[-1]
    m = _DATE.sub("", m)
    return m.replace(".", "-")


class CatalogueEntry(BaseModel):
    id: str
    aliases: list[str] = Field(default_factory=list)
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool | None = None
    supports_json_schema: bool | None = None
    supports_vision: bool | None = None
    price_in_usd_per_mtok: float | None = None
    price_out_usd_per_mtok: float | None = None


class ModelCatalogue:
    def __init__(self, entries: list[CatalogueEntry]) -> None:
        self.by_key: dict[str, CatalogueEntry] = {}
        for e in entries:  # later entries win
            for key in {normalise_model_id(e.id), *(normalise_model_id(a) for a in e.aliases)}:
                self.by_key[key] = e
        self.keys_by_length = sorted(self.by_key, key=len, reverse=True)

    def lookup(self, model_id: str) -> CatalogueEntry | None:
        key = normalise_model_id(model_id)
        if key in self.by_key:
            return self.by_key[key]
        for candidate in self.keys_by_length:
            if key.startswith(candidate + "-") or key.startswith(candidate + ":"):
                return self.by_key[candidate]
        return None


def load_baseline() -> ModelCatalogue:
    text = resources.files("tuppence.datapacks.baseline").joinpath("model-catalogue.json").read_text(encoding="utf-8")
    return ModelCatalogue([CatalogueEntry(**e) for e in json.loads(text)["models"]])
```

> Check the prefix test: `"model-a-long-2026-preview"` must match `model-a-long` (longer) before `model-a`; iterating `keys_by_length` longest-first does that.

`src/tuppence/llm/providers/__init__.py`:

```python
from __future__ import annotations

import httpx

from tuppence.llm.providers.anthropic import AnthropicProvider
from tuppence.llm.providers.gemini import GeminiProvider
from tuppence.llm.providers.openai_compat import OpenAICompatProvider
from tuppence.llm.types import Provider


def build_provider(
    api_style: str, client: httpx.Client, base_url: str, api_key: str | None,
    headers: dict[str, str] | None = None, max_tokens_param: str = "max_tokens",
) -> Provider:
    if api_style == "anthropic":
        return AnthropicProvider(client, base_url, api_key, extra_headers=headers)
    if api_style == "gemini":
        return GeminiProvider(client, base_url, api_key, extra_headers=headers)
    return OpenAICompatProvider(client, base_url, api_key, extra_headers=headers, max_tokens_param=max_tokens_param)
```

`src/tuppence/llm/connections.py`:

```python
"""Saved LLM connections and their models (spec §4.1, §4.2)."""

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
from tuppence.core.secrets import SecretStore
from tuppence.llm.catalogue import ModelCatalogue
from tuppence.llm.presets import PRESETS, normalise_base_url
from tuppence.llm.providers import build_provider
from tuppence.llm.types import LLMError, Provider
from tuppence.net.client import CallContext
from tuppence.net.hosts import is_local_host

ClientFactory = Callable[[CallContext, float], httpx.Client]


class Connection(BaseModel):
    id: str
    preset: str
    name: str
    api_style: str
    base_url: str
    is_local: bool
    has_key: bool
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


class DetectedServer(BaseModel):
    preset: str
    base_url: str
    model_count: int


def _locality(preset_id: str, base_url: str) -> bool:
    preset = PRESETS[preset_id]
    if preset.kind == "cloud":
        return False
    host = httpx.URL(base_url).host
    return is_local_host(host)


class ConnectionRegistry:
    def __init__(self, db: Database, secrets: SecretStore, catalogue: ModelCatalogue, *, client_factory: ClientFactory) -> None:
        self.db, self.secrets, self.catalogue, self.client_factory = db, secrets, catalogue, client_factory

    def _row_to_connection(self, r: Any) -> Connection:
        return Connection(
            id=r["id"], preset=r["preset"], name=r["name"], api_style=r["api_style"], base_url=r["base_url"],
            is_local=bool(r["is_local"]), has_key=r["secret_ref"] is not None,
            needs_notice=not r["is_local"] and r["notice_acknowledged_at"] is None,
            notice_acknowledged_at=r["notice_acknowledged_at"], enabled=bool(r["enabled"]), version=r["version"],
        )

    def _row(self, connection_id: str) -> Any:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM llm_connection WHERE id = ?", [connection_id]).fetchone()
        if row is None:
            raise NotFound("llm_connection", connection_id)
        return row

    def list(self) -> list[Connection]:
        with self.db.connection() as conn:
            rows = conn.execute("SELECT * FROM llm_connection ORDER BY created_at, rowid").fetchall()
        return [self._row_to_connection(r) for r in rows]

    def get(self, connection_id: str) -> Connection:
        return self._row_to_connection(self._row(connection_id))

    def create(
        self, preset: str, *, name: str | None = None, base_url: str | None = None, api_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Connection:
        if preset not in PRESETS:
            raise InputError(f"Unknown provider '{preset}'.")
        p = PRESETS[preset]
        url = normalise_base_url(base_url or p.base_url, p.api_style) if (base_url or p.base_url) else ""
        if not url:
            raise InputError("Enter the server's base URL.")
        key = (api_key or "").strip() or None
        if p.key_required and not key:
            raise InputError(f"An API key is needed for {p.label}.")
        connection_id = "c_" + pysecrets.token_hex(4)
        secret_ref = self.secrets.put(key) if key else None
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO llm_connection (id, preset, name, api_style, base_url, secret_ref, headers, is_local, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [connection_id, preset, (name or p.label).strip(), p.api_style, url, secret_ref, json.dumps(headers or {}),
                 int(_locality(preset, url)), now, now],
            )
        return self.get(connection_id)

    def update(self, connection_id: str, changes: dict[str, Any], expected_version: int) -> Connection:
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
            if not data["is_local"]:
                data["notice_acknowledged_at"] = None if row["is_local"] else row["notice_acknowledged_at"]
        if "enabled" in changes:
            data["enabled"] = int(bool(changes["enabled"]))
        if "headers" in changes:
            data["headers"] = json.dumps(changes["headers"] or {})
        if "api_key" in changes:
            key = (changes["api_key"] or "").strip()
            if row["secret_ref"]:
                self.secrets.delete(row["secret_ref"])
            data["secret_ref"] = self.secrets.put(key) if key else None
        if data:
            with self.db.transaction() as conn:
                update_versioned(conn, "llm_connection", "id", connection_id, expected_version, data, now=to_iso(utcnow()))
        return self.get(connection_id)

    def delete(self, connection_id: str) -> None:
        row = self._row(connection_id)
        if row["secret_ref"]:
            self.secrets.delete(row["secret_ref"])
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM llm_connection WHERE id = ?", [connection_id])

    def api_key(self, connection_id: str) -> str | None:
        ref = self._row(connection_id)["secret_ref"]
        return self.secrets.get(ref) if ref else None

    def acknowledge_notice(self, connection_id: str) -> Connection:
        self._row(connection_id)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE llm_connection SET notice_acknowledged_at = ? WHERE id = ?", [to_iso(utcnow()), connection_id]
            )
        return self.get(connection_id)

    def provider(self, connection_id: str, *, task: str | None = None, redactions: int = 0, timeout: float = 30.0) -> Provider:
        row = self._row(connection_id)
        ctx = CallContext(purpose="llm", task=task, connection_id=connection_id, local=bool(row["is_local"]), redactions=redactions)
        client = self.client_factory(ctx, timeout)
        preset = PRESETS[row["preset"]]
        return build_provider(
            row["api_style"], client, row["base_url"], self.api_key(connection_id),
            json.loads(row["headers"]), preset.max_tokens_param,
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
                    "SELECT context_window, source FROM llm_model WHERE connection_id = ? AND model_id = ?",
                    [connection_id, pm.id],
                ).fetchone()
                entry = self.catalogue.lookup(pm.id)

                def pick(provider_value: Any, catalogue_attr: str, default: Any) -> Any:
                    if provider_value is not None:
                        return provider_value
                    if entry is not None and getattr(entry, catalogue_attr) is not None:
                        return getattr(entry, catalogue_attr)
                    return default

                source = "provider" if pm.context_window else ("catalogue" if entry else "default")
                context = pick(pm.context_window, "context_window", 4096 if local else 32768)
                if existing is not None and existing["source"] == "user":
                    context, source = existing["context_window"], "user"
                price_default = 0.0 if local else None
                conn.execute(
                    "INSERT INTO llm_model (connection_id, model_id, display_name, context_window, max_output_tokens,"
                    " supports_tools, supports_json_schema, supports_vision, price_in_usd_per_mtok, price_out_usd_per_mtok,"
                    " source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(connection_id, model_id) DO UPDATE SET display_name = excluded.display_name,"
                    " context_window = excluded.context_window, max_output_tokens = excluded.max_output_tokens,"
                    " supports_tools = excluded.supports_tools, supports_json_schema = excluded.supports_json_schema,"
                    " supports_vision = excluded.supports_vision, price_in_usd_per_mtok = excluded.price_in_usd_per_mtok,"
                    " price_out_usd_per_mtok = excluded.price_out_usd_per_mtok, source = excluded.source,"
                    " fetched_at = excluded.fetched_at",
                    [connection_id, pm.id, pm.display_name, context,
                     pick(pm.max_output_tokens, "max_output_tokens", None),
                     int(bool(pick(pm.supports_tools, "supports_tools", False))),
                     int(bool(pick(pm.supports_json_schema, "supports_json_schema", False))),
                     int(bool(pick(pm.supports_vision, "supports_vision", False))),
                     0.0 if local else pick(pm.price_in_usd_per_mtok, "price_in_usd_per_mtok", price_default),
                     0.0 if local else pick(pm.price_out_usd_per_mtok, "price_out_usd_per_mtok", price_default),
                     source, now],
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
            connection_id=r["connection_id"], model_id=r["model_id"], display_name=r["display_name"],
            context_window=r["context_window"], max_output_tokens=r["max_output_tokens"],
            supports_tools=bool(r["supports_tools"]), supports_json_schema=bool(r["supports_json_schema"]),
            supports_vision=bool(r["supports_vision"]), price_in_usd_per_mtok=r["price_in_usd_per_mtok"],
            price_out_usd_per_mtok=r["price_out_usd_per_mtok"], source=r["source"],
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
                "SELECT * FROM llm_model WHERE connection_id = ? AND model_id = ?", [connection_id, model_id]
            ).fetchone()
        if r is None:
            raise NotFound("llm_model", f"{connection_id}/{model_id}")
        return self._model_row(r)

    def set_context_window(self, connection_id: str, model_id: str, value: int) -> ModelInfo:
        if value < 512:
            raise InputError("The context window must be at least 512 tokens.")
        self.model(connection_id, model_id)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE llm_model SET context_window = ?, source = 'user' WHERE connection_id = ? AND model_id = ?",
                [value, connection_id, model_id],
            )
        return self.model(connection_id, model_id)


def detect_local(client_factory: ClientFactory) -> list[DetectedServer]:
    from tuppence.llm.providers.openai_compat import OpenAICompatProvider

    found: list[DetectedServer] = []
    for preset in PRESETS.values():
        if preset.kind != "local":
            continue
        client = client_factory(CallContext(purpose="llm", local=True), 0.5)
        try:
            models = OpenAICompatProvider(client, preset.base_url, None).list_models()
        except (LLMError, httpx.HTTPError, OSError):
            continue
        finally:
            client.close()
        found.append(DetectedServer(preset=preset.id, base_url=preset.base_url, model_count=len(models)))
    return found
```

Wire into `Services`: `catalogue = load_baseline()`, `client_factory = lambda ctx, timeout: make_client(ctx, privacy_log=privacy_log, local_only=lambda: settings.get("privacy.local_only"), timeout=timeout)`, `connections = ConnectionRegistry(db, secrets, catalogue, client_factory=client_factory)`; keep `client_factory` on `Services` too (used by `detect_local`).

- [ ] **Step 5: Tests, lint, pyright, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add -A && git commit -m "Add provider presets, model catalogue and connection registry"
```

---

### Task 7: Pseudonymiser

**Files:**
- Create: `src/tuppence/llm/pseudonymise.py`
- Modify: `src/tuppence/core/settings_store.py` (define `privacy.hidden_names` → `list[str]`, default `[]`, "Other names to hide from cloud AI (e.g. your landlord)")
- Test: `tests/llm/test_pseudonymise.py`

**Interfaces:**
- Produces: `Pseudonymiser(people: list[tuple[str, str]], hidden_names: list[str] = [])` where `people` = (display name, role); `redact(text: str) -> str`; `restore(text: str) -> str`; `restore_value(value: Any) -> Any` (recurses into dicts/lists of strings); `count: int` (number of replacements made); `role_labels(people) -> dict[str, str]` (adults `Adult A`, `Adult B`…; children `Child 1`…; dependent adults `Dependant 1`…); hidden names → `Person 1`…
- Replacement tokens: IBAN → `IBAN_n`; card/account numbers → `ACCT_n ending ••dd` (last two digits); sort code → `SORTCODE_n`; email → `EMAIL_n`; UK mobile/+44 numbers → `PHONE_n`; full postcode → `POSTCODE_n`; names → role labels. Same original within one instance → same token. Merchant names, amounts and dates are not touched.

- [ ] **Step 1: Write the failing tests**

`tests/llm/test_pseudonymise.py`:

```python
from tuppence.llm.pseudonymise import Pseudonymiser, role_labels

PEOPLE = [("Alex Example", "adult"), ("Sam Example", "adult"), ("Kid A", "child")]


def test_role_labels():
    assert role_labels(PEOPLE) == {"Alex Example": "Adult A", "Sam Example": "Adult B", "Kid A": "Child 1"}


def test_redacts_identifiers_but_not_money_dates_or_merchants():
    p = Pseudonymiser(PEOPLE, hidden_names=["Jo Landlord"])
    text = (
        "03/09/2026 TESCO STORES 3123 -£54.20\n"
        "04/09/2026 TO A EXAMPLE sort code 12-34-56 account no 12345678 £300.00\n"
        "Card 4111 1111 1111 1234 used by Alex Example; Sam Example paid Jo Landlord £1,450.00\n"
        "Contact alex@example.com or 07700 900123; IBAN GB33BUKB20201555555555; LS6 2AB\n"
        "OFX date 20260907 and ref 99887766"
    )
    out = p.redact(text)
    for secret in ["12-34-56", "12345678", "4111 1111 1111 1234", "Alex Example", "Sam Example", "Jo Landlord",
                   "alex@example.com", "07700 900123", "GB33BUKB20201555555555", "LS6 2AB"]:
        assert secret not in out, secret
    for kept in ["TESCO STORES 3123", "-£54.20", "03/09/2026", "£1,450.00", "20260907", "99887766"]:
        assert kept in out, kept
    assert "Adult A" in out and "Adult B" in out and "Person 1" in out
    assert "ACCT_1 ending ••78" in out and "ending ••34" in out and "SORTCODE_1" in out
    assert p.count >= 10


def test_consistent_tokens_and_restore():
    p = Pseudonymiser(PEOPLE)
    a = p.redact("Alex Example paid sort code 12-34-56")
    b = p.redact("again sort code 12-34-56 for Alex Example")
    assert "SORTCODE_1" in a and "SORTCODE_1" in b
    reply = "Adult A sends money via SORTCODE_1; ACCT tokens unchanged"
    assert p.restore(reply) == "Alex Example sends money via 12-34-56; ACCT tokens unchanged"


def test_restore_value_recurses_and_handles_bare_account_token():
    p = Pseudonymiser(PEOPLE)
    p.redact("account number 87654321")
    value = {"who": "Adult A", "items": ["ACCT_1", "ACCT_1 ending ••21"]}
    assert p.restore_value(value) == {"who": "Alex Example", "items": ["87654321", "87654321"]}


def test_names_are_word_bounded_and_case_insensitive():
    p = Pseudonymiser([("Alex Example", "adult")])
    assert p.redact("ALEX EXAMPLE and alexandra examples") == "Adult A and alexandra examples"
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

`src/tuppence/llm/pseudonymise.py`:

```python
"""Optional stand-ins for names and numbers before cloud calls (spec §4.6).

Consistent tokens keep the agent's reasoning intact ("Adult A pays ACCT_2"); the mapping
never leaves the machine and replies are restored locally. Merchants, amounts and dates
are deliberately left alone.
"""

from __future__ import annotations

import re
import string
from typing import Any

_IBAN = re.compile(r"\bGB\d{2}\s?[A-Z]{4}(?:\s?\d){14}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_SORT = re.compile(r"\b\d{2}-\d{2}-\d{2}\b")
_ACCOUNT = re.compile(r"(?i)\b(acc(?:ount)?|a/c)(\.?\s*(?:no\.?|number|num)?\s*[:#]?\s*)(\d{8})\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b")
_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b")


def role_labels(people: list[tuple[str, str]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    adults = children = dependants = 0
    for name, role in people:
        if role == "adult":
            labels[name] = f"Adult {string.ascii_uppercase[adults % 26]}"
            adults += 1
        elif role == "child":
            children += 1
            labels[name] = f"Child {children}"
        else:
            dependants += 1
            labels[name] = f"Dependant {dependants}"
    return labels


class Pseudonymiser:
    def __init__(self, people: list[tuple[str, str]], hidden_names: list[str] | None = None) -> None:
        self.names = role_labels(people)
        for i, name in enumerate(hidden_names or [], start=1):
            self.names.setdefault(name, f"Person {i}")
        self.forward: dict[str, str] = {}
        # role labels are always restorable, even if the model mentions someone the prompt didn't
        self.reverse: dict[str, str] = {label: name for name, label in self.names.items()}
        self.counters: dict[str, int] = {}
        self.count = 0

    def _token(self, kind: str, original: str, digits: str | None = None) -> str:
        key = f"{kind}:{re.sub(r'[\s-]', '', original)}"
        if key not in self.forward:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            base = f"{kind}_{self.counters[kind]}"
            token = f"{base} ending ••{digits[-2:]}" if digits else base
            self.forward[key] = token
            self.reverse[token] = original
            if digits:
                self.reverse[base] = original
        self.count += 1
        return self.forward[key]

    def redact(self, text: str) -> str:
        text = _IBAN.sub(lambda m: self._token("IBAN", m.group(0)), text)
        text = _SORT.sub(lambda m: self._token("SORTCODE", m.group(0)), text)
        text = _ACCOUNT.sub(lambda m: m.group(1) + m.group(2) + self._token("ACCT", m.group(3), m.group(3)), text)

        def card(m: re.Match[str]) -> str:
            digits = re.sub(r"\D", "", m.group(0))
            if not 13 <= len(digits) <= 19:
                return m.group(0)
            return self._token("ACCT", m.group(0), digits)

        text = _CARD.sub(card, text)
        text = _EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), text)
        text = _PHONE.sub(lambda m: self._token("PHONE", m.group(0)), text)
        text = _POSTCODE.sub(lambda m: self._token("POSTCODE", m.group(0)), text)
        for name in sorted(self.names, key=len, reverse=True):
            pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)

            def swap(m: re.Match[str], name: str = name) -> str:
                self.count += 1
                return self.names[name]

            text = pattern.sub(swap, text)
        return text

    def restore(self, text: str) -> str:
        for token in sorted(self.reverse, key=len, reverse=True):
            text = text.replace(token, self.reverse[token])
        return text

    def restore_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, list):
            return [self.restore_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self.restore_value(v) for k, v in value.items()}
        return value
```

> Watch the ordering in `redact`: IBAN and sort codes before account/card numbers (so a sort code's digits aren't eaten by the card pattern), the 8-digit account rule only with an account keyword nearby (so dates like `20260907` and references survive), and the card rule only for 13–19 digits. Run the test after any change to these regexes.

- [ ] **Step 4: Tests, lint, pyright, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add -A && git commit -m "Add pseudonymiser for optional cloud-call redaction"
```

---

### Task 8: Routing, budgets, usage and the LLM client

**Files:**
- Create: `src/tuppence/llm/routing.py`, `src/tuppence/llm/budget.py`, `src/tuppence/llm/client.py`
- Modify: `src/tuppence/core/settings_store.py` (define `llm.mode` → `Literal["simple","advanced"]`, default `"simple"`; `llm.simple_model` → `dict[str, str] | None`, default `None`), `src/tuppence/app/services.py` (add `router`, `usage`, `breakers`, `llm`)
- Test: `tests/llm/test_routing.py`, `tests/llm/test_budget.py`, `tests/llm/test_client.py`

**Interfaces:**
- Produces:
  - `TaskRouter(db, settings, connections)`: `view() -> RoutingView` (`mode`, `mode_version`, `simple_model: {"connection_id","model_id"} | None`, `simple_version`, `tasks: {task: {"chain": [...], "local_only": bool, "version": int}}`), `set_task(task, chain: list[dict], local_only: bool, expected_version: int) -> RoutingView`, `chain_for(task) -> list[tuple[Connection, ModelInfo]]` (enabled connections only; `local_only` route keeps only local; vision keeps only `supports_vision`; empty → `NoModelConfigured("Choose an AI model in Settings › AI.")`)
  - `TASK_TIMEOUTS = {"read": 400, "categorise": 120, "review": 120, "research": 120, "coach": 60, "report": 120, "vision": 300}`; `timeout_for(task, is_local) -> float` (×3 when local)
  - `estimate_tokens(messages, tools=()) -> int` (characters ÷ 4, rounded up, + 8 per message)
  - `cost_gbp(model: ModelInfo, usage: Usage, usd_to_gbp: float) -> float | None`
  - `RunBudget(max_calls, max_tokens, max_gbp, max_seconds, *, monotonic=time.monotonic)` with `check(estimated_tokens) -> None` (raises `BudgetExceeded` naming which cap) and `record(tokens, gbp) -> None`; `RunBudget.from_manifest(budgets: Budgets)`
  - `UsageLedger(db)`: `record(task, connection_id, model_id, usage, cost_gbp, ok, error=None, run_id=None)`, `month_spend_gbp(year, month) -> float`, `summary(year, month) -> dict` (`total_gbp`, `calls`, `by_task`, `by_model`, `by_day`)
  - `BreakerBoard(threshold=3, reset_s=60, *, monotonic=time.monotonic)`: `allow(key) -> bool`, `success(key)`, `failure(key)`
  - `LLMClient(*, connections, router, usage, settings, household, breakers, sleep=time.sleep)`: `chat(task, messages, *, tools=(), json_schema=None, schema_name="result", max_tokens=4096, run: RunBudget | None = None, run_id=None) -> ChatResult` where `ChatResult` = `ChatResponse` fields + `connection_id`, `model_id`, `redactions`, `cost_gbp`; `structured(task, messages, schema: type[T], *, max_tokens=4096, run=None) -> T`
  - Errors the UI shows are the exception messages, so write them for people: e.g. `NoticeRequired("Confirm what <name> will see before Tuppence uses it (Settings › AI).")`, `ContextTooLarge("This is too much text for <model> (about N tokens; it fits M). Choose a model with a bigger context window.")`, `BudgetExceeded("This month's AI spending cap (£10.00) would be exceeded.")`

- [ ] **Step 1: Write the failing tests**

`tests/llm/test_budget.py`:

```python
import pytest

from tuppence.llm.budget import BreakerBoard, RunBudget, cost_gbp, estimate_tokens
from tuppence.llm.connections import ModelInfo
from tuppence.llm.types import BudgetExceeded, Message, Usage


def model(**kw):
    base = dict(connection_id="c", model_id="m", display_name=None, context_window=8000, max_output_tokens=None,
                supports_tools=False, supports_json_schema=False, supports_vision=False,
                price_in_usd_per_mtok=2.0, price_out_usd_per_mtok=8.0, source="catalogue")
    base.update(kw)
    return ModelInfo(**base)


def test_estimate_tokens():
    assert estimate_tokens([Message(role="user", content="x" * 400)]) == 108


def test_cost():
    assert cost_gbp(model(), Usage(input_tokens=1_000_000, output_tokens=500_000), 0.75) == pytest.approx(4.5)
    assert cost_gbp(model(price_in_usd_per_mtok=None), Usage(input_tokens=10), 0.75) is None


def test_run_budget_caps():
    t = [0.0]
    b = RunBudget(max_calls=2, max_tokens=1000, max_gbp=0.10, max_seconds=30, monotonic=lambda: t[0])
    b.check(400)
    b.record(400, 0.05)
    with pytest.raises(BudgetExceeded, match="tokens"):
        b.check(700)
    b.record(100, 0.06)
    with pytest.raises(BudgetExceeded, match="calls"):
        b.check(1)
    b2 = RunBudget(max_calls=10, max_tokens=10_000, max_gbp=1, max_seconds=30, monotonic=lambda: t[0])
    t[0] = 31
    with pytest.raises(BudgetExceeded, match="time"):
        b2.check(1)


def test_breaker_opens_and_resets():
    t = [0.0]
    br = BreakerBoard(threshold=3, reset_s=60, monotonic=lambda: t[0])
    for _ in range(3):
        assert br.allow("c")
        br.failure("c")
    assert not br.allow("c")
    t[0] = 61
    assert br.allow("c")  # half-open trial
    br.success("c")
    assert br.allow("c")
```

`tests/llm/test_routing.py` and `tests/llm/test_client.py` share a scripted fake server. Create `tests/fakes/__init__.py` (empty) and `tests/fakes/scripted.py` (tests/ is on `sys.path` via the root conftest, so tests import it as `fakes.scripted`):

```python
"""A fake OpenAI-compatible server whose replies are scripted per call."""

import json

import httpx


class Scripted:
    def __init__(self):
        self.replies = []
        self.requests = []
        self.handler = self._default  # tests may replace this attribute

    def _default(self, req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m-small"}, {"id": "m-big"}]})
        self.requests.append(json.loads(req.content))
        reply = self.replies.pop(0) if self.replies else {"content": "ok"}
        if isinstance(reply, httpx.Response):
            return reply
        if callable(reply):
            return reply(req)
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": reply["content"]}}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(lambda req: self.handler(req))  # looked up per request
```

`tests/llm/conftest.py`:

```python
import httpx
import pytest
from fakes.scripted import Scripted

from tuppence.app.services import build_services
from tuppence.settings import RuntimeSettings


@pytest.fixture
def env(tmp_path, monkeypatch):
    scripted = Scripted()
    services = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    from tuppence.net import client as netclient

    real = netclient.make_client

    def fake_make_client(ctx, *, privacy_log, local_only, timeout, transport=None):
        return real(ctx, privacy_log=privacy_log, local_only=local_only, timeout=timeout, transport=scripted.transport())

    monkeypatch.setattr(netclient, "make_client", fake_make_client)
    services.llm.sleep = lambda s: scripted.requests.append({"slept": s})
    return services, scripted
```

> For this fixture to work, `Services` must build its client factory by calling `tuppence.net.client.make_client` **at call time** (e.g. `lambda ctx, timeout: netclient.make_client(...)` where `netclient` is the imported module), not a reference captured at import.

`tests/llm/test_routing.py`:

```python
import pytest

from tuppence.llm.types import NoModelConfigured


def test_no_model_configured(env):
    services, _ = env
    with pytest.raises(NoModelConfigured):
        services.router.chain_for("coach")


def test_simple_mode_and_advanced_chain(env):
    services, _ = env
    local = services.connections.create("custom", base_url="http://127.0.0.1:9000/v1")
    services.connections.test(local.id)
    services.settings.set("llm.simple_model", {"connection_id": local.id, "model_id": "m-small"}, expected_version=0)
    [(conn, model)] = services.router.chain_for("coach")
    assert conn.id == local.id and model.model_id == "m-small"

    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task("coach", [{"connection_id": local.id, "model_id": "m-big"}, {"connection_id": local.id, "model_id": "m-small"}],
                             local_only=False, expected_version=0)
    assert [m.model_id for _, m in services.router.chain_for("coach")] == ["m-big", "m-small"]
    # tasks without their own chain fall back to the simple model
    assert [m.model_id for _, m in services.router.chain_for("report")] == ["m-small"]


def test_local_only_route_filters_cloud(env):
    services, _ = env
    cloud = services.connections.create("openai", api_key="k", base_url="http://127.0.0.1:9001/v1")
    services.connections.test(cloud.id)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task("read", [{"connection_id": cloud.id, "model_id": "m-small"}], local_only=True, expected_version=0)
    with pytest.raises(NoModelConfigured):
        services.router.chain_for("read")
```

`tests/llm/test_client.py`:

```python
import httpx
import pytest
from pydantic import BaseModel

from tuppence.core.household import PersonIn
from tuppence.llm.budget import RunBudget
from tuppence.llm.types import AllModelsFailed, BudgetExceeded, ContextTooLarge, LLMBadResponse, Message


def setup_local(services, model="m-small"):
    c = services.connections.create("custom", base_url="http://127.0.0.1:9000/v1")
    services.connections.test(c.id)
    services.settings.set("llm.simple_model", {"connection_id": c.id, "model_id": model}, expected_version=0)
    return c


def setup_cloud(services):
    c = services.connections.create("openai", api_key="sk-x", base_url="http://127.0.0.1:9100/v1")
    services.connections.test(c.id)
    services.connections.acknowledge_notice(c.id)
    services.settings.set("llm.simple_model", {"connection_id": c.id, "model_id": "m-small"}, expected_version=0)
    return c


U = [Message(role="user", content="hello")]


def test_chat_records_usage_and_privacy_log(env):
    services, scripted = env
    c = setup_local(services)
    scripted.replies = [{"content": "hi there"}]
    r = services.llm.chat("coach", U)
    assert r.text == "hi there" and r.connection_id == c.id and r.cost_gbp == 0.0
    summary = services.usage.summary(*map(int, __import__("datetime").date.today().isoformat().split("-")[:2]))
    assert summary["calls"] == 1
    assert any(e.outcome == "sent" and e.task == "coach" for e in services.privacy_log.list())


def test_retry_after_then_success(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [httpx.Response(429, headers={"Retry-After": "2"}, json={}), {"content": "ok"}]
    assert services.llm.chat("coach", U).text == "ok"
    assert {"slept": 2.0} in scripted.requests


def test_falls_back_to_next_model_on_failure(env):
    services, scripted = env
    c = setup_local(services)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task("coach", [{"connection_id": c.id, "model_id": "m-big"}, {"connection_id": c.id, "model_id": "m-small"}],
                             local_only=False, expected_version=0)
    scripted.replies = [httpx.Response(500, json={})] * 3 + [{"content": "from small"}]
    r = services.llm.chat("coach", U)
    assert r.text == "from small" and r.model_id == "m-small"


def test_all_fail_raises_with_reasons(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [httpx.Response(400, json={"error": "bad"})]
    with pytest.raises(AllModelsFailed) as exc:
        services.llm.chat("coach", U)
    assert "HTTP 400" in str(exc.value)


def test_cloud_needs_notice_first(env):
    services, _ = env
    c = services.connections.create("openai", api_key="sk-x", base_url="http://127.0.0.1:9100/v1")
    services.connections.test(c.id)
    services.settings.set("llm.simple_model", {"connection_id": c.id, "model_id": "m-small"}, expected_version=0)
    with pytest.raises(AllModelsFailed, match="Confirm what"):
        services.llm.chat("coach", U)


def test_local_only_blocks_cloud_before_sending(env):
    services, scripted = env
    setup_cloud(services)
    services.settings.set("privacy.local_only", True, expected_version=0)
    with pytest.raises(AllModelsFailed, match="Local only"):
        services.llm.chat("coach", U)
    assert scripted.requests == []
    assert services.privacy_log.list()[0].outcome == "blocked"


def test_pseudonymise_redacts_outbound_and_restores_reply(env):
    services, scripted = env
    services.household.create_person(PersonIn(display_name="Alex Example", role="adult"))
    setup_cloud(services)
    services.settings.set("privacy.pseudonymise", True, expected_version=0)
    scripted.replies = [lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "Hello Adult A"}}], "usage": {}})]
    r = services.llm.chat("coach", [Message(role="user", content="I am Alex Example")])
    assert "Alex Example" not in str(scripted.requests[-1]) and "Adult A" in str(scripted.requests[-1])
    assert r.text == "Hello Alex Example" and r.redactions >= 1


def test_pseudonymise_never_applies_to_local(env):
    services, scripted = env
    services.household.create_person(PersonIn(display_name="Alex Example", role="adult"))
    setup_local(services)
    services.settings.set("privacy.pseudonymise", True, expected_version=0)
    services.llm.chat("coach", [Message(role="user", content="I am Alex Example")])
    assert "Alex Example" in str(scripted.requests[-1])


def test_context_too_large_fails_fast(env):
    services, scripted = env
    setup_local(services)  # default local context 4096
    with pytest.raises(AllModelsFailed, match="too much text"):
        services.llm.chat("coach", [Message(role="user", content="x" * 40_000)])
    assert scripted.requests == []


def test_run_budget_enforced(env):
    services, _ = env
    setup_local(services)
    run = RunBudget(max_calls=1, max_tokens=100_000, max_gbp=1, max_seconds=60)
    services.llm.chat("coach", U, run=run)
    with pytest.raises(BudgetExceeded):
        services.llm.chat("coach", U, run=run)


def test_monthly_cap(env):
    services, _ = env
    setup_cloud(services)
    # make the model expensive and the cap tiny
    with services.db.transaction() as conn:
        conn.execute("UPDATE llm_model SET price_in_usd_per_mtok = 1000000, price_out_usd_per_mtok = 1000000")
    services.settings.set("llm.monthly_cap_gbp", 0.01, expected_version=0)
    with pytest.raises(AllModelsFailed, match="spending cap"):
        services.llm.chat("coach", U)


class Verdict(BaseModel):
    category: str
    confidence: float


def test_structured_extracts_from_prose_and_repairs_once(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [{"content": "Sure! {\"category\": \"groceries\"}"},  # invalid: missing confidence
                        {"content": "```json\n{\"category\": \"groceries\", \"confidence\": 0.9}\n```"}]
    v = services.llm.structured("categorise", U, Verdict)
    assert v == Verdict(category="groceries", confidence=0.9)
    assert len([r for r in scripted.requests if "messages" in r]) == 2


def test_structured_gives_up_after_one_repair(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [{"content": "nope"}, {"content": "still nope"}]
    with pytest.raises(LLMBadResponse):
        services.llm.structured("categorise", U, Verdict)


def test_structured_uses_native_schema_when_supported(env):
    services, scripted = env
    setup_local(services)
    with services.db.transaction() as conn:
        conn.execute("UPDATE llm_model SET supports_json_schema = 1")
    scripted.replies = [{"content": "{\"category\": \"bills\", \"confidence\": 0.5}"}]
    services.llm.structured("categorise", U, Verdict)
    assert scripted.requests[-1]["response_format"]["type"] == "json_schema"
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

`src/tuppence/llm/budget.py`:

```python
"""Token estimates, prices, per-run budgets, monthly spend and circuit breakers (spec §4.5)."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from tuppence.config.models import Budgets
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.llm.connections import ModelInfo
from tuppence.llm.types import BudgetExceeded, Message, ToolSpec, Usage


def estimate_tokens(messages: Iterable[Message], tools: Iterable[ToolSpec] = ()) -> int:
    chars = 0
    count = 0
    for m in messages:
        count += 1
        chars += len(m.content) + sum(len(json.dumps(c.arguments)) + len(c.name) for c in m.tool_calls)
    chars += sum(len(json.dumps(t.model_dump())) for t in tools)
    return math.ceil(chars / 4) + 8 * count


def cost_gbp(model: ModelInfo, usage: Usage, usd_to_gbp: float) -> float | None:
    if model.price_in_usd_per_mtok is None or model.price_out_usd_per_mtok is None:
        return None
    usd = (usage.input_tokens * model.price_in_usd_per_mtok + usage.output_tokens * model.price_out_usd_per_mtok) / 1e6
    return round(usd * usd_to_gbp, 6)


@dataclass
class RunBudget:
    max_calls: int
    max_tokens: int
    max_gbp: float
    max_seconds: float
    monotonic: Callable[[], float] = time.monotonic
    calls: int = 0
    tokens: int = 0
    gbp: float = 0.0
    started: float = field(default=-1.0)

    def __post_init__(self) -> None:
        if self.started < 0:
            self.started = self.monotonic()

    @classmethod
    def from_manifest(cls, budgets: Budgets) -> RunBudget:
        return cls(budgets.max_llm_calls, budgets.max_tokens, budgets.max_gbp, budgets.max_seconds)

    def check(self, estimated_tokens: int) -> None:
        if self.calls + 1 > self.max_calls:
            raise BudgetExceeded(f"This run reached its limit of {self.max_calls} AI calls.")
        if self.tokens + estimated_tokens > self.max_tokens:
            raise BudgetExceeded(f"This run reached its limit of {self.max_tokens:,} tokens.")
        if self.gbp >= self.max_gbp:
            raise BudgetExceeded(f"This run reached its £{self.max_gbp:.2f} spending limit.")
        if self.monotonic() - self.started > self.max_seconds:
            raise BudgetExceeded(f"This run reached its time limit of {self.max_seconds:.0f} seconds.")

    def record(self, tokens: int, gbp: float | None) -> None:
        self.calls += 1
        self.tokens += tokens
        self.gbp += gbp or 0.0


class UsageLedger:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self, task: str, connection_id: str | None, model_id: str, usage: Usage, cost: float | None, ok: bool,
        error: str | None = None, run_id: str | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO llm_usage (ts, run_id, task, connection_id, model_id, input_tokens, output_tokens, cost_gbp, ok, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [to_iso(utcnow()), run_id, task, connection_id, model_id, usage.input_tokens, usage.output_tokens,
                 cost, int(ok), (error or "")[:500] or None],
            )

    def _month_rows(self, year: int, month: int) -> list[Any]:
        start = date(year, month, 1)
        end = date(year + (month == 12), month % 12 + 1, 1)
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT * FROM llm_usage WHERE ts >= ? AND ts < ?", [start.isoformat(), end.isoformat()]
            ).fetchall()

    def month_spend_gbp(self, year: int, month: int) -> float:
        return round(sum(r["cost_gbp"] or 0.0 for r in self._month_rows(year, month)), 6)

    def summary(self, year: int, month: int) -> dict[str, Any]:
        rows = self._month_rows(year, month)
        out: dict[str, Any] = {"total_gbp": 0.0, "calls": len(rows), "by_task": {}, "by_model": {}, "by_day": {}}
        for r in rows:
            gbp = r["cost_gbp"] or 0.0
            tokens = r["input_tokens"] + r["output_tokens"]
            out["total_gbp"] += gbp
            for bucket, key in (("by_task", r["task"]), ("by_model", r["model_id"]), ("by_day", r["ts"][:10])):
                agg = out[bucket].setdefault(key, {"calls": 0, "tokens": 0, "gbp": 0.0})
                agg["calls"] += 1
                agg["tokens"] += tokens
                agg["gbp"] += gbp
        out["total_gbp"] = round(out["total_gbp"], 4)
        return out


class BreakerBoard:
    def __init__(self, threshold: int = 3, reset_s: float = 60.0, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.threshold, self.reset_s, self.monotonic = threshold, reset_s, monotonic
        self.failures: dict[str, int] = {}
        self.opened_at: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        opened = self.opened_at.get(key)
        if opened is None:
            return True
        if self.monotonic() - opened >= self.reset_s:
            del self.opened_at[key]
            self.failures[key] = self.threshold - 1  # half-open: one more failure re-opens
            return True
        return False

    def success(self, key: str) -> None:
        self.failures.pop(key, None)
        self.opened_at.pop(key, None)

    def failure(self, key: str) -> None:
        self.failures[key] = self.failures.get(key, 0) + 1
        if self.failures[key] >= self.threshold:
            self.opened_at[key] = self.monotonic()
```

`src/tuppence/llm/routing.py`:

```python
"""Which model handles which task (spec §4.3)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from tuppence.config.models import Task
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.settings_store import SettingsStore
from tuppence.llm.connections import Connection, ConnectionRegistry, ModelInfo
from tuppence.llm.types import NoModelConfigured

TASKS: tuple[str, ...] = ("read", "categorise", "review", "research", "coach", "report", "vision")
TASK_TIMEOUTS: dict[str, float] = {
    "read": 400, "categorise": 120, "review": 120, "research": 120, "coach": 60, "report": 120, "vision": 300,
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
    def __init__(self, db: Database, settings: SettingsStore, connections: ConnectionRegistry) -> None:
        self.db, self.settings, self.connections = db, settings, connections

    def _routes(self) -> dict[str, TaskRoute]:
        with self.db.connection() as conn:
            rows = {r["task"]: r for r in conn.execute("SELECT * FROM llm_route")}
        return {
            t: TaskRoute(chain=json.loads(rows[t]["chain"]), local_only=bool(rows[t]["local_only"]), version=rows[t]["version"])
            if t in rows else TaskRoute(chain=[], local_only=False, version=0)
            for t in TASKS
        }

    def view(self) -> RoutingView:
        mode = self.settings.entry("llm.mode")
        simple = self.settings.entry("llm.simple_model")
        return RoutingView(mode=mode.value, mode_version=mode.version, simple_model=simple.value,
                           simple_version=simple.version, tasks=self._routes())

    def set_task(self, task: str, chain: list[dict[str, Any]], local_only: bool, expected_version: int) -> RoutingView:
        if task not in TASKS:
            raise InputError(f"Unknown task '{task}'.")
        clean = []
        for ref in chain:
            self.connections.model(str(ref["connection_id"]), str(ref["model_id"]))  # NotFound if bogus
            clean.append({"connection_id": str(ref["connection_id"]), "model_id": str(ref["model_id"])})
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            row = conn.execute("SELECT version FROM llm_route WHERE task = ?", [task]).fetchone()
            current = 0 if row is None else int(row["version"])
            if current != expected_version:
                raise VersionConflict("llm_route", task, expected_version, current)
            conn.execute(
                "INSERT INTO llm_route (task, chain, local_only, version, updated_at) VALUES (?, ?, ?, 1, ?)"
                " ON CONFLICT(task) DO UPDATE SET chain = excluded.chain, local_only = excluded.local_only,"
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
```

`src/tuppence/llm/client.py`:

```python
"""One client for every task: routing, budgets, privacy, retries, fallback (spec §4)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from tuppence.core.household import HouseholdService
from tuppence.core.settings_store import SettingsStore
from tuppence.llm.budget import BreakerBoard, RunBudget, UsageLedger, cost_gbp, estimate_tokens
from tuppence.llm.connections import ConnectionRegistry
from tuppence.llm.jsonextract import extract_json, to_strict_schema
from tuppence.llm.pseudonymise import Pseudonymiser
from tuppence.llm.routing import TaskRouter, timeout_for
from tuppence.llm.types import (
    AllModelsFailed, BudgetExceeded, ChatRequest, ChatResponse, ContextTooLarge, LLMBadResponse, LLMError,
    Message, NoticeRequired, ToolCall, ToolSpec, Usage,
)
from tuppence.net.client import LocalOnlyBlocked

T = TypeVar("T", bound=BaseModel)
MAX_RETRIES = 2
BACKOFF = (1.0, 4.0)


class ChatResult(ChatResponse):
    connection_id: str
    model_id: str
    redactions: int
    cost_gbp: float | None


class LLMClient:
    def __init__(
        self, *, connections: ConnectionRegistry, router: TaskRouter, usage: UsageLedger, settings: SettingsStore,
        household: HouseholdService, breakers: BreakerBoard, sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.connections, self.router, self.usage = connections, router, usage
        self.settings, self.household, self.breakers, self.sleep = settings, household, breakers, sleep

    def _pseudonymiser(self) -> Pseudonymiser:
        people = [(p.display_name, p.role) for p in self.household.list_people(include_retired=True)]
        return Pseudonymiser(people, self.settings.get("privacy.hidden_names"))

    def _call_with_retries(self, provider: Any, req: ChatRequest) -> ChatResponse:
        attempt = 0
        while True:
            try:
                return provider.chat(req)
            except LLMError as exc:
                if not exc.retryable or attempt >= MAX_RETRIES:
                    raise
                wait = getattr(exc, "retry_after", None)
                self.sleep(min(30.0, wait) if wait is not None else BACKOFF[attempt])
                attempt += 1

    def chat(
        self, task: str, messages: Sequence[Message], *, tools: Sequence[ToolSpec] = (), json_schema: dict[str, Any] | None = None,
        schema_name: str = "result", max_tokens: int = 4096, run: RunBudget | None = None, run_id: str | None = None,
    ) -> ChatResult:
        attempts: list[str] = []
        for conn, model in self.router.chain_for(task):
            label = f"{conn.name} / {model.model_id}"
            if not self.breakers.allow(conn.id):
                attempts.append(f"{label}: paused after repeated failures")
                continue
            if conn.needs_notice:
                attempts.append(f"{label}: " + str(NoticeRequired(
                    f"Confirm what {conn.name} will see before Tuppence uses it (Settings › AI).")))
                continue
            estimate = estimate_tokens(messages, tools)
            if estimate > model.context_window * 0.6 or estimate + max_tokens > model.context_window:
                attempts.append(f"{label}: " + str(ContextTooLarge(
                    f"This is too much text for {model.model_id} (about {estimate:,} tokens; it fits "
                    f"{model.context_window:,}). Choose a model with a bigger context window.")))
                continue
            rate = self.settings.get("llm.usd_to_gbp")
            projected = cost_gbp(model, Usage(input_tokens=estimate, output_tokens=max_tokens), rate) or 0.0
            cap = self.settings.get("llm.monthly_cap_gbp")
            today = date.today()
            if projected and self.usage.month_spend_gbp(today.year, today.month) + projected > cap:
                attempts.append(f"{label}: " + str(BudgetExceeded(
                    f"This month's AI spending cap (£{cap:.2f}) would be exceeded.")))
                continue
            if run is not None:
                run.check(estimate + max_tokens)
            pseudo = self._pseudonymiser() if (not conn.is_local and self.settings.get("privacy.pseudonymise")) else None
            sent = [m.model_copy(update={"content": pseudo.redact(m.content)}) for m in messages] if pseudo else list(messages)
            req = ChatRequest(model=model.model_id, messages=sent, tools=list(tools), json_schema=json_schema,
                              schema_name=schema_name, max_tokens=max_tokens)
            provider = self.connections.provider(conn.id, task=task, redactions=pseudo.count if pseudo else 0,
                                                 timeout=timeout_for(task, conn.is_local))
            try:
                resp = self._call_with_retries(provider, req)
            except LocalOnlyBlocked as exc:
                attempts.append(f"{label}: {exc}")
                continue
            except LLMError as exc:
                self.breakers.failure(conn.id)
                self.usage.record(task, conn.id, model.model_id, Usage(), None, ok=False, error=str(exc), run_id=run_id)
                attempts.append(f"{label}: {exc}")
                continue
            finally:
                provider.client.close()
            self.breakers.success(conn.id)
            cost = cost_gbp(model, resp.usage, rate)
            self.usage.record(task, conn.id, model.model_id, resp.usage, cost, ok=True, run_id=run_id)
            if run is not None:
                run.record(resp.usage.input_tokens + resp.usage.output_tokens, cost)
            text = pseudo.restore(resp.text) if pseudo else resp.text
            calls = [ToolCall(id=c.id, name=c.name, arguments=pseudo.restore_value(c.arguments)) for c in resp.tool_calls] if pseudo else resp.tool_calls
            return ChatResult(
                text=text, tool_calls=calls, usage=resp.usage, model=resp.model, finish_reason=resp.finish_reason,
                connection_id=conn.id, model_id=model.model_id, redactions=pseudo.count if pseudo else 0, cost_gbp=cost,
            )
        raise AllModelsFailed(attempts)

    def structured(
        self, task: str, messages: Sequence[Message], schema: type[T], *, max_tokens: int = 4096, run: RunBudget | None = None,
    ) -> T:
        strict = to_strict_schema(schema.model_json_schema())
        native = self.router.chain_for(task)[0][1].supports_json_schema
        instruction = Message(role="system", content=(
            "Reply with only a JSON value that matches this JSON Schema. No prose, no code fences.\n" + json.dumps(strict)
        ))
        convo = [instruction, *messages]
        first = self.chat(task, convo, json_schema=strict if native else None, schema_name=schema.__name__,
                          max_tokens=max_tokens, run=run)
        try:
            return schema.model_validate(extract_json(first.text))
        except (ValueError, ValidationError) as exc:
            problem = str(exc).splitlines()[0][:300]
        repair = [*convo, Message(role="assistant", content=first.text[:4000]),
                  Message(role="user", content=f"That wasn't valid: {problem}. Reply with only the corrected JSON.")]
        second = self.chat(task, repair, json_schema=strict if native else None, schema_name=schema.__name__,
                           max_tokens=max_tokens, run=run)
        try:
            return schema.model_validate(extract_json(second.text))
        except (ValueError, ValidationError) as exc:
            raise LLMBadResponse(f"The model's reply didn't match the expected format: {str(exc).splitlines()[0][:200]}") from exc
```

> `RunBudget.check` raising `BudgetExceeded` is deliberately **not** caught in `chat` — a run budget stops the whole run, not just one model.

Wire into `Services`: `usage = UsageLedger(db)`, `breakers = BreakerBoard()`, `router = TaskRouter(db, settings, connections)`, `llm = LLMClient(connections=connections, router=router, usage=usage, settings=settings, household=household, breakers=breakers)`.

- [ ] **Step 4: Tests, lint, pyright, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add -A && git commit -m "Add task routing, budgets, usage ledger and the LLM client"
```

---

### Task 9: AI, privacy and usage API

**Files:**
- Create: `src/tuppence/app/routes/llm.py`, `src/tuppence/app/routes/usage.py`
- Modify: `src/tuppence/app/routes/__init__.py`, `src/tuppence/app/errors.py` (map `LLMError` subclasses)
- Test: `tests/app/test_llm_api.py`

**Interfaces:**
- Routes (all protected):
  - `GET /api/llm/presets` → `{"presets": [{id, label, api_style, base_url, kind, key_required}]}`
  - `GET /api/llm/detect` → `{"servers": [DetectedServer]}`
  - `GET /api/llm/connections` → `{"connections": [Connection]}`; `POST /api/llm/connections` `{preset, name?, base_url?, api_key?}` → 201 Connection; `PATCH /api/llm/connections/{id}` `{changes, expected_version}`; `DELETE /api/llm/connections/{id}` → 204; `POST /api/llm/connections/{id}/acknowledge-notice` → Connection; `POST /api/llm/connections/{id}/test` → `{"ok": true, "models": [...]}` or `{"ok": false, "error": "..."}` (never 500 for provider errors)
  - `GET /api/llm/models?connection_id=` → `{"models": [ModelInfo]}`; `PATCH /api/llm/models/{connection_id}/{model_id}` `{context_window}` → ModelInfo (model ids may contain `/`: declare the path as `{model_id:path}`)
  - `GET /api/llm/routing` → RoutingView; `PUT /api/llm/routing/tasks/{task}` `{chain, local_only, expected_version}` → RoutingView (mode and simple model change through `PATCH /api/settings/llm.mode` and `/api/settings/llm.simple_model`)
  - `POST /api/llm/try` `{task: "coach", prompt}` → `{"text", "connection_id", "model_id", "redactions", "usage", "cost_gbp"}`; `AllModelsFailed`/`NoModelConfigured` → 502/409 with `{"detail": message}`
  - `GET /api/usage?month=YYYY-MM` (default current month) → summary + `"cap_gbp"`
- Error mapping in `install_error_handlers`: `NoModelConfigured` → 409; `AllModelsFailed` → 502; `BudgetExceeded` → 429; other `LLMError` → 502; always `{"detail": str(exc)}`

- [ ] **Step 1: Write the failing tests** — `tests/app/test_llm_api.py` uses the `client` fixture plus a monkeypatched `tuppence.net.client.make_client` routed to the shared `Scripted` fake from Task 8:

```python
import httpx
import pytest
from fakes.scripted import Scripted


@pytest.fixture
def scripted(monkeypatch):
    s = Scripted()
    from tuppence.net import client as netclient

    real = netclient.make_client
    monkeypatch.setattr(netclient, "make_client", lambda ctx, *, privacy_log, local_only, timeout, transport=None:
                        real(ctx, privacy_log=privacy_log, local_only=local_only, timeout=timeout,
                             transport=s.transport()))
    return s


def test_presets(client):
    ids = {p["id"] for p in client.get("/api/llm/presets").json()["presets"]}
    assert {"ollama", "anthropic", "openai", "gemini", "deepseek", "qwen", "kimi", "glm"} <= ids


def test_connection_lifecycle_and_try(client, scripted):
    r = client.post("/api/llm/connections", json={"preset": "openai", "api_key": "sk-secret", "base_url": "http://127.0.0.1:9100"})
    assert r.status_code == 201
    conn = r.json()
    assert conn["needs_notice"] and conn["has_key"] and "sk-secret" not in r.text
    assert conn["base_url"] == "http://127.0.0.1:9100/v1"

    test = client.post(f"/api/llm/connections/{conn['id']}/test").json()
    assert test["ok"] and {m["model_id"] for m in test["models"]} == {"m-small", "m-big"}

    client.patch("/api/settings/llm.simple_model", json={"value": {"connection_id": conn["id"], "model_id": "m-small"}, "expected_version": 0})
    blocked = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert blocked.status_code == 502 and "Confirm what" in blocked.json()["detail"]

    client.post(f"/api/llm/connections/{conn['id']}/acknowledge-notice")
    scripted.replies = [{"content": "Hello!"}]
    ok = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert ok.status_code == 200 and ok.json()["text"] == "Hello!"
    assert client.get("/api/privacy/log").json()["entries"][0]["outcome"] == "sent"
    assert client.get("/api/usage").json()["calls"] >= 1

    assert client.delete(f"/api/llm/connections/{conn['id']}").status_code == 204


def test_test_endpoint_reports_provider_errors(client, scripted):
    conn = client.post("/api/llm/connections", json={"preset": "custom", "base_url": "http://127.0.0.1:9200/v1"}).json()
    scripted.handler = lambda req: httpx.Response(401, json={"error": "bad key"})
    r = client.post(f"/api/llm/connections/{conn['id']}/test")
    assert r.status_code == 200 and r.json()["ok"] is False and "401" in r.json()["error"]


def test_try_without_model_is_409(client):
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 409 and "Settings › AI" in r.json()["detail"]


def test_model_ids_with_slashes(client, scripted):
    conn = client.post("/api/llm/connections", json={"preset": "custom", "base_url": "http://127.0.0.1:9300/v1"}).json()
    client.post(f"/api/llm/connections/{conn['id']}/test")
    r = client.patch(f"/api/llm/models/{conn['id']}/m-small", json={"context_window": 16384})
    assert r.status_code == 200 and r.json()["context_window"] == 16384
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

`src/tuppence/app/routes/llm.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.llm.connections import Connection, ModelInfo, detect_local
from tuppence.llm.presets import PRESETS
from tuppence.llm.routing import RoutingView
from tuppence.llm.types import LLMError, Message

router = APIRouter(prefix="/api/llm", tags=["llm"])


class ConnectionIn(BaseModel):
    preset: str
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ConnectionUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class ContextIn(BaseModel):
    context_window: int


class TaskRouteIn(BaseModel):
    chain: list[dict[str, str]]
    local_only: bool = False
    expected_version: int


class TryIn(BaseModel):
    task: str = "coach"
    prompt: str


@router.get("/presets")
def presets() -> dict[str, list[dict[str, Any]]]:
    return {"presets": [
        {"id": p.id, "label": p.label, "api_style": p.api_style, "base_url": p.base_url, "kind": p.kind,
         "key_required": p.key_required}
        for p in PRESETS.values()
    ]}


@router.get("/detect")
def detect(services: Services = Depends(get_services)) -> dict[str, Any]:
    return {"servers": detect_local(services.client_factory)}


@router.get("/connections")
def list_connections(services: Services = Depends(get_services)) -> dict[str, list[Connection]]:
    return {"connections": services.connections.list()}


@router.post("/connections", status_code=201)
def create_connection(body: ConnectionIn, services: Services = Depends(get_services)) -> Connection:
    return services.connections.create(body.preset, name=body.name, base_url=body.base_url, api_key=body.api_key)


@router.patch("/connections/{connection_id}")
def update_connection(connection_id: str, body: ConnectionUpdate, services: Services = Depends(get_services)) -> Connection:
    return services.connections.update(connection_id, body.changes, body.expected_version)


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(connection_id: str, services: Services = Depends(get_services)) -> Response:
    services.connections.delete(connection_id)
    return Response(status_code=204)


@router.post("/connections/{connection_id}/acknowledge-notice")
def acknowledge(connection_id: str, services: Services = Depends(get_services)) -> Connection:
    return services.connections.acknowledge_notice(connection_id)


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    services.connections.get(connection_id)
    try:
        return {"ok": True, "models": services.connections.test(connection_id)}
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - LocalOnlyBlocked and transport surprises become a friendly message
        return {"ok": False, "error": str(exc)}


@router.get("/models")
def list_models(connection_id: str | None = None, services: Services = Depends(get_services)) -> dict[str, list[ModelInfo]]:
    return {"models": services.connections.models(connection_id)}


@router.patch("/models/{connection_id}/{model_id:path}")
def set_context(connection_id: str, model_id: str, body: ContextIn, services: Services = Depends(get_services)) -> ModelInfo:
    return services.connections.set_context_window(connection_id, model_id, body.context_window)


@router.get("/routing")
def routing(services: Services = Depends(get_services)) -> RoutingView:
    return services.router.view()


@router.put("/routing/tasks/{task}")
def set_route(task: str, body: TaskRouteIn, services: Services = Depends(get_services)) -> RoutingView:
    return services.router.set_task(task, body.chain, body.local_only, body.expected_version)


@router.post("/try")
def try_model(body: TryIn, services: Services = Depends(get_services)) -> dict[str, Any]:
    result = services.llm.chat(body.task, [Message(role="user", content=body.prompt)], max_tokens=512)
    return {
        "text": result.text, "connection_id": result.connection_id, "model_id": result.model_id,
        "redactions": result.redactions, "usage": result.usage.model_dump(), "cost_gbp": result.cost_gbp,
    }
```

`src/tuppence/app/routes/usage.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.errors import InputError

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("")
def usage(month: str | None = None, services: Services = Depends(get_services)) -> dict[str, Any]:
    if month:
        try:
            year, mon = (int(x) for x in month.split("-"))
            date(year, mon, 1)
        except ValueError:
            raise InputError("Use the month format YYYY-MM.") from None
    else:
        today = date.today()
        year, mon = today.year, today.month
    out = services.usage.summary(year, mon)
    out["month"] = f"{year:04d}-{mon:02d}"
    out["cap_gbp"] = services.settings.get("llm.monthly_cap_gbp")
    return out
```

Error handlers (in `install_error_handlers`):

```python
    @app.exception_handler(NoModelConfigured)
    async def _no_model(_r: Request, exc: NoModelConfigured) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(BudgetExceeded)
    async def _budget(_r: Request, exc: BudgetExceeded) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=429)

    @app.exception_handler(LLMError)
    async def _llm(_r: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=502)
```

(Starlette picks the most specific registered class via the exception's MRO, so `NoModelConfigured` and `BudgetExceeded` win over `LLMError`.)

Add `llm.router` and `usage.router` to `PROTECTED`.

- [ ] **Step 4: Tests, lint, pyright, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add -A && git commit -m "Add AI connections, routing, try, privacy log and usage API"
```

---

### Task 10: Settings › AI, Settings › Privacy and Usage pages

**Files:**
- Create: `web/src/components/Modal.svelte`, `web/src/pages/settings/AI.svelte`, `web/src/pages/settings/Privacy.svelte`, `web/src/pages/Usage.svelte`
- Modify: `web/src/App.svelte` (routes `/settings/ai`, `/settings/privacy`, `/usage`), `web/src/components/Nav.svelte` (links "AI", "Privacy", "Usage")
- Test: `web/src/pages/settings/AI.test.ts`, `web/src/pages/settings/Privacy.test.ts`, `web/src/pages/Usage.test.ts`

**Interfaces:**
- Consumes the Task 9 API.
- UI behaviour (the tests pin these):
  - **AI page**
    - "Find local AI" button calls `/api/llm/detect` and lists servers with an "Add" button each.
    - "Add connection" form: provider `<select>` labelled "Provider" (presets), "Name", "Base URL" (prefilled from preset, editable), "API key" (`type=password`; hidden for local presets), button "Save connection".
    - Each connection card (`role=region`, `aria-label` = connection name):
      - shows Local/Internet badge;
      - "Test" button → model count text "N models found" or the error;
      - for cloud connections needing a notice, a "Review what's sent" button opens a **Modal** titled "Before you use <name>" containing the text "<name> will see the statement text Tuppence sends to it" and buttons "I understand" (calls acknowledge) and "Cancel";
      - "Remove" button.
    - "Model" section:
      - Simple mode `<select>` labelled "Model for everything" listing `connection name — model id` for all tested models;
      - a toggle "Advanced: choose per task" switching `llm.mode`;
      - in advanced mode a `<select>` per task labelled by task name, plus a "Keep this task on this device" checkbox.
    - "Try it" box: `<textarea>` labelled "Message", button "Send", shows the reply text or the error detail.
  - **Privacy page**
    - Toggles (checkboxes) labelled "Local only", "Pseudonymise before cloud AI", "Research lookups", "Live market data", "Data-pack updates", each saving immediately via `PATCH /api/settings/<key>` with its version.
    - A "Privacy log" table with columns Time, Purpose, Task, Destination, Sent, Received, Outcome.
  - **Usage page**: month total `£x.xx of £cap`, calls count, and a table by task and by model.

- [ ] **Step 1: Write the failing tests** (pattern as in M1a Task 7; one test per page). `AI.test.ts`: mock `/api/llm/presets`, `/api/llm/connections` (one cloud connection needing notice), `/api/llm/models`, `/api/llm/routing`, `/api/settings`; click "Review what's sent" → modal visible with the exact text → click "I understand" → asserts POST to `/acknowledge-notice` and the button disappears. Second test: type in "Message", click "Send" → mock `/api/llm/try` returns `{text: 'Hello!'}` → "Hello!" shown; when it returns 502 `{detail: 'Local only is on…'}` the detail is shown in an alert. `Privacy.test.ts`: toggling "Local only" sends `PATCH /api/settings/privacy.local_only` with `{value: true, expected_version: 0}`; the log table renders a `blocked` row. `Usage.test.ts`: renders `£1.23 of £10.00`.

```ts
// web/src/pages/settings/Privacy.test.ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Privacy from './Privacy.svelte'

afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } })

it('toggles Local only and shows blocked calls', async () => {
  const calls: Array<[string, RequestInit | undefined]> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push([url, init])
    if (url === '/api/settings') return json({ settings: [
      { key: 'privacy.local_only', value: false, default: false, version: 0, description: '' },
      { key: 'privacy.pseudonymise', value: false, default: false, version: 0, description: '' },
      { key: 'privacy.research_lookups', value: false, default: false, version: 0, description: '' },
      { key: 'privacy.live_market_data', value: true, default: true, version: 0, description: '' },
      { key: 'privacy.datapack_updates', value: true, default: true, version: 0, description: '' },
    ] })
    if (url.startsWith('/api/privacy/log')) return json({ entries: [
      { id: 1, ts: '2026-10-07T10:00:00Z', purpose: 'llm', task: 'coach', connection_id: 'c1', destination: 'api.openai.com',
        method: 'POST', path: '/v1/chat/completions', bytes_out: 0, bytes_in: 0, status: null, redactions: 0, outcome: 'blocked', note: 'Local only is on' },
    ] })
    if (url === '/api/settings/privacy.local_only') return json({ key: 'privacy.local_only', value: true, default: false, version: 1, description: '' })
    return json({ detail: 'unexpected' }, 500)
  }))
  render(Privacy)
  expect(await screen.findByText('api.openai.com')).toBeInTheDocument()
  expect(screen.getByText('blocked')).toBeInTheDocument()
  await fireEvent.click(screen.getByLabelText('Local only'))
  const patch = calls.find(([u, i]) => u === '/api/settings/privacy.local_only' && i?.method === 'PATCH')!
  expect(JSON.parse(patch[1]!.body as string)).toEqual({ value: true, expected_version: 0 })
})
```

Write `AI.test.ts` and `Usage.test.ts` in the same style with the behaviours listed above.

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

`web/src/components/Modal.svelte` — accessible dialog using `<dialog>`:

```svelte
<script lang="ts">
  import type { Snippet } from 'svelte'
  let { open = false, title, children, onclose }: { open?: boolean; title: string; children: Snippet; onclose: () => void } = $props()
  let dialog: HTMLDialogElement
  $effect(() => { if (open && !dialog.open) dialog.showModal?.(); if (!open && dialog.open) dialog.close() })
</script>

<dialog bind:this={dialog} aria-labelledby="modal-title" onclose={onclose} open={open || undefined}>
  <h2 id="modal-title">{title}</h2>
  {@render children()}
</dialog>

<style>
  dialog { border: 1px solid var(--line); border-radius: 12px; padding: 1.25rem; max-width: 32rem; background: var(--panel); color: var(--ink); }
  dialog::backdrop { background: rgb(0 0 0 / .4); }
</style>
```

(`open={open || undefined}` keeps it visible in jsdom, which lacks `showModal`.)

`web/src/pages/settings/Privacy.svelte`:

```svelte
<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import { api, ApiError } from '../../lib/api'

  type Entry = { key: string; value: unknown; version: number; description: string }
  type LogEntry = { id: number; ts: string; purpose: string; task: string | null; destination: string; bytes_out: number; bytes_in: number; outcome: string; note: string | null }

  const TOGGLES: Array<[string, string, string]> = [
    ['privacy.local_only', 'Local only', 'AI and research calls stay on this device or your home network.'],
    ['privacy.pseudonymise', 'Pseudonymise before cloud AI', 'Names and account numbers become stand-ins like "Adult A" and "ACCT_2". Off by default.'],
    ['privacy.research_lookups', 'Research lookups', 'Look up unknown merchant names online. Only merchant names are sent — never amounts or your details.'],
    ['privacy.live_market_data', 'Live market data', 'Interest rates, inflation, exchange rates and share prices. Share lookups reveal which tickers you hold.'],
    ['privacy.datapack_updates', 'Data-pack updates', 'Anonymous downloads of updated UK tax, benefit and rent data.'],
  ]

  let settings = $state<Record<string, Entry>>({})
  let log = $state<LogEntry[]>([])
  let error = $state('')

  const fmt = (n: number) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`)

  async function load() {
    const res = await api<{ settings: Entry[] }>('/api/settings')
    settings = Object.fromEntries(res.settings.map((s) => [s.key, s]))
    log = (await api<{ entries: LogEntry[] }>('/api/privacy/log?limit=100')).entries
  }
  onMount(() => { load().catch((e) => (error = e instanceof ApiError ? e.detail : 'Something went wrong.')) })

  async function toggle(key: string, value: boolean) {
    error = ''
    try {
      const entry = await api<Entry>(`/api/settings/${key}`, { method: 'PATCH', body: { value, expected_version: settings[key].version } })
      settings[key] = entry
    } catch (e) { error = e instanceof ApiError ? e.detail : 'Something went wrong.'; await load() }
  }
</script>

<section>
  <h1>Privacy</h1>
  <p>With a local model, nothing leaves your machine. With a cloud model, your statement text goes to the provider you chose, and every call is logged below.</p>
  <Notice message={error} />
  <div class="card">
    {#each TOGGLES as [key, label, help]}
      {#if settings[key]}
        <div class="toggle">
          <label><input type="checkbox" checked={settings[key].value === true}
            onchange={(e) => toggle(key, (e.currentTarget as HTMLInputElement).checked)} /> {label}</label>
          <p class="hint">{help}</p>
        </div>
      {/if}
    {/each}
  </div>
  <div class="card">
    <h2>Privacy log</h2>
    {#if log.length === 0}<p>Nothing has left this machine yet.</p>{:else}
      <table>
        <thead><tr><th>Time</th><th>Purpose</th><th>Task</th><th>Destination</th><th>Sent</th><th>Received</th><th>Outcome</th></tr></thead>
        <tbody>
          {#each log as e (e.id)}
            <tr><td>{new Date(e.ts).toLocaleString('en-GB')}</td><td>{e.purpose}</td><td>{e.task ?? '—'}</td><td>{e.destination}</td>
              <td>{fmt(e.bytes_out)}</td><td>{fmt(e.bytes_in)}</td><td title={e.note ?? ''}>{e.outcome}</td></tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</section>
```

`web/src/pages/settings/AI.svelte` and `web/src/pages/Usage.svelte`: implement the behaviours listed in Interfaces, following the Household/Agents page patterns (load in `onMount`, `Notice` for errors, `api()` for calls, optimistic version tracking from server responses). The AI page's notice modal must show exactly: "**<name> will see the statement text Tuppence sends to it.** It's covered by <name>'s own privacy terms. Every call is listed in Settings › Privacy." Usage page heading "Usage" and summary line `£{total_gbp.toFixed(2)} of £{cap_gbp.toFixed(2)} this month · {calls} calls`.

Add shared table styles to `app.css`: `table { width: 100%; border-collapse: collapse; } th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--line); font-size: .92rem; }`, `.toggle { margin: .5rem 0 1rem; }`.

- [ ] **Step 4: Tests, check, build, commit**

```bash
npm --prefix web test && npm --prefix web run check && npm --prefix web run build
git add -A && git commit -m "Add Settings › AI, Settings › Privacy and Usage pages"
```

---

### Task 11: Fake providers, end-to-end privacy flows, live checks and M1 verification

**Files:**
- Create: `tests/fakes/fake_llm.py`, `tests/integration/__init__.py`, `tests/integration/test_providers_over_http.py`, `tests/live/__init__.py`, `tests/live/test_live_providers.py`, `web/e2e/04-ai-privacy.spec.ts`, `scripts/live_llamacpp.sh`
- Modify: `scripts/e2e.sh` (start the fake LLM server too and export `FAKE_LLM_URL`), `CONTRIBUTING.md` (live tests), `README.md` (supported providers list)

**Interfaces:**
- `tests/fakes/fake_llm.py`: a FastAPI app `create_fake_app()` implementing, over real HTTP:
  - OpenAI style: `GET /v1/models` → `fake-small`, `fake-large`; `POST /v1/chat/completions` → reply `"Echo: <last user message>"`, or, when `response_format` is present, `{"category": "test", "confidence": 1}` as text
  - Anthropic style: `GET /v1/models` with header `x-api-key` → `claude-fake` (with `max_input_tokens`, `capabilities`); `POST /v1/messages` → text block `"Echo: …"`
  - Gemini style: `GET /v1beta/models` → `models/gemini-fake`; `POST /v1beta/models/{m}:generateContent` → candidate text `"Echo: …"`
  - `GET /_last` → the last chat request body received (any style); `POST /_reset`
  - Runnable: `python tests/fakes/fake_llm.py --port N` (uvicorn)
- `tests/integration/test_providers_over_http.py`: starts the fake app on a free loopback port with `ServerThread` (from `tuppence.desktop.launcher`) and, for each of `custom` (OpenAI style), `anthropic` (with base URL override), `gemini` (base URL override) and `ollama` (base URL override), creates a connection, runs `test` (models listed), sets it as the simple model, acknowledges the notice where needed, and runs `llm.chat` + `llm.structured` end to end through the real guarded HTTP client. This is the M1 exit criterion "connection test passes against Ollama, OpenAI-compatible, Anthropic and Gemini" at the wire level.
- `tests/live/test_live_providers.py` (marker `live`, skipped unless env vars are set): `TUPPENCE_LIVE_OPENAI_BASE` + `TUPPENCE_LIVE_OPENAI_KEY` + `TUPPENCE_LIVE_OPENAI_MODEL`; `ANTHROPIC_API_KEY` (+ optional `TUPPENCE_LIVE_ANTHROPIC_MODEL`, default `claude-haiku-4-5`); `GEMINI_API_KEY` + `TUPPENCE_LIVE_GEMINI_MODEL`; `TUPPENCE_LIVE_LOCAL_BASE` + `TUPPENCE_LIVE_LOCAL_MODEL`. Each test: list models, a one-line chat, and a `structured` call returning a small pydantic model.
- `scripts/live_llamacpp.sh`: downloads `qwen2.5-0.5b-instruct-q4_k_m.gguf` (~490 MB) into `${TUPPENCE_MODELS_DIR:-$HOME/.cache/tuppence-models}` if missing, runs `ghcr.io/ggml-org/llama.cpp:server` on `127.0.0.1:18081` with `-c 4096`, waits for `/v1/models`, runs `TUPPENCE_LIVE_LOCAL_BASE=http://127.0.0.1:18081/v1 TUPPENCE_LIVE_LOCAL_MODEL=<listed id> uv run pytest -m live tests/live -k local`, then stops the container.

- [ ] **Step 1: Write the fake server and integration tests**, run `uv run pytest tests/integration -q` → pass.

- [ ] **Step 2: End-to-end privacy flow** — `web/e2e/04-ai-privacy.spec.ts` (runs after the M1a specs, signed in as the same admin):
  1. Settings › Household: add person "Alex Example" (adult) if not present.
  2. Settings › AI: Provider "OpenAI", Base URL = `${FAKE_LLM_URL}/v1`, API key `sk-e2e`, "Save connection" → card shows "Internet" badge and "Review what's sent".
  3. "Test" → "2 models found".
  4. Choose "Model for everything" = the OpenAI connection's `fake-small`.
  5. Try it with "Hello from Alex Example" → alert says "Confirm what" (notice not yet acknowledged).
  6. "Review what's sent" → modal text visible → "I understand".
  7. Try again → reply "Echo: Hello from Alex Example".
  8. Settings › Privacy: log shows a `sent` row to `127.0.0.1`.
  9. Toggle "Pseudonymise before cloud AI" on; Settings › AI → try "Hello from Alex Example" → reply still reads "Echo: Hello from Alex Example" (restored locally) **and** `GET ${FAKE_LLM_URL}/_last` (via `page.request`) contains "Adult A" and not "Alex Example".
  10. Toggle "Local only" on; try again → alert contains "Local only is on"; Privacy log first row outcome `blocked`; `/_last` unchanged since step 9.
  11. Toggle Local only and Pseudonymise off again (leave the database tidy for re-runs).

  `scripts/e2e.sh` additions (before Playwright):

```bash
LLM_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
uv run python tests/fakes/fake_llm.py --port "$LLM_PORT" >"$DATA/fake_llm.log" 2>&1 &
LLM_PID=$!
trap 'kill $PID $LLM_PID 2>/dev/null || true; rm -rf "$DATA"' EXIT
export FAKE_LLM_URL="http://127.0.0.1:$LLM_PORT"
```

  Run `bash scripts/e2e.sh` → all specs pass.

- [ ] **Step 3: Live checks (best effort, not CI)**
  - Run `bash scripts/live_llamacpp.sh` → the local live test passes against a real llama.cpp server running Qwen2.5 0.5B. If Docker or the download isn't available, record that in the report — don't fake it.
  - If an OpenAI-compatible endpoint and key are available in the environment, run the live OpenAI test the same way.

- [ ] **Step 4: Docs** — README "Bring any AI" list matches `PRESETS`; CONTRIBUTING documents `uv run pytest -m live` with the env vars and `scripts/live_llamacpp.sh`.

- [ ] **Step 5: Full M1 verification**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest -q
uv run pytest tests/integration -q
npm --prefix web test && npm --prefix web run check
bash scripts/e2e.sh
uv run pytest -m slow -q
uv run python scripts/denylist_guard.py --require
```

Every command must pass. Commit:

```bash
git add -A && git commit -m "Add fake providers, wire-level provider tests, privacy end-to-end flow and live checks"
```
