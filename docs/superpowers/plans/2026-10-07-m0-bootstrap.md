# Tuppence M0 — Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A public-ready repo whose app (FastAPI + prebuilt Svelte UI) starts and passes a health check in all three shells — Docker, desktop (pywebview + PyInstaller), and `uvx`/wheel — with CI and a personal-data guard.

**Architecture:** One Python package `tuppence` (src layout) exposes `create_app(RuntimeSettings) -> FastAPI`. A CLI (`tuppence serve|desktop|version`) runs it in `local`, `desktop` or `server` mode. The Svelte UI builds into `src/tuppence/web_dist/`, which the app serves with an SPA fallback and which ships inside the wheel, Docker image and desktop bundle.

**Tech Stack:** Python 3.12 (uv-managed), FastAPI 0.142, uvicorn 0.54, httpx 0.28, pydantic 2.13, platformdirs 4.12, pywebview 6.2 (optional `desktop` extra), PyInstaller 6.22 (`build` group), pytest 9, ruff, pyright; Svelte 5.57 + Vite 8 + TypeScript, vitest 5 + @testing-library/svelte; Docker (python:3.12-slim, node:22-alpine); GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-07-tuppence-design.md` (§2, §3.1, §3.2, §14.2, §14.3, §16 M0)

## Global Constraints

- Licence: AGPL-3.0-only. Package name `tuppence`; product name "Tuppence".
- Privacy claim, verbatim in README: "With a local model, nothing leaves your machine. With a cloud model, your statement text goes to the provider you chose, and every call is logged so you can see exactly what was sent."
- Positioning line, verbatim: "Tuppence: a private AI money coach for UK households. Your statements never leave your machine."
- No personal data in the repo: all fixtures synthetic; denylist read only from `TUPPENCE_DENYLIST` (env/CI secret) or `TUPPENCE_DENYLIST_FILE` / `git config tuppence.denylistFile` — never stored in the repo, and the guard must never print a matched term.
- Python `>=3.12`; pin dev interpreter 3.12 via `.python-version`.
- Modes and binds: `local` (uvx) → `127.0.0.1:8040`; `desktop` → `127.0.0.1:<random free port>`; `server` (Docker) → `0.0.0.0:8040`.
- All data paths come from `TUPPENCE_DATA_DIR` or the OS app-data dir (`platformdirs.user_data_dir("Tuppence", appauthor=False)`), never the working directory.
- `/health` is unauthenticated liveness; everything else app-specific lives under `/api/`.
- Security headers on every response; `Cache-Control: no-store` on `/api/*` and `/health`.
- UK English in user-facing copy.
- Commits end with:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NJjEQDR3UGuFJz6TzhmiFM
  ```

## Review Focus

1. **Data folder path with spaces/non-ASCII** (e.g. `C:\Users\Zoë Smith\AppData\…`) — app must start and `/health` must answer. Test in Task 2.
2. **Port 8040 already in use** — `tuppence serve` must exit with code 2 and a one-line hint (`--port`), not a traceback. Test in Task 2.
3. **UI not built** (`web_dist/` missing) — `/` must return a helpful 200 HTML page and `/health` + `/api/*` must keep working; never a 500. Test in Task 3.
4. **No desktop GUI backend** (pywebview missing or no GTK/WebView2) — launcher must fall back to opening the system browser and keep serving. Test in Task 5.
5. **Data folder not writable** — startup must fail with a clear message naming the folder and `TUPPENCE_DATA_DIR`, not a traceback. Test in Task 2.

---

## File Structure

```
pyproject.toml, uv.lock, .python-version, .gitignore, .gitattributes
LICENSE, README.md, CONTRIBUTING.md, SECURITY.md, PRIVACY.md
.githooks/pre-commit
scripts/denylist_guard.py          personal-data guard (no terms in repo)
scripts/smoke_http.py              poll a URL's /health until ok (used by all smoke scripts)
scripts/smoke_docker.sh            build image, run, health-check, clean up
scripts/smoke_wheel.sh             build wheel, install in temp venv, run, health-check
scripts/build_desktop.py           PyInstaller build + smoke
src/tuppence/__init__.py           __version__
src/tuppence/__main__.py           python -m tuppence
src/tuppence/cli.py                argparse: serve | desktop | version
src/tuppence/paths.py              resolve_data_dir, DataPaths, DataDirError
src/tuppence/settings.py           RuntimeSettings (mode, host, port, data_dir, web_dir, launch_token, secure_cookies)
src/tuppence/app/__init__.py       re-exports create_app
src/tuppence/app/factory.py        create_app()
src/tuppence/app/security.py       SecurityHeadersMiddleware
src/tuppence/app/static.py         mount_web(): StaticFiles + SPA fallback + "UI not built" page
src/tuppence/desktop/__init__.py
src/tuppence/desktop/launcher.py   free port, launch token, ServerThread, run_desktop()
desktop/entry.py                   PyInstaller entry point
desktop/tuppence.spec              PyInstaller spec (onedir)
web/                               Svelte 5 + Vite + TS app (package.json, vite.config.ts, src/…)
Dockerfile, .dockerignore, compose.yaml
.github/workflows/ci.yml, .github/workflows/release.yml
tests/conftest.py, tests/test_denylist_guard.py, tests/test_paths.py, tests/test_cli.py,
tests/app/test_health.py, tests/app/test_security_headers.py, tests/app/test_static.py,
tests/desktop/test_launcher.py, tests/smoke/test_packaging.py
```

---

### Task 1: Repository scaffold, licence, docs and personal-data guard

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.gitattributes`, `LICENSE`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `PRIVACY.md`, `src/tuppence/__init__.py`, `scripts/denylist_guard.py`, `.githooks/pre-commit`, `tests/conftest.py`
- Test: `tests/test_denylist_guard.py`

**Interfaces:**
- Produces: `tuppence.__version__: str` (= `"0.1.0.dev0"`); `scripts/denylist_guard.py` with `load_terms(env: Mapping[str,str], git_config_file: str | None) -> list[str]`, `compile_terms(terms: list[str]) -> list[re.Pattern[str]]`, `scan_text(text: str, patterns) -> list[tuple[int, int]]` (line number, term index), `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Initialise the uv project**

Working directory: the repository root (already a git repo with the spec committed).

```bash
cd .
uv python install 3.12
echo "3.12" > .python-version
```

Write `pyproject.toml`:

```toml
[project]
name = "tuppence"
version = "0.1.0.dev0"
description = "A private AI money coach for UK households. Your statements never leave your machine."
readme = "README.md"
license = "AGPL-3.0-only"
license-files = ["LICENSE"]
requires-python = ">=3.12"
authors = [{ name = "Tuppence contributors" }]
keywords = ["personal-finance", "uk", "budgeting", "local-first", "llm", "self-hosted", "privacy"]
classifiers = [
  "Development Status :: 2 - Pre-Alpha",
  "Environment :: Web Environment",
  "Intended Audience :: End Users/Desktop",
  "Programming Language :: Python :: 3.12",
  "Topic :: Office/Business :: Financial",
]
dependencies = [
  "fastapi>=0.142",
  "uvicorn>=0.54",
  "httpx>=0.28",
  "pydantic>=2.13",
  "platformdirs>=4.12",
]

[project.optional-dependencies]
desktop = ["pywebview>=6.2"]

[project.scripts]
tuppence = "tuppence.cli:run"

[dependency-groups]
dev = ["pytest>=9.1", "ruff>=0.16", "pyright>=1.1.414"]
build = ["pyinstaller>=6.22"]

[build-system]
requires = ["hatchling>=1.32"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/tuppence"]
artifacts = ["src/tuppence/web_dist/**"]

[tool.hatch.build.targets.sdist]
exclude = ["web/node_modules", "src/tuppence/web_dist"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
  "slow: builds images/bundles or launches external processes",
  "live: needs a real LLM endpoint (opt-in, never in CI)",
]
addopts = "-m 'not live and not slow'"

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests", "scripts"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "SIM"]
ignore = ["S101"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S"]
"scripts/**" = ["S603", "S607"]

[tool.pyright]
include = ["src"]
pythonVersion = "3.12"
typeCheckingMode = "standard"
```

Run: `uv sync` → Expected: creates `.venv` and `uv.lock`, exit 0.

- [ ] **Step 2: Licence and docs**

```bash
gh api /licenses/agpl-3.0 --jq .body > LICENSE
head -3 LICENSE   # Expected: "GNU AFFERO GENERAL PUBLIC LICENSE" / "Version 3, 19 November 2007"
```

`README.md` (exact content):

````markdown
# Tuppence

**Tuppence: a private AI money coach for UK households. Your statements never leave your machine.**

Upload your bank and card statements. Tuppence works out what every payment is
and *why* you make it, asks you smart questions, remembers the answers, and
gives UK-specific guidance — from bills creeping up to money you may be owed.

> **Privacy, plainly:** With a local model, nothing leaves your machine. With a
> cloud model, your statement text goes to the provider you chose, and every
> call is logged so you can see exactly what was sent.

> **Status: pre-alpha.** Tuppence is under active development and not ready
> for real use yet. Watch the repo for the developer preview.

## Run it

| How | For | Command |
|---|---|---|
| Docker | home servers, NAS, self-hosters | `docker compose up -d` then open `http://<server>:8040` |
| Desktop app | Windows, macOS, Linux | download from Releases (coming with the developer preview) |
| uvx | technical users | `uvx tuppence` |

Never expose Tuppence directly to the internet. For remote access use a VPN
such as Tailscale.

## What it is (and isn't)

- **Guidance, not regulated financial advice.** Tuppence explains your money
  and points you to official tools and free advice services (MoneyHelper,
  StepChange, Citizens Advice). It never recommends specific investment products.
- **Bring any AI:** local (Ollama, LM Studio, llama.cpp, vLLM, Jan) or cloud
  (Anthropic, OpenAI, Gemini, OpenRouter, Mistral, Groq, DeepSeek, Qwen, Kimi,
  GLM…) with your own key.
- **Works without AI** for importing, rules, budgets and UK checks; AI adds the
  understanding.

## Develop

```bash
uv sync                      # Python deps
npm --prefix web ci          # UI deps
npm --prefix web run build   # builds the UI into src/tuppence/web_dist
uv run tuppence serve        # http://127.0.0.1:8040
uv run pytest                # fast tests
npm --prefix web test        # UI tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).
How your data is handled: [PRIVACY.md](PRIVACY.md).

## Licence

[AGPL-3.0](LICENSE). If you run a modified Tuppence as a service for others,
you must share your changes.
````

`CONTRIBUTING.md`: sections "Setup" (the Develop commands), "Tests" (`uv run pytest`, `uv run pytest -m slow` for packaging smoke tests, `npm --prefix web test`), "Personal-data guard" (explain: `git config core.hooksPath .githooks`; set `TUPPENCE_DENYLIST_FILE` or `git config tuppence.denylistFile <path>` to a private file of terms you never want committed; CI runs the guard with a secret list; the guard never prints matched terms), "Fixtures must be synthetic" (never commit real statements; use invented names like "Alex Example" and companies like "Acme Payroll"), "Style" (ruff, pyright, UK English in UI copy), "Commits" (small, imperative subject lines).

`SECURITY.md`: "Report vulnerabilities privately via GitHub Security Advisories (Security → Report a vulnerability). Please don't open public issues for security problems. Scope: anything that could expose a user's financial data, credentials or API keys, or let someone on the network read or change data." Plus a "Threat model" paragraph: desktop binds loopback only; Docker is for trusted LAN behind a VPN, never port-forwarded.

`PRIVACY.md`: what's stored (SQLite + uploaded files in the data folder), what leaves the machine (only: calls to the LLM provider you configure; optional research lookups — merchant names only; optional anonymous data-pack and market-data downloads; nothing else, no telemetry), the "Local only" switch, the privacy log, the pseudonymise toggle (default off), how to delete everything (delete the data folder).

`.gitignore`:

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
dist/
build/
*.egg-info/
web/node_modules/
web/dist/
src/tuppence/web_dist/
.env
*.db
*.db-wal
*.db-shm
/data/
.DS_Store
```

`.gitattributes`: `* text=auto eol=lf` and `*.bat text eol=crlf`.

`src/tuppence/__init__.py`:

```python
"""Tuppence: a private AI money coach for UK households."""

__version__ = "0.1.0.dev0"
```

- [ ] **Step 3: Write the failing guard tests**

`tests/conftest.py`:

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
```

`tests/test_denylist_guard.py`:

```python
import subprocess

import denylist_guard as guard


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_matches_whole_words_case_insensitively():
    patterns = guard.compile_terms(["Alex Example"])
    assert guard.scan_text("Paid ALEX EXAMPLE £5", patterns) == [(1, 0)]


def test_ignores_substrings_inside_words():
    patterns = guard.compile_terms(["r exa", "mple"])
    assert guard.scan_text("for example\nsimple plan", patterns) == []


def test_load_terms_merges_env_file_and_skips_comments(tmp_path):
    f = tmp_path / "deny.txt"
    f.write_text("# comment\nsecretword\n\n", encoding="utf-8")
    terms = guard.load_terms({"TUPPENCE_DENYLIST": "alpha\nbeta", "TUPPENCE_DENYLIST_FILE": str(f)}, None)
    assert terms == ["alpha", "beta", "secretword"]


def test_main_reports_term_index_not_term(tmp_path, capsys, monkeypatch):
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("hello\nwe paid Secretword today\n", encoding="utf-8")
    monkeypatch.setenv("TUPPENCE_DENYLIST", "secretword")
    monkeypatch.delenv("TUPPENCE_DENYLIST_FILE", raising=False)
    rc = guard.main(["--root", str(repo)])
    out = capsys.readouterr()
    assert rc == 1
    assert "notes.md:2" in out.out + out.err
    assert "term #1" in out.out + out.err
    assert "secretword" not in (out.out + out.err).lower()


def test_main_checks_file_paths_too(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    (repo / "secretword-notes.md").write_text("clean\n", encoding="utf-8")
    monkeypatch.setenv("TUPPENCE_DENYLIST", "secretword")
    assert guard.main(["--root", str(repo)]) == 1


def test_no_terms_passes_unless_required(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    monkeypatch.delenv("TUPPENCE_DENYLIST", raising=False)
    monkeypatch.delenv("TUPPENCE_DENYLIST_FILE", raising=False)
    assert guard.main(["--root", str(repo), "--no-git-config"]) == 0
    assert guard.main(["--root", str(repo), "--no-git-config", "--require"]) == 2


def test_clean_repo_passes(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    (repo / "a.txt").write_text("nothing to see\n", encoding="utf-8")
    monkeypatch.setenv("TUPPENCE_DENYLIST", "secretword")
    assert guard.main(["--root", str(repo)]) == 0
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_denylist_guard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'denylist_guard'`.

- [ ] **Step 5: Implement the guard**

`scripts/denylist_guard.py`:

```python
"""Fail if any tracked or untracked-but-not-ignored file mentions a denylisted term.

The denylist is never stored in this repository. Supply it via:
  TUPPENCE_DENYLIST        newline-separated terms (e.g. a CI secret)
  TUPPENCE_DENYLIST_FILE   path to a file with one term per line
  git config tuppence.denylistFile <path>   (local, per clone)
Blank lines and lines starting with '#' are ignored. Matching is
case-insensitive on word boundaries. Matched terms are never printed —
only their 1-based index — so CI logs can't leak the list.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path


def _git_config_file(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "config", "--get", "tuppence.denylistFile"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value or None


def load_terms(env: Mapping[str, str], git_config_file: str | None) -> list[str]:
    raw = env.get("TUPPENCE_DENYLIST", "")
    for candidate in (env.get("TUPPENCE_DENYLIST_FILE"), git_config_file):
        if candidate and Path(candidate).is_file():
            raw += "\n" + Path(candidate).read_text(encoding="utf-8")
    terms: list[str] = []
    for line in raw.splitlines():
        term = line.strip()
        if term and not term.startswith("#") and term not in terms:
            terms.append(term)
    return terms


def compile_terms(terms: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)", re.IGNORECASE) for t in terms]


def scan_text(text: str, patterns: list[re.Pattern[str]]) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for index, pattern in enumerate(patterns):
            if pattern.search(line):
                hits.append((lineno, index))
    return hits


def candidate_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    return sorted({p for p in out.decode("utf-8").split("\0") if p})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root to scan")
    parser.add_argument("--require", action="store_true", help="fail if no terms are configured")
    parser.add_argument("--no-git-config", action="store_true", help="ignore git config tuppence.denylistFile")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    git_file = None if args.no_git_config else _git_config_file(root)
    terms = load_terms(os.environ, git_file)
    if not terms:
        print("denylist: no terms configured", file=sys.stderr)
        return 2 if args.require else 0

    patterns = compile_terms(terms)
    failures = 0
    for rel in candidate_files(root):
        for _, index in scan_text(rel, patterns):
            print(f"{rel}: path matches denylisted term #{index + 1}")
            failures += 1
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for lineno, index in scan_text(text, patterns):
            print(f"{rel}:{lineno}: matches denylisted term #{index + 1}")
            failures += 1
    if failures:
        print(f"denylist: {failures} match(es) — remove personal data before committing", file=sys.stderr)
        return 1
    print(f"denylist: clean ({len(terms)} terms checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`.githooks/pre-commit` (mark executable with `chmod +x`):

```sh
#!/bin/sh
# Personal-data guard. Configure your private list with:
#   git config tuppence.denylistFile /path/outside/repo/denylist.txt
exec uv run --quiet python scripts/denylist_guard.py
```

Then enable it for this clone and point at the private list (outside the repo):

```bash
git config core.hooksPath .githooks
git config tuppence.denylistFile /path/outside/the/repo/denylist.txt
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_denylist_guard.py -q` → Expected: 7 passed.
Run: `uv run python scripts/denylist_guard.py` → Expected: `denylist: clean (N terms checked)`, exit 0.
Run: `uv run ruff check . && uv run ruff format --check .` → Expected: no errors (run `uv run ruff format .` first if needed).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Scaffold uv project, AGPL licence, docs and personal-data guard" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJjEQDR3UGuFJz6TzhmiFM"
```

---

### Task 2: Server skeleton, data paths and CLI

**Files:**
- Create: `src/tuppence/paths.py`, `src/tuppence/settings.py`, `src/tuppence/app/__init__.py`, `src/tuppence/app/factory.py`, `src/tuppence/app/security.py`, `src/tuppence/app/static.py` (stub in this task, completed in Task 3), `src/tuppence/cli.py`, `src/tuppence/__main__.py`
- Test: `tests/test_paths.py`, `tests/test_cli.py`, `tests/app/__init__.py`, `tests/app/test_health.py`, `tests/app/test_security_headers.py`

**Interfaces:**
- Consumes: `tuppence.__version__`.
- Produces:
  - `tuppence.paths.resolve_data_dir(override: str | os.PathLike[str] | None = None) -> Path` (raises `DataDirError`)
  - `tuppence.paths.DataPaths(root: Path)` with properties `db`, `checkpoints_db`, `files`, `backups`, `config` and method `ensure() -> DataPaths`
  - `tuppence.settings.Mode = Literal["local", "desktop", "server"]`
  - `tuppence.settings.RuntimeSettings` (pydantic): `mode: Mode`, `host: str`, `port: int`, `data_dir: Path`, `web_dir: Path | None = None`, `launch_token: str | None = None`, `secure_cookies: bool = False`; classmethod `for_mode(mode, *, data_dir, host=None, port=None, web_dir=None, launch_token=None) -> RuntimeSettings` applying defaults (server→`0.0.0.0:8040`, local→`127.0.0.1:8040`, desktop→`127.0.0.1:0`)
  - `tuppence.app.create_app(settings: RuntimeSettings) -> FastAPI`; `app.state.settings`, `app.state.paths`
  - `GET /health` → `{"status": "ok", "version": str, "mode": str}`
  - `tuppence.cli.main(argv: list[str] | None = None) -> int`; `tuppence.cli.run() -> NoReturn`
  - `tuppence.cli.port_available(host: str, port: int) -> bool`
  - `tuppence.app.static.mount_web(app: FastAPI, web_dir: Path | None) -> None` (Task 3 fills in)

- [ ] **Step 1: Write the failing tests**

`tests/test_paths.py`:

```python
import os
import sys

import pytest

from tuppence.paths import DataDirError, DataPaths, resolve_data_dir


def test_override_wins_and_is_created(tmp_path):
    target = tmp_path / "Zoë Smith" / "App Data"
    path = resolve_data_dir(target)
    assert path == target.resolve()
    assert path.is_dir()


def test_env_var_used_when_no_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TUPPENCE_DATA_DIR", str(tmp_path / "envdir"))
    assert resolve_data_dir() == (tmp_path / "envdir").resolve()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX non-root only")
def test_unwritable_dir_raises_clear_error(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(DataDirError) as exc:
            resolve_data_dir(locked / "inner")
        message = str(exc.value)
        assert "TUPPENCE_DATA_DIR" in message
        assert "locked" in message
    finally:
        locked.chmod(0o700)


def test_data_paths_layout(tmp_path):
    paths = DataPaths(tmp_path).ensure()
    assert paths.db == tmp_path / "tuppence.db"
    assert paths.checkpoints_db == tmp_path / "checkpoints.db"
    for d in (paths.files, paths.backups, paths.config):
        assert d.is_dir()
```

`tests/app/__init__.py`: empty.

`tests/app/test_health.py`:

```python
from fastapi.testclient import TestClient

from tuppence import __version__
from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


def make_client(tmp_path, mode="local"):
    settings = RuntimeSettings.for_mode(mode, data_dir=tmp_path, web_dir=tmp_path / "no-ui")
    return TestClient(create_app(settings))


def test_health_reports_version_and_mode(tmp_path):
    client = make_client(tmp_path, mode="server")
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__, "mode": "server"}


def test_health_works_with_unicode_data_dir(tmp_path):
    client = make_client(tmp_path / "Zoë Smith" / "App Data")
    assert client.get("/health").status_code == 200


def test_unknown_api_route_is_json_404(tmp_path):
    r = make_client(tmp_path).get("/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_mode_defaults():
    from pathlib import Path

    assert RuntimeSettings.for_mode("server", data_dir=Path("/x")).host == "0.0.0.0"
    assert RuntimeSettings.for_mode("server", data_dir=Path("/x")).port == 8040
    assert RuntimeSettings.for_mode("local", data_dir=Path("/x")).host == "127.0.0.1"
    assert RuntimeSettings.for_mode("desktop", data_dir=Path("/x")).port == 0
```

`tests/app/test_security_headers.py`:

```python
from fastapi.testclient import TestClient

from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


def test_security_headers_on_every_response(tmp_path):
    client = TestClient(create_app(RuntimeSettings.for_mode("local", data_dir=tmp_path)))
    for path in ("/health", "/", "/api/nope"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert "default-src 'self'" in h["content-security-policy"]


def test_no_store_on_api_and_health(tmp_path):
    client = TestClient(create_app(RuntimeSettings.for_mode("local", data_dir=tmp_path)))
    assert client.get("/health").headers["cache-control"] == "no-store"
    assert client.get("/api/nope").headers["cache-control"] == "no-store"
```

`tests/test_cli.py`:

```python
import socket

from tuppence import __version__
from tuppence.cli import main, port_available


def test_version_prints(capsys):
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_port_in_use_exits_2_with_hint(tmp_path, capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert not port_available("127.0.0.1", port)
        rc = main(["serve", "--port", str(port), "--no-browser", "--data-dir", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert f"Port {port} is already in use" in err
    assert "--port" in err


def test_unwritable_data_dir_exits_2(tmp_path, capsys, monkeypatch):
    from tuppence import cli
    from tuppence.paths import DataDirError

    def boom(_override=None):
        raise DataDirError("Tuppence can't write to its data folder /nope. Set TUPPENCE_DATA_DIR to a writable folder.")

    monkeypatch.setattr(cli, "resolve_data_dir", boom)
    rc = main(["serve", "--no-browser", "--data-dir", "/nope"])
    assert rc == 2
    assert "TUPPENCE_DATA_DIR" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_paths.py tests/test_cli.py tests/app -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tuppence.paths'` (and similar).

- [ ] **Step 3: Implement**

`src/tuppence/paths.py`:

```python
"""Where Tuppence keeps its data. Never the working directory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Tuppence"


class DataDirError(RuntimeError):
    """The data folder can't be created or written."""


def resolve_data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    raw = override or os.environ.get("TUPPENCE_DATA_DIR") or user_data_dir(APP_NAME, appauthor=False)
    path = Path(raw).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise DataDirError(
            f"Tuppence can't write to its data folder {path}: {reason}. "
            "Set TUPPENCE_DATA_DIR to a writable folder."
        ) from exc
    return path


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "tuppence.db"

    @property
    def checkpoints_db(self) -> Path:
        return self.root / "checkpoints.db"

    @property
    def files(self) -> Path:
        return self.root / "files"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def config(self) -> Path:
        return self.root / "config"

    def ensure(self) -> DataPaths:
        for directory in (self.root, self.files, self.backups, self.config):
            directory.mkdir(parents=True, exist_ok=True)
        return self
```

`src/tuppence/settings.py`:

```python
"""Runtime settings for one process: how and where it runs."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Mode = Literal["local", "desktop", "server"]

_DEFAULT_HOST: dict[str, str] = {"local": "127.0.0.1", "desktop": "127.0.0.1", "server": "0.0.0.0"}  # noqa: S104
_DEFAULT_PORT: dict[str, int] = {"local": 8040, "desktop": 0, "server": 8040}


class RuntimeSettings(BaseModel):
    mode: Mode
    host: str
    port: int
    data_dir: Path
    web_dir: Path | None = None
    launch_token: str | None = None
    secure_cookies: bool = False

    @classmethod
    def for_mode(
        cls,
        mode: Mode,
        *,
        data_dir: Path,
        host: str | None = None,
        port: int | None = None,
        web_dir: Path | None = None,
        launch_token: str | None = None,
        secure_cookies: bool = False,
    ) -> RuntimeSettings:
        return cls(
            mode=mode,
            host=host or _DEFAULT_HOST[mode],
            port=_DEFAULT_PORT[mode] if port is None else port,
            data_dir=data_dir,
            web_dir=web_dir,
            launch_token=launch_token,
            secure_cookies=secure_cookies,
        )
```

`src/tuppence/app/security.py`:

```python
"""Security headers on every response (spec §14.2)."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)

_STATIC_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        no_store = path == "/health" or path.startswith("/api/")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if not (no_store and k.lower() == b"cache-control")
                ]
                headers.extend(_STATIC_HEADERS)
                if no_store:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

`src/tuppence/app/static.py` (stub; Task 3 replaces it):

```python
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def mount_web(app: FastAPI, web_dir: Path | None) -> None:
    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse("<!doctype html><title>Tuppence</title><p>Tuppence is running.</p>")
```

`src/tuppence/app/factory.py`:

```python
"""Application factory: one FastAPI app for every shell."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from tuppence import __version__
from tuppence.app.security import SecurityHeadersMiddleware
from tuppence.app.static import mount_web
from tuppence.paths import DataPaths
from tuppence.settings import RuntimeSettings


def create_app(settings: RuntimeSettings) -> FastAPI:
    app = FastAPI(
        title="Tuppence",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.paths = DataPaths(settings.data_dir).ensure()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "mode": settings.mode}

    api = APIRouter(prefix="/api")

    @api.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(path: str) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    app.state.api_fallback = api
    mount_web(app, settings.web_dir)
    app.include_router(api)
    return app
```

> Note for later tasks: real API routers must be included **before** `app.include_router(api)` (the catch-all). Later milestones add `include_routers(app)` immediately before that line.

`src/tuppence/app/__init__.py`:

```python
from tuppence.app.factory import create_app

__all__ = ["create_app"]
```

`src/tuppence/cli.py`:

```python
"""Command line: tuppence serve | desktop | version."""

from __future__ import annotations

import argparse
import socket
import sys
import webbrowser
from typing import NoReturn

from tuppence import __version__
from tuppence.paths import DataDirError, resolve_data_dir
from tuppence.settings import RuntimeSettings


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from tuppence.app import create_app

    try:
        data_dir = resolve_data_dir(args.data_dir)
    except DataDirError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings = RuntimeSettings.for_mode(args.mode, data_dir=data_dir, host=args.host, port=args.port)
    if not port_available(settings.host, settings.port):
        print(
            f"Port {settings.port} is already in use. Try another with --port, e.g. --port {settings.port + 1}.",
            file=sys.stderr,
        )
        return 2
    url = f"http://{'127.0.0.1' if settings.host in ('0.0.0.0', '::') else settings.host}:{settings.port}/"  # noqa: S104
    print(f"Tuppence {__version__} running at {url}  (data: {data_dir})")
    if settings.mode == "local" and not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")
    return 0


def _desktop(args: argparse.Namespace) -> int:
    from tuppence.desktop.launcher import run_desktop

    return run_desktop(data_dir=args.data_dir, smoke=args.smoke, smoke_out=args.smoke_out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tuppence", description="Tuppence: a private AI money coach.")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--mode", choices=["local", "server"], default="local")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--data-dir", default=None)
    serve.add_argument("--no-browser", action="store_true")
    serve.set_defaults(func=_serve)

    desktop = sub.add_parser("desktop", help="run the desktop app window")
    desktop.add_argument("--data-dir", default=None)
    desktop.add_argument("--smoke", action="store_true", help="start, health-check, exit (no window)")
    desktop.add_argument("--smoke-out", default=None, help="write smoke result JSON to this file")
    desktop.set_defaults(func=_desktop)

    version = sub.add_parser("version", help="print the version")
    version.set_defaults(func=lambda _a: print(__version__) or 0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve", *(argv or [])])
    return int(args.func(args))


def run() -> NoReturn:
    sys.exit(main())
```

`src/tuppence/__main__.py`:

```python
from tuppence.cli import run

run()
```

Also create `src/tuppence/desktop/__init__.py` (empty) and `src/tuppence/desktop/launcher.py` with a placeholder `run_desktop` that raises `NotImplementedError("desktop shell lands in Task 5")` so `cli` imports resolve — Task 5 replaces it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q` → Expected: all tests pass (guard tests + new ones).
Run: `uv run pyright` → Expected: 0 errors.
Run: `uv run ruff check . && uv run ruff format --check .` → Expected: clean.
Manual: `uv run tuppence serve --no-browser --port 18040 --data-dir /tmp/tp-m0 &` then `curl -s localhost:18040/health` → `{"status":"ok",...}`; kill the server.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add app factory, /health, security headers, data paths and CLI" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJjEQDR3UGuFJz6TzhmiFM"
```

---

### Task 3: Svelte UI skeleton served by the app

**Files:**
- Create: `web/` (Vite svelte-ts template, adjusted), `web/src/lib/health.ts`, `web/src/App.svelte`, `web/src/App.test.ts`, `web/vitest.config.ts` (or `test` block in `vite.config.ts`)
- Modify: `src/tuppence/app/static.py` (full implementation), `src/tuppence/settings.py` (no change to fields; `web_dir` default resolution lives in static.py)
- Test: `tests/app/test_static.py`

**Interfaces:**
- Consumes: `create_app`, `RuntimeSettings.web_dir`.
- Produces: `tuppence.app.static.default_web_dir() -> Path` (= `Path(tuppence.__file__).parent / "web_dist"`); `mount_web(app, web_dir)` serving `/assets/*` via StaticFiles, other top-level files (`favicon.svg` etc.), SPA fallback to `index.html` for non-`/api`, non-`/health` GETs, and a 200 "UI not built" page when `index.html` is missing. Web build output dir: `src/tuppence/web_dist/`. Frontend `fetchHealth(): Promise<{status: string; version: string; mode: string}>` in `web/src/lib/health.ts`.

- [ ] **Step 1: Write the failing Python tests**

`tests/app/test_static.py`:

```python
from fastapi.testclient import TestClient

from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


def _fake_ui(tmp_path):
    web = tmp_path / "web_dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<!doctype html><div id="app"></div><script type="module" src="/assets/app.js"></script>', encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    (web / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    return web


def client_for(tmp_path, web_dir):
    return TestClient(create_app(RuntimeSettings.for_mode("local", data_dir=tmp_path / "data", web_dir=web_dir)))


def test_serves_index_and_assets(tmp_path):
    c = client_for(tmp_path, _fake_ui(tmp_path))
    r = c.get("/")
    assert r.status_code == 200 and 'id="app"' in r.text
    a = c.get("/assets/app.js")
    assert a.status_code == 200 and "javascript" in a.headers["content-type"]
    assert c.get("/favicon.svg").status_code == 200


def test_spa_fallback_for_client_routes(tmp_path):
    c = client_for(tmp_path, _fake_ui(tmp_path))
    r = c.get("/settings/ai")
    assert r.status_code == 200 and 'id="app"' in r.text


def test_api_paths_never_fall_back_to_spa(tmp_path):
    c = client_for(tmp_path, _fake_ui(tmp_path))
    r = c.get("/api/unknown")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_missing_asset_is_404_not_index(tmp_path):
    c = client_for(tmp_path, _fake_ui(tmp_path))
    assert c.get("/assets/missing.js").status_code == 404


def test_path_traversal_blocked(tmp_path):
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    c = client_for(tmp_path, _fake_ui(tmp_path))
    r = c.get("/assets/../../secret.txt")
    assert "nope" not in r.text


def test_ui_not_built_page_is_helpful_200(tmp_path):
    c = client_for(tmp_path, tmp_path / "does-not-exist")
    r = c.get("/")
    assert r.status_code == 200
    assert "web UI hasn't been built" in r.text
    assert c.get("/health").status_code == 200
    assert c.get("/some/route").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/app/test_static.py -q` → Expected: FAIL (assets 404, no fallback, no "not built" page).

- [ ] **Step 3: Implement `static.py`**

```python
"""Serve the prebuilt Svelte UI with an SPA fallback (spec §3.2)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import tuppence

NOT_BUILT_HTML = """<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8"><title>Tuppence</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem">
<h1>Tuppence is running</h1>
<p>The web UI hasn't been built yet. From the repository root run:</p>
<pre>npm --prefix web ci &amp;&amp; npm --prefix web run build</pre>
<p>The API is available and <a href="/health">/health</a> reports status.</p>
</body></html>"""

_RESERVED_PREFIXES = ("/api/", "/health")


def default_web_dir() -> Path:
    return Path(tuppence.__file__).parent / "web_dist"


def mount_web(app: FastAPI, web_dir: Path | None) -> None:
    root = (web_dir or default_web_dir()).resolve()
    index = root / "index.html"
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request) -> FileResponse | HTMLResponse:
        path = "/" + full_path
        if path.startswith(_RESERVED_PREFIXES) or path.startswith("/assets/"):
            raise HTTPException(status_code=404)
        if not index.is_file():
            return HTMLResponse(NOT_BUILT_HTML)
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
```

Ordering matters: in `factory.py`, the SPA catch-all route is registered by `mount_web` **after** `/health` and the API routers but **before** the `/api` catch-all `include_router(api)`. Because the SPA route explicitly 404s `/api/*`, change `factory.py` so the API fallback router is included **before** `mount_web(...)`:

```python
    # in create_app(), replace the last three lines with:
    app.include_router(api)
    mount_web(app, settings.web_dir)
    return app
```

(And remove the `app.state.api_fallback = api` line.) Later milestones insert `include_routers(app)` before `app.include_router(api)`.

- [ ] **Step 4: Run Python tests to verify they pass**

Run: `uv run pytest -q` → Expected: all pass.

- [ ] **Step 5: Scaffold the Svelte app**

```bash
cd .
npm create vite@latest web -- --template svelte-ts
cd web && npm install && npm install -D vitest @testing-library/svelte @testing-library/jest-dom jsdom
```

Replace `web/vite.config.ts`:

```ts
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: '../src/tuppence/web_dist',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8040',
      '/health': 'http://127.0.0.1:8040',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
  },
  resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
})
```

`web/src/test-setup.ts`:

```ts
import '@testing-library/jest-dom/vitest'
```

In `web/package.json` scripts set: `"dev": "vite"`, `"build": "vite build"`, `"test": "vitest run"`, `"check": "svelte-check --tsconfig ./tsconfig.app.json"` (keep the template's tsconfig names). If `tsc` complains about `test` in the Vite config, add `/// <reference types="vitest/config" />` as the first line of `vite.config.ts`.

Delete the template's demo files (`src/lib/Counter.svelte`, `src/assets/svelte.svg`, `public/vite.svg`) and their imports. Set `index.html` `<html lang="en-GB">`, `<title>Tuppence</title>`, favicon `/favicon.svg`. Create `web/public/favicon.svg` — a simple two-coin mark:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="24" cy="32" r="18" fill="#c08a2e"/><circle cx="40" cy="32" r="18" fill="#e3b04b" stroke="#7a5418" stroke-width="3"/><text x="40" y="39" font-family="Georgia,serif" font-size="20" text-anchor="middle" fill="#7a5418">2</text></svg>
```

`web/src/lib/health.ts`:

```ts
export type Health = { status: string; version: string; mode: string }

export async function fetchHealth(fetchImpl: typeof fetch = fetch): Promise<Health> {
  const res = await fetchImpl('/health', { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`)
  return (await res.json()) as Health
}
```

- [ ] **Step 6: Write the failing UI test**

`web/src/App.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.svelte'

afterEach(() => vi.unstubAllGlobals())

describe('App', () => {
  it('shows the name, tagline and backend status', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ status: 'ok', version: '0.1.0.dev0', mode: 'local' }), { status: 200 })))
    render(App)
    expect(screen.getByRole('heading', { name: 'Tuppence' })).toBeInTheDocument()
    expect(screen.getByText(/private AI money coach for UK households/)).toBeInTheDocument()
    expect(await screen.findByText(/Connected · v0\.1\.0\.dev0 · local/)).toBeInTheDocument()
  })

  it('says so when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
    render(App)
    expect(await screen.findByText(/Can't reach the Tuppence service/)).toBeInTheDocument()
  })
})
```

Run: `npm --prefix web test` → Expected: FAIL (template App doesn't render these).

- [ ] **Step 7: Implement `App.svelte` and `main.ts`**

`web/src/App.svelte`:

```svelte
<script lang="ts">
  import { onMount } from 'svelte'
  import { fetchHealth, type Health } from './lib/health'

  let health = $state<Health | null>(null)
  let error = $state(false)

  onMount(async () => {
    try {
      health = await fetchHealth()
    } catch {
      error = true
    }
  })
</script>

<main>
  <h1>Tuppence</h1>
  <p class="tagline">A private AI money coach for UK households. Your statements never leave your machine.</p>
  {#if health}
    <p class="status ok">Connected · v{health.version} · {health.mode}</p>
  {:else if error}
    <p class="status err">Can't reach the Tuppence service. Is it running?</p>
  {:else}
    <p class="status">Connecting…</p>
  {/if}
</main>

<style>
  main { font-family: system-ui, sans-serif; max-width: 44rem; margin: 4rem auto; padding: 0 1rem; }
  h1 { font-size: 2.5rem; margin: 0 0 .5rem; }
  .tagline { color: #555; }
  .status { margin-top: 2rem; font-size: .95rem; }
  .ok { color: #1b7a3a; }
  .err { color: #a02020; }
</style>
```

`web/src/main.ts`:

```ts
import { mount } from 'svelte'
import App from './App.svelte'

const app = mount(App, { target: document.getElementById('app')! })

export default app
```

Remove the template's global CSS import if it styles the demo (keep a minimal `app.css` with `:root { color-scheme: light dark; }` and `body { margin: 0; }`).

- [ ] **Step 8: Run UI tests, build, and serve the real build**

Run: `npm --prefix web test` → Expected: 2 passed.
Run: `npm --prefix web run build` → Expected: `src/tuppence/web_dist/index.html` and `src/tuppence/web_dist/assets/*.js` exist.
Run: `uv run tuppence serve --no-browser --port 18041 --data-dir /tmp/tp-m0 &` then `curl -s localhost:18041/ | grep -c 'id="app"'` → `1`; `curl -sI localhost:18041/assets/$(ls src/tuppence/web_dist/assets | grep '\.js$' | head -1) | grep -i content-type` → javascript. Kill server.
Run: `uv run pytest -q && uv run ruff check . && uv run pyright` → clean.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "Add Svelte UI skeleton and serve it with an SPA fallback" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJjEQDR3UGuFJz6TzhmiFM"
```

---

### Task 4: Docker image, compose and smoke script

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `compose.yaml`, `scripts/smoke_http.py`, `scripts/smoke_docker.sh`
- Test: `tests/smoke/__init__.py`, `tests/smoke/test_packaging.py` (Docker part; `slow` marker)

**Interfaces:**
- Consumes: CLI `tuppence serve --mode server --host 0.0.0.0 --port 8040`; `/health`.
- Produces: `scripts/smoke_http.py URL [--timeout S] [--expect-mode MODE]` → exit 0 when `URL/health` returns `status == "ok"` (and mode matches if given), else exit 1 after timeout. `scripts/smoke_docker.sh` → exit 0 on success; image tag `tuppence:smoke`.

- [ ] **Step 1: Write the failing smoke test**

`tests/smoke/__init__.py`: empty.

`tests/smoke/test_packaging.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")
def test_docker_image_serves_health():
    result = subprocess.run(["bash", "scripts/smoke_docker.sh"], cwd=ROOT, capture_output=True, text=True, timeout=1200)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "smoke: ok" in result.stdout
```

Run: `uv run pytest -m slow tests/smoke -q` → Expected: FAIL (script missing).

- [ ] **Step 2: Implement**

`scripts/smoke_http.py`:

```python
"""Poll <base>/health until it reports ok. Used by every packaging smoke test."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base", help="e.g. http://127.0.0.1:8040")
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--expect-mode", default=None)
    args = p.parse_args(argv)
    deadline = time.monotonic() + args.timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(args.base.rstrip("/") + "/health", timeout=3) as r:  # noqa: S310
                body = json.loads(r.read().decode())
            if body.get("status") == "ok" and (args.expect_mode in (None, body.get("mode"))):
                print(f"smoke: ok {body}")
                return 0
            last = f"unexpected body {body}"
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError) as exc:
            last = str(exc)
        time.sleep(1)
    print(f"smoke: FAILED after {args.timeout}s: {last}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

`.dockerignore`:

```
.git
.venv
**/__pycache__
web/node_modules
web/dist
src/tuppence/web_dist
dist
build
*.db*
docs
tests
```

`Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TUPPENCE_DATA_DIR=/data
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY --from=web /src/src/tuppence/web_dist ./src/tuppence/web_dist
RUN uv sync --frozen --no-dev --no-editable \
 && useradd --system --uid 10001 --home-dir /app tuppence \
 && mkdir -p /data && chown tuppence /data
USER tuppence
VOLUME ["/data"]
EXPOSE 8040
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8040/health', timeout=4).status == 200 else 1)"]
CMD ["/app/.venv/bin/tuppence", "serve", "--mode", "server", "--host", "0.0.0.0", "--port", "8040"]
```

`compose.yaml`:

```yaml
# Tuppence on a home server. Keep it on your LAN; use a VPN (e.g. Tailscale) for remote access.
# Optional local AI: docker compose --profile ollama up -d
services:
  tuppence:
    image: ghcr.io/szk1234/tuppence:latest
    build: .
    ports:
      - "8040:8040"
    volumes:
      - tuppence-data:/data
    restart: unless-stopped

  ollama:
    image: ollama/ollama:latest
    profiles: ["ollama"]
    volumes:
      - ollama:/root/.ollama
    restart: unless-stopped

volumes:
  tuppence-data: {}
  ollama: {}
```

`scripts/smoke_docker.sh` (chmod +x):

```bash
#!/usr/bin/env bash
# Build the image, run it on a random port, check /health, clean up.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${IMAGE:-tuppence:smoke}"
NAME="tuppence-smoke-$$"
docker build -t "$IMAGE" .
docker run -d --name "$NAME" -p 127.0.0.1::8040 "$IMAGE" >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
PORT="$(docker port "$NAME" 8040/tcp | head -1 | sed 's/.*://')"
if uv run --quiet python scripts/smoke_http.py "http://127.0.0.1:${PORT}" --timeout 60 --expect-mode server; then
  # the container must run as a non-root user
  USER_ID="$(docker exec "$NAME" id -u)"
  [ "$USER_ID" != "0" ] || { echo "smoke: FAILED container runs as root"; exit 1; }
  echo "smoke: ok docker"
else
  docker logs "$NAME" || true
  exit 1
fi
```

- [ ] **Step 3: Run the smoke test**

Run: `uv run pytest -m slow tests/smoke/test_packaging.py::test_docker_image_serves_health -q` → Expected: PASS (first build takes a few minutes).
Also: `docker image ls tuppence:smoke --format '{{.Size}}'` → note size (target < 250 MB).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Add Docker image, compose file and Docker smoke test" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJjEQDR3UGuFJz6TzhmiFM"
```

---

### Task 5: Desktop shell (pywebview + PyInstaller) with headless smoke mode

**Files:**
- Modify: `src/tuppence/desktop/launcher.py` (replace placeholder)
- Create: `desktop/entry.py`, `desktop/tuppence.spec`, `scripts/build_desktop.py`
- Modify: `pyproject.toml` (no change unless PyInstaller needs hooks), `tests/smoke/test_packaging.py` (add desktop smoke)
- Test: `tests/desktop/__init__.py`, `tests/desktop/test_launcher.py`

**Interfaces:**
- Consumes: `create_app`, `RuntimeSettings.for_mode("desktop", ...)`, `resolve_data_dir`.
- Produces:
  - `find_free_port(host: str = "127.0.0.1") -> int`
  - `new_launch_token() -> str` (≥ 32 url-safe chars)
  - `class ServerThread(app, host: str, port: int)` with `start() -> None`, `wait_until_healthy(timeout: float = 20.0) -> None` (raises `TimeoutError`), `stop(timeout: float = 5.0) -> None`, property `url -> str` (`http://127.0.0.1:{port}/`)
  - `run_desktop(data_dir: str | None = None, *, smoke: bool = False, smoke_out: str | None = None, open_window: Callable[[str], bool] | None = None, open_browser: Callable[[str], object] | None = None, wait_forever: Callable[[], None] | None = None) -> int`
  - `open_webview_window(url: str) -> bool` — returns False if pywebview or a GUI backend is unavailable.
  - Desktop smoke JSON: `{"ok": true, "url": "...", "version": "...", "mode": "desktop"}`.

- [ ] **Step 1: Write the failing tests**

`tests/desktop/__init__.py`: empty.

`tests/desktop/test_launcher.py`:

```python
import json
import socket

import httpx

from tuppence.desktop import launcher


def test_find_free_port_is_bindable():
    port = launcher.find_free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_launch_tokens_are_long_and_unique():
    a, b = launcher.new_launch_token(), launcher.new_launch_token()
    assert a != b and len(a) >= 32


def test_server_thread_serves_health(tmp_path):
    from tuppence.app import create_app
    from tuppence.settings import RuntimeSettings

    port = launcher.find_free_port()
    settings = RuntimeSettings.for_mode("desktop", data_dir=tmp_path, port=port)
    server = launcher.ServerThread(create_app(settings), "127.0.0.1", port)
    server.start()
    try:
        server.wait_until_healthy(15)
        assert httpx.get(server.url + "health").json()["mode"] == "desktop"
    finally:
        server.stop()


def test_smoke_mode_writes_result_and_exits(tmp_path):
    out = tmp_path / "smoke.json"
    rc = launcher.run_desktop(data_dir=str(tmp_path / "data"), smoke=True, smoke_out=str(out))
    assert rc == 0
    result = json.loads(out.read_text())
    assert result["ok"] is True and result["mode"] == "desktop"
    assert result["url"].startswith("http://127.0.0.1:")


def test_falls_back_to_browser_when_no_gui(tmp_path):
    opened = []
    rc = launcher.run_desktop(
        data_dir=str(tmp_path / "data"),
        open_window=lambda url: False,          # pywebview unavailable
        open_browser=lambda url: opened.append(url),
        wait_forever=lambda: None,              # don't block the test
    )
    assert rc == 0
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:")
```

Run: `uv run pytest tests/desktop -q` → Expected: FAIL (`NotImplementedError` / missing names).

- [ ] **Step 2: Implement `launcher.py`**

```python
"""Desktop shell: run the app on a random loopback port inside a native window."""

from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

import httpx
import uvicorn

from tuppence import __version__
from tuppence.paths import DataDirError, resolve_data_dir
from tuppence.settings import RuntimeSettings


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def new_launch_token() -> str:
    return secrets.token_urlsafe(32)


class ServerThread:
    def __init__(self, app: object, host: str, port: int) -> None:
        self.host, self.port = host, port
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="on")  # type: ignore[arg-type]
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="tuppence-server", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        self._thread.start()

    def wait_until_healthy(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if httpx.get(self.url + "health", timeout=1.0).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise TimeoutError(f"Tuppence didn't start within {timeout:.0f}s")

    def stop(self, timeout: float = 5.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout)


def open_webview_window(url: str) -> bool:
    try:
        import webview  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - any import failure means "no GUI available"
        return False
    try:
        webview.create_window("Tuppence", url, width=1280, height=820, min_size=(900, 600))
        webview.start()
    except Exception:  # noqa: BLE001 - missing GTK/WebView2 etc.
        return False
    return True


def _wait_forever() -> None:
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def run_desktop(
    data_dir: str | None = None,
    *,
    smoke: bool = False,
    smoke_out: str | None = None,
    open_window: Callable[[str], bool] | None = None,
    open_browser: Callable[[str], object] | None = None,
    wait_forever: Callable[[], None] | None = None,
) -> int:
    from tuppence.app import create_app

    try:
        root = resolve_data_dir(data_dir)
    except DataDirError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    port = find_free_port()
    settings = RuntimeSettings.for_mode("desktop", data_dir=root, port=port, launch_token=new_launch_token())
    server = ServerThread(create_app(settings), "127.0.0.1", port)
    server.start()
    try:
        server.wait_until_healthy()
        if smoke:
            result = {"ok": True, "url": server.url, "version": __version__, "mode": "desktop"}
            text = json.dumps(result)
            if smoke_out:
                Path(smoke_out).write_text(text, encoding="utf-8")
            print(text)
            return 0
        if not (open_window or open_webview_window)(server.url):
            print(f"No desktop window available; opening Tuppence in your browser at {server.url}")
            (open_browser or webbrowser.open)(server.url)
            (wait_forever or _wait_forever)()
        return 0
    finally:
        server.stop()
```

(Launch-token use for auth arrives in M1; the token is generated now so the settings contract is fixed.)

`desktop/entry.py`:

```python
"""PyInstaller entry point for the desktop app."""

import sys

from tuppence.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(["desktop", *args]))
```

`desktop/tuppence.spec`:

```python
# PyInstaller spec — onedir build of the Tuppence desktop app.
# Build: uv run --group build --extra desktop pyinstaller desktop/tuppence.spec --noconfirm --clean
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is injected by PyInstaller)
WEB = ROOT / "src" / "tuppence" / "web_dist"
if not (WEB / "index.html").is_file():
    raise SystemExit("Build the UI first: npm --prefix web ci && npm --prefix web run build")

hidden = collect_submodules("uvicorn") + collect_submodules("tuppence")
try:
    hidden += collect_submodules("webview")
except Exception:  # pywebview not installed: window falls back to the browser
    pass

a = Analysis(  # noqa: F821
    [str(ROOT / "desktop" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(WEB), "tuppence/web_dist")],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)  # noqa: F821
windowed = sys.platform in ("win32", "darwin")
exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="Tuppence",
    console=not windowed, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Tuppence")  # noqa: F821
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Tuppence.app", bundle_identifier="org.tuppence.app")  # noqa: F821
```

`scripts/build_desktop.py`:

```python
"""Build the desktop bundle with PyInstaller, then smoke-test the frozen binary."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def binary_path() -> Path:
    base = ROOT / "dist" / "Tuppence"
    if sys.platform == "win32":
        return base / "Tuppence.exe"
    if sys.platform == "darwin":
        return ROOT / "dist" / "Tuppence.app" / "Contents" / "MacOS" / "Tuppence"
    return base / "Tuppence"


def main() -> int:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "desktop/tuppence.spec", "--noconfirm", "--clean"],
        cwd=ROOT,
        check=True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "smoke.json"
        data = Path(tmp) / "data dir ü"
        subprocess.run(
            [str(binary_path()), "--smoke", "--smoke-out", str(out), "--data-dir", str(data)],
            check=True,
            timeout=120,
        )
        result = json.loads(out.read_text(encoding="utf-8"))
    if not result.get("ok"):
        print(f"desktop smoke: FAILED {result}")
        return 1
    print(f"smoke: ok desktop {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add to `tests/smoke/test_packaging.py`:

```python
@pytest.mark.slow
def test_desktop_bundle_smoke():
    if not (ROOT / "src" / "tuppence" / "web_dist" / "index.html").is_file():
        pytest.skip("UI not built")
    result = subprocess.run(
        ["uv", "run", "--group", "build", "--extra", "desktop", "python", "scripts/build_desktop.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=1800,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert "smoke: ok desktop" in result.stdout
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/desktop -q` → Expected: 5 passed.
Run: `uv sync --group build --extra desktop` → Expected: installs pyinstaller + pywebview. (On Linux without GTK dev libs pywebview still installs; its GUI import fails at runtime and the launcher falls back — that's the tested behaviour.)
Run: `uv run pytest -m slow tests/smoke/test_packaging.py::test_desktop_bundle_smoke -q` → Expected: PASS on this Linux host; `dist/Tuppence/Tuppence` exists.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Add desktop shell with pywebview window, browser fallback and PyInstaller smoke build" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJjEQDR3UGuFJz6TzhmiFM"
```

---

### Task 6: Wheel/uvx smoke and CI workflows

**Files:**
- Create: `scripts/smoke_wheel.sh`, `.github/workflows/ci.yml`, `.github/workflows/release.yml`
- Modify: `tests/smoke/test_packaging.py` (wheel test)

**Interfaces:**
- Consumes: `scripts/smoke_http.py`, `scripts/build_desktop.py`, `scripts/denylist_guard.py --require`.
- Produces: CI jobs `python`, `web`, `denylist`, `package`, `desktop` (matrix ubuntu/windows/macos); release workflow pushing `ghcr.io/szk1234/tuppence:{version,latest}` (linux/amd64, linux/arm64) and uploading desktop archives on `v*` tags.

- [ ] **Step 1: Write the failing wheel smoke test**

Append to `tests/smoke/test_packaging.py`:

```python
@pytest.mark.slow
def test_wheel_contains_ui_and_runs():
    if not (ROOT / "src" / "tuppence" / "web_dist" / "index.html").is_file():
        pytest.skip("UI not built")
    result = subprocess.run(["bash", "scripts/smoke_wheel.sh"], cwd=ROOT, capture_output=True, text=True, timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "smoke: ok wheel" in result.stdout
```

Run: `uv run pytest -m slow tests/smoke/test_packaging.py::test_wheel_contains_ui_and_runs -q` → FAIL (script missing).

- [ ] **Step 2: Implement `scripts/smoke_wheel.sh`** (chmod +x)

```bash
#!/usr/bin/env bash
# Build the wheel, check the UI is inside, install it like `uvx` would, and health-check it.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf dist/*.whl
uv build --wheel --out-dir dist >/dev/null
WHEEL="$(ls dist/tuppence-*.whl | head -1)"
python3 - "$WHEEL" <<'PY'
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
assert any(n.endswith("tuppence/web_dist/index.html") for n in names), "web_dist missing from wheel"
assert any("/web_dist/assets/" in n for n in names), "assets missing from wheel"
PY
TMP="$(mktemp -d)"
trap 'kill "${PID:-0}" 2>/dev/null || true; rm -rf "$TMP"' EXIT
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
uvx --from "$WHEEL" tuppence serve --no-browser --port "$PORT" --data-dir "$TMP/data" >"$TMP/log" 2>&1 &
PID=$!
if uv run --quiet python scripts/smoke_http.py "http://127.0.0.1:${PORT}" --timeout 90 --expect-mode local; then
  curl -fsS "http://127.0.0.1:${PORT}/" | grep -q 'id="app"'
  echo "smoke: ok wheel"
else
  cat "$TMP/log"; exit 1
fi
```

Run the test again → Expected: PASS.

- [ ] **Step 3: Write CI workflows**

`.github/workflows/ci.yml`:

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:
permissions:
  contents: read
jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pyright
      - run: uv run pytest -q

  web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm, cache-dependency-path: web/package-lock.json }
      - run: npm --prefix web ci
      - run: npm --prefix web test
      - run: npm --prefix web run build
      - uses: actions/upload-artifact@v4
        with: { name: web_dist, path: src/tuppence/web_dist }

  denylist:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv run --no-project python scripts/denylist_guard.py --require --no-git-config
        env:
          TUPPENCE_DENYLIST: ${{ secrets.TUPPENCE_DENYLIST }}

  package:
    needs: web
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - uses: actions/download-artifact@v4
        with: { name: web_dist, path: src/tuppence/web_dist }
      - run: bash scripts/smoke_wheel.sh
      - run: bash scripts/smoke_docker.sh

  desktop:
    needs: web
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - uses: actions/download-artifact@v4
        with: { name: web_dist, path: src/tuppence/web_dist }
      - run: uv sync --frozen --group build --extra desktop
      - run: uv run python scripts/build_desktop.py
```

`.github/workflows/release.yml`:

```yaml
name: Release
on:
  push:
    tags: ["v*"]
permissions:
  contents: write
  packages: write
jobs:
  web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: npm --prefix web ci && npm --prefix web run build
      - uses: actions/upload-artifact@v4
        with: { name: web_dist, path: src/tuppence/web_dist }

  image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with: { registry: ghcr.io, username: "${{ github.actor }}", password: "${{ secrets.GITHUB_TOKEN }}" }
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ghcr.io/${{ github.repository }}:${{ github.ref_name }}
            ghcr.io/${{ github.repository }}:latest

  desktop:
    needs: web
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - uses: actions/download-artifact@v4
        with: { name: web_dist, path: src/tuppence/web_dist }
      - run: uv sync --frozen --group build --extra desktop
      - run: uv run python scripts/build_desktop.py
      - shell: bash
        run: |
          cd dist
          if [ "$RUNNER_OS" = "macOS" ]; then ditto -c -k --keepParent Tuppence.app "Tuppence-${GITHUB_REF_NAME}-macos.zip";
          elif [ "$RUNNER_OS" = "Windows" ]; then 7z a "Tuppence-${GITHUB_REF_NAME}-windows.zip" Tuppence;
          else tar czf "Tuppence-${GITHUB_REF_NAME}-linux.tar.gz" Tuppence; fi
      - uses: softprops/action-gh-release@v2
        with: { files: "dist/Tuppence-*", prerelease: true }
```

- [ ] **Step 4: Lint the workflows**

Run: `uvx --from actionlint-py actionlint .github/workflows/*.yml` → Expected: no output, exit 0. Fix anything reported.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add wheel smoke test and CI/release workflows" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJjEQDR3UGuFJz6TzhmiFM"
```

---

### Task 7: M0 verification and publish the public repo

**Files:**
- Modify: `README.md` only if a command in it turned out wrong during verification.

**Interfaces:**
- Consumes: everything above; private denylist at `/path/outside/the/repo/denylist.txt`.
- Produces: public `https://github.com/szk1234/tuppence` with green CI; repo secret `TUPPENCE_DENYLIST`.

- [ ] **Step 1: Full local verification**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest -q
npm --prefix web test && npm --prefix web run build
uv run pytest -m slow -q          # docker + wheel + desktop smoke
TUPPENCE_DENYLIST_FILE=/path/outside/the/repo/denylist.txt uv run python scripts/denylist_guard.py --require
git log --all -p | grep -i -w -f <(grep -v '^#' /path/outside/the/repo/denylist.txt | sed '/^$/d') && echo "HISTORY LEAK" || echo "history clean"
```

Expected: everything green; final line `history clean`. **If history is not clean, stop and fix before publishing** (rewrite the offending local commits; the repo is unpublished so rewriting is safe).

- [ ] **Step 2: Create the public repo and push**

```bash
gh repo create szk1234/tuppence --public \
  --description "A private AI money coach for UK households. Your statements never leave your machine." \
  --homepage "https://github.com/szk1234/tuppence" --source . --remote origin --push
grep -v '^#' /path/outside/the/repo/denylist.txt | sed '/^$/d' | gh secret set TUPPENCE_DENYLIST --repo szk1234/tuppence
gh repo edit szk1234/tuppence --add-topic personal-finance --add-topic uk --add-topic local-first \
  --add-topic self-hosted --add-topic llm --add-topic privacy --add-topic ai-agents --add-topic budgeting
```

- [ ] **Step 3: Watch CI and fix failures**

```bash
gh run list --repo szk1234/tuppence --limit 3
gh run watch --repo szk1234/tuppence --exit-status "$(gh run list --repo szk1234/tuppence --limit 1 --json databaseId -q '.[0].databaseId')"
```

Expected: all jobs green. Windows/macOS desktop jobs are the most likely to need fixes (PyInstaller hidden imports, path quoting) — fix in small commits and re-push until green. Re-run the denylist job after setting the secret if the first run raced it.

- [ ] **Step 4: Record**

Add a short "M0 complete" note to the plan's checkboxes; nothing else to commit unless fixes were needed.
