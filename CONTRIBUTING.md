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
```

## Personal-data guard

Tuppence handles financial data, so we make sure none of yours ends up in the
repository. A guard script scans every tracked and untracked-but-not-ignored
file (contents and paths) for terms you never want committed, such as your
name, address or employer.

1. Enable the hook for your clone: `git config core.hooksPath .githooks`
2. Put your terms, one per line, in a private file outside the repository, then
   point the guard at it with either
   `export TUPPENCE_DENYLIST_FILE=/path/to/denylist.txt` or
   `git config tuppence.denylistFile /path/to/denylist.txt`.

CI runs the same guard with a secret list. The guard never prints matched
terms, only the file, line and the term's position in the list, so logs cannot
leak it.

## Fixtures must be synthetic

Never commit real statements, even redacted ones. Test data uses invented
people and companies, for example "Alex Example" paid by "Acme Payroll".

## Style

- Python: `uv run ruff check .`, `uv run ruff format .` and `uv run pyright`.
- UI copy and docs use UK English (organisation, licence, colour).

## Commits

Keep commits small, with an imperative subject line ("Add statement parser",
not "Added statement parser").
