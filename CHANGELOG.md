# Changelog

All notable changes to Tuppence are listed here. Versions follow [PEP 440](https://peps.python.org/pep-0440/)
in the package (`0.2.0.dev1`) and the same number as a git tag (`v0.2.0-dev.1`).

## [0.2.0.dev1] – developer preview

The first build for people who are happy to run unfinished software. Expect rough edges and
breaking changes; keep your own copies of your statements.

### What works
- **Three ways to run it:** Docker (`docker compose up`), from source (`uv run tuppence serve`),
  and unsigned desktop builds for Windows, macOS and Linux.
- **Sign-in:** a launch link on your own computer; first-run admin setup and sessions in Docker.
- **Household set-up:** people, the profile timeline, accounts and cards, income and pay dates,
  debts and goals, with a five-minute onboarding wizard.
- **Any AI, your keys:** local servers (Ollama, LM Studio, llama.cpp, vLLM, Jan) and cloud
  providers, task routing, budgets, the Local only switch, optional pseudonymising and a privacy log.
- **Statements:** CSV from 12 UK banks and cards, OFX/QFX, QIF, CAMT.053 and Excel with no AI;
  PDFs, scans and screenshots read by your AI and checked line by line; duplicate detection;
  a fix-up screen when the numbers don't add up.
- **Understanding:** every transaction gets a category (rules and memory first, then your AI in
  batches sized to its context window, then a second look at unsure ones), transfers between
  your accounts are paired, and bills, subscriptions and instalments are found with their next
  due date, yearly cost, price rises, missed payments, duplicates and free trials that turned
  into paid plans.
- **Pages:** Spending (treemap drill-down, filters, "Why?" panel, change a category and turn it
  into a rule), Commitments (calendar, yearly costs, flags), and Home cards.

### Not yet
- Questions, the learning loop, the researcher and the coach (M5).
- UK advisor skills, data-pack updates, live market data and reports (M6).
- Signed installers, notarisation and a published PyPI package (M7).

### Accuracy baseline
See "How well does it understand?" in the README: the numbers come from
`python -m evals.understand` on a synthetic household.
