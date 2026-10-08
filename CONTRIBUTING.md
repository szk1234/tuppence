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

The end-to-end and `tests/integration` tests talk to a fake LLM server
(`tests/fakes/fake_llm.py`) that speaks the OpenAI, Anthropic and Gemini wire formats.

### Live tests (optional, never in CI)

`uv run pytest -m live tests/live` calls real endpoints. Each test is skipped unless its
environment variables are set:

| Test | Variables |
| --- | --- |
| OpenAI-compatible | `TUPPENCE_LIVE_OPENAI_BASE`, `TUPPENCE_LIVE_OPENAI_KEY`, `TUPPENCE_LIVE_OPENAI_MODEL` |
| Anthropic | `ANTHROPIC_API_KEY` (optional `TUPPENCE_LIVE_ANTHROPIC_MODEL`, default `claude-haiku-4-5`) |
| Gemini | `GEMINI_API_KEY`, `TUPPENCE_LIVE_GEMINI_MODEL` |
| Local | `TUPPENCE_LIVE_LOCAL_BASE`, `TUPPENCE_LIVE_LOCAL_MODEL` |

Cloud tests may cost a little money. Add `-k local` to run only the local one.
`bash scripts/live_llamacpp.sh` needs Docker: it downloads Qwen2.5 0.5B (about 490 MB, cached in
`~/.cache/tuppence-models`), runs it in a llama.cpp server container and runs the local live test.

The end-to-end tests need a browser; on first run: `cd web && npx playwright install chromium`.

## Statement evals

- `uv run python -m evals.run --model oracle` checks the whole synthetic corpus with the deterministic oracle (no model needed; this is what CI runs).
- `uv run python -m evals.run --model "<connection>/<model id>" --out evals/results/<name>.json` scores a real model you have set up in Tuppence (Settings > AI); its usage and cost appear on the Usage page. For a local model, run any local server (for example the llama.cpp container that `scripts/live_llamacpp.sh` starts), add it in Settings > AI, then run the eval against it. Close Tuppence first: the eval needs the data folder to itself.
- `uv run python -m evals.table --write README.md` refreshes the README table from `evals/results/`.
- Regenerate the binary fixtures with `uv run python scripts/make_statement_fixtures.py`; never add a real statement to the corpus.

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

## Adding or confirming a bank layout

1. Never commit or attach a real statement. Compare only the heading row and the date and amount formats of your own export with the entry in `src/tuppence/datapacks/baseline/uk-banks/layouts.yaml`.
2. Edit or add the entry. `signature` lists headings that must all be present; refer to columns by heading, or `#0`, `#1`... for files without a heading row. Use `perspective: card` when purchases are positive.
3. Add a synthetic fixture `tests/fixtures/statements/csv/<id>.csv` with invented merchants and amounts (the October story used by the other fixtures is easiest), and a row in `EXPECTED` in `tests/ingest/test_csv_layouts.py`.
4. Set `confirmed: true` only after checking against a real export, and say so in the pull request.
5. Run `uv run python scripts/build_pack_manifest.py src/tuppence/datapacks/baseline/uk-banks` and `uv run pytest tests/ingest -q`.
6. Your own layouts can also live outside the app, in `<data folder>/config/importers/*.yaml` with the same format.

## Style

- Python: `uv run ruff check .`, `uv run ruff format .` and `uv run pyright`.
- UI copy and docs use UK English (organisation, licence, colour).

## Commits

Keep commits small, with an imperative subject line ("Add statement parser",
not "Added statement parser").

## Understanding evals

- `uv run python -m evals.understand --model oracle --require-targets` runs the whole analysis on a synthetic 12-month household with the deterministic oracle (no model needed; CI runs this).
- `uv run python -m evals.understand --model "<connection>/<model id>" --out evals/results/understanding-<name>.json` scores a real model you've set up (Settings › AI, or a throwaway `--data-dir`); `--months 24` and `--seed` vary the household.
- `uv run python -m evals.understand --table --write README.md` refreshes the README table from `evals/results/understanding-*.json`.
- `uv run python -m evals.household --write-fixture` regenerates the browser test's statement; never add real statements.
