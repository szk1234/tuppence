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
