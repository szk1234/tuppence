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
| Docker | home servers, NAS, self-hosters | from a clone of this repo: `git clone https://github.com/szk1234/tuppence && cd tuppence && docker compose up -d --build`, then open `http://<server>:8040` (a published image arrives with the developer preview) |
| Desktop app | Windows, macOS, Linux | download from Releases (coming with the developer preview) |
| uvx | technical users | not yet on PyPI; arrives with the developer preview. For now, from a clone: `uv run tuppence serve` |

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
