# Tuppence

**Tuppence: a private AI money coach for UK households. Your statements never leave your machine.**

Upload your bank and card statements. Tuppence works out what every payment is
and *why* you make it, asks you smart questions, remembers the answers, and
gives UK-specific guidance — from bills creeping up to money you may be owed.

> **Privacy, plainly:** With a local model, nothing leaves your machine. With a
> cloud model, your statement text goes to the provider you chose, and every
> call is logged so you can see exactly what was sent.

> **Status: developer preview (v0.2.0-dev.1).** Tuppence imports your statements, works out
> what each payment is, pairs transfers between your accounts, and finds your bills and
> subscriptions. Questions, the coach and the UK advisor skills aren't in it yet. Expect rough
> edges and breaking changes, and keep your own copies of your statements.

## Developer preview

| How | For | Start here |
|---|---|---|
| Docker | home servers, NAS, self-hosters | [docs/install/docker.md](docs/install/docker.md): `TUPPENCE_VERSION=v0.2.0-dev.1 docker compose up -d` |
| Desktop app (unsigned) | Windows, macOS, Linux | download from [Releases](https://github.com/szk1234/tuppence/releases); [how to open an unsigned app](docs/install/desktop-unsigned.md) |
| From source | technical users, contributors | [docs/install/from-source.md](docs/install/from-source.md) |

What works in this preview:
- import CSV from 12 UK banks and cards, OFX/QFX, QIF, CAMT.053 and Excel with no AI, and
  PDFs, scans and screenshots with your AI (every amount checked against the file);
- every transaction sorted into a category: your rules and what Tuppence already knows first,
  then your AI in small batches, with a "Why?" for every decision;
- transfers between your own accounts and card repayments paired, so they don't count as spending;
- bills, subscriptions and instalments found from the gaps between payments, with next due
  dates, yearly costs, price rises, missed payments, duplicates and free trials that turned paid;
- Spending (drill down from "Food & drink" to a single payment) and Commitments (a calendar of
  what's due) pages, and Home cards.

Not yet: questions and the learning loop, the coach, UK checks and advice, reports, signed
installers. See [CHANGELOG.md](CHANGELOG.md).

Platform note: reading scans and screenshots uses on-device OCR (onnxruntime and
OpenCV), which needs macOS 14 or later (Apple silicon or Intel), 64-bit Windows
(x86-64) or 64-bit Linux (x86-64 or ARM64). Windows on ARM isn't supported yet.

Never expose Tuppence directly to the internet. For remote access use a VPN
such as Tailscale.

## What it is (and isn't)

- **Guidance, not regulated financial advice.** Tuppence explains your money
  and points you to official tools and free advice services (MoneyHelper,
  StepChange, Citizens Advice). It never recommends specific investment products.
- **Bring any AI:** local (Ollama, LM Studio, llama.cpp, vLLM, Jan, Foundry Local) or
  cloud (Anthropic, OpenAI, Gemini, OpenRouter, Mistral, Groq, Together AI, xAI,
  DeepSeek, Qwen, Kimi, GLM) with your own key, or any OpenAI-compatible server
  through the custom option.
- **Works without AI** for importing, rules, budgets and UK checks; AI adds the
  understanding.

## Which AI model is enough?

CSV, OFX/QFX, QIF, CAMT.053 and Excel statements never need an AI model: they are
read by fixed importers on your machine. PDFs, scans and screenshots do. The table
below is produced by `python -m evals.run` on a synthetic statement corpus (see
CONTRIBUTING.md), so you can see what a model costs and how well it reads.

<!-- model-table:start -->
| Model | Statements read correctly | Needing AI | Row accuracy | £ per AI statement | Seconds per AI statement |
|---|---|---|---|---|---|
<!-- model-table:end -->

## How well does it understand?

Measured with `uv run python -m evals.understand` on a synthetic household (12 months, three
accounts, about 540 transactions, 20 commitments). "Top level" and "Level 2" are the share of
transactions in the right category at that level of the tree. The `oracle` row is a keyword
stand-in that proves the pipeline works end to end; it says nothing about a real model.
Targets for v1.0: 90% top level and 75% level 2 with the recommended local model; 95% top level
with the recommended cloud model.

<!-- understanding-table:start -->
| Model | Top level | Level 2 | Still unknown | Commitments | AI calls | £ |
|---|---|---|---|---|---|---|
| oracle | 99.6% | 99.6% | 0.0% | 20/20 | 16 | £0.0000 |
| Custom (OpenAI-compatible)//models/qwen2.5-0.5b-instruct-q4_k_m.gguf | 21.5% | 20.0% | 73.5% | 12/20 | 122 | £0.0000 |
<!-- understanding-table:end -->

The `Custom (OpenAI-compatible)` row is a baseline with Qwen2.5 0.5B (4-bit) run through
llama.cpp on one machine, a model far too small for this job; it is recorded so progress can
be measured, not as a recommendation. Accuracy with any model is also shaped by privacy
masking: Tuppence hides account details (titled names, mid-row dates, account numbers) from the
model, which can reduce the signal for person-to-person transfers.

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

UK bank holiday dates come from [GOV.UK](https://www.gov.uk/bank-holidays) and contain public
sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
See [NOTICE](NOTICE) for the bundled third-party data.
