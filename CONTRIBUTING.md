# Contributing to Tuppence

## Setup

```bash
uv sync                      # Python deps
npm --prefix web ci          # UI deps
npm --prefix web run build   # builds the UI into src/tuppence/web_dist
uv run tuppence serve        # http://127.0.0.1:8040
```

## Tests

```bash
uv run pytest                # fast tests
uv run pytest -m slow        # packaging smoke tests (builds images/bundles)
npm --prefix web test        # UI tests
bash scripts/e2e.sh          # browser end-to-end tests against a fresh server
```

The end-to-end tests need a browser; on first run: `cd web && npx playwright install chromium`.

## Personal-data guard

Tuppence handles financial data, so we make sure none of yours ends up in the
repository. A guard script (`scripts/denylist_guard.py`, standard library only)
checks contents and paths for terms you never want committed, such as your
name, address or employer. Matched terms are never printed, only the location
and the term's position in your list, so logs cannot leak it.

CI only runs the guard on pushes to `main`, after a merge, using a secret list.
That is a backstop, not protection: by then the data would already be public.
**The local hooks are the real protection**, so please install them:

1. Enable the hooks for your clone: `git config core.hooksPath .githooks`.
   This switches git to the scripts in `.githooks/`:
   - `pre-commit` scans the staged content (the index) and paths,
   - `commit-msg` scans the commit message,
   - `pre-push` scans every commit (message and patch) about to be pushed.
2. Put your terms, one per line, in a private file outside the repository, then
   point the guard at it with either
   `export TUPPENCE_DENYLIST_FILE=/path/to/denylist.txt` or
   `git config tuppence.denylistFile /path/to/denylist.txt`.

If a configured denylist file is missing or unreadable, the guard fails closed
(exit 2) instead of silently checking nothing.

## Fixtures must be synthetic

Never commit real statements, even redacted ones. Test data uses invented
people and companies, for example "Alex Example" paid by "Acme Payroll".

## Style

- Python: `uv run ruff check .`, `uv run ruff format .` and `uv run pyright`.
- UI copy and docs use UK English (organisation, licence, colour).

## Commits

Keep commits small, with an imperative subject line ("Add statement parser",
not "Added statement parser").
