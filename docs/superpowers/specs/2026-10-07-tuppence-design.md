# Tuppence — Umbrella Design

Status: approved in brainstorming 2026-10-07 · Scope: iteration 1 (v1.0) plus roadmap

Tuppence is a private AI money coach for UK households. It runs on the
user's own machine as a Docker container or a native desktop app. It reads
bank and card statements, works out what each payment is and *why* it is
made, asks good questions, remembers the answers and gives UK-specific
guidance. It works with any LLM, local or cloud, using the user's own keys.

This umbrella spec fixes the cross-cutting design. Milestones M2–M7 each get
a short spec of their own when they start, building on this one (§16).

## 1. Intent

### 1.1 Who it is for

1. Self-hosters and local-AI enthusiasts (Docker, home servers).
2. UK personal-finance enthusiasts (desktop app).
3. Mainstream UK households under cost-of-living pressure (desktop app,
   once installers are polished).

### 1.2 What success looks like for v1.0

- **Install with no terminal.** A non-technical user goes from download to
  wizard to first statement imported to a report on Windows, macOS and
  Linux. `docker compose up` on a clean machine gives a working app.
- **Fully offline with a local model.** With the "Local only" switch on, no
  LLM or research call leaves the machine. The only other network calls are
  anonymous data-pack and market-data downloads, and each has its own
  setting to switch it off. A test with the network blocked proves the app
  still works fully.
- **Any statement.**
  - At least 12 UK bank and card CSV layouts, plus OFX, QIF and CAMT.053,
    import deterministically with no LLM calls.
  - Text PDFs, scanned PDFs and screenshots from at least 5 UK banks either
    pass the checks or produce clear questions.
- **Accurate understanding.**
  - **Target:** at least 90% of transactions in the right top-level category
    and at least 75% right to level 2, using the recommended local model on
    the synthetic eval corpus.
  - **Cloud target:** at least 95% right at the top level with the
    recommended cloud model.
  - **If targets prove unreachable:** M4 establishes the baseline, and the M4
    spec revises these targets if needed.
- **It visibly learns.**
  - **Corrections become rules:** a 👎 plus an edit, once generalised and
    accepted, means the next import categorises matching rows with no LLM
    call.
  - **No repeated questions:** a topic that has been answered is never asked
    again within its cooldown (property test).
  - **Fewer questions over time:** questions per statement fall across 6
    months of synthetic history.
- **Agents can't run away.**
  - Tests with hostile scripted LLMs pass.
  - No run exceeds its configured budget for calls, tokens, £ or time.
- **Grounded UK guidance.**
  - Every advisor skill has unit tests, using official worked examples where
    they exist.
  - Every entitlement rule cites an official source and has a last-reviewed
    date.

### 1.3 Non-goals for v1.0

- **Open Banking sync.** The free tiers are disappearing, and an aggregator
  would see the data.
- **Double-entry accounting.**
- **Regulated, personalised investment advice.**
- **Native mobile apps.**
- **Multi-currency beyond conversion.** Foreign amounts are converted for
  display; no multi-currency ledger.
- **A hosted SaaS version.**

## 2. Product guardrails

- **Positioning:** "Tuppence: a private AI money coach for UK households.
  Your statements never leave your machine."
- **Privacy claim, worded exactly.** The README uses this wording: "With a
  local model, nothing leaves your machine. With a cloud model, your statement
  text goes to the provider you chose, and every call is logged so you can see
  exactly what was sent."
- **Regulatory framing.**
  - All output is *insights and guidance*, never regulated financial advice.
  - No product-specific investment recommendations.
  - Debt features signpost MoneyHelper, StepChange and Citizens Advice.
  - A one-line disclaimer appears in onboarding and in reports.
  - A regulatory wording review is a release gate (M7). Debt and pension
    guidance sit close to FCA-regulated activities.
- **Licence:** AGPL-3.0. Also CONTRIBUTING.md, SECURITY.md and PRIVACY.md.
- **No personal data in the repo.**
  - All fixtures are synthetic.
  - A pre-commit hook and a CI job grep for a denylist of known personal
    strings. The denylist is read from a CI secret or a local gitignored file,
    never from the repo.
- **Origin.** Ideas and some agent code are ported from a private predecessor
  app (House Fund v3). Code is ported module by module and scrubbed, never
  bulk-copied. Tuppence is a separate product, so there is no migration from
  it.

## 3. Architecture

### 3.1 One app, three ways to run it

A single Python process (FastAPI + uvicorn) hosts:
- the API;
- the prebuilt web UI;
- the LangGraph agent runtime;
- a job worker.

| Shell | Bind | Auth | Secrets |
|---|---|---|---|
| **Desktop** (pywebview window, PyInstaller build per OS) | `127.0.0.1`, random port | Per-launch token injected into the webview; no login screen | OS keychain (`keyring`) |
| **Docker** (multi-arch image, compose with optional Ollama) | `0.0.0.0:8040` | First-run admin setup (no default credentials); multiple users per household | Docker secrets, or settings encrypted with a key from a secret file |
| **`uvx tuppence` / `pipx`** (technical users) | `127.0.0.1:8040` | As desktop | OS keychain |

All paths resolve from the OS app-data directory (`platformdirs`) or from
`TUPPENCE_DATA_DIR`, never from the working directory.

### 3.2 Code layout

```
tuppence/
  app/        FastAPI app, routes, auth modes, SSE streaming, job worker
  core/       domain model, SQLite store, migrations, effective-dated profile
  llm/        providers, connection registry, model catalogue, task routing,
              budgets, structured output, pseudonymiser, privacy log
  ingest/     importer registry, PDF/OCR, identify, LLM read, check, dedupe
  agents/     LangGraph graphs: analysis workflow, specialists, coach, guards
  knowledge/  knowledge store API: understanding, merchants, facts, rules,
              questions, feedback, knowledge_version
  advisor/    skill runner, calculators, entitlement engine
  skills/     one folder per skill (SKILL.md + calc.py + tests/)
  datapacks/  pack loader, signature verify, updater
  market/     live market-data providers + cache
  reports/    facts (code), commentary (LLM), number guard, wording guard
  config/     defaults: agents/*.toml, presets, prompt templates (Jinja)
web/          Svelte 5 + Vite UI; built output shipped inside the wheel
desktop/      pywebview launcher, PyInstaller specs, installer configs
docker/       Dockerfile, compose files, Caddy example
evals/        synthetic corpus, expectations, harness, model report
```

Users never need Node; only UI contributors do.

### 3.3 Storage

- `tuppence.db` (SQLite, WAL mode) holds all domain and knowledge data.
- `checkpoints.db` holds LangGraph run state (`SqliteSaver`), so runs can
  resume after a restart.
- `files/` holds the raw uploaded statements.
- **Knowledge lives in our own relational tables (§7), behind a
  `KnowledgeStore` interface the graphs call.** LangGraph's Store is not used
  in v1, so there is one source of truth that can be queried, versioned and
  tested.

### 3.4 Two kinds of data

| Kind | Examples | Who writes |
|---|---|---|
| **What you've told it** | Household, people, profile timeline, accounts, incomes, debts, goals, confirmed facts and rules, settings | Only the user, directly or by confirming a proposal or confirm card |
| **What the agent has worked out** | Understanding rows, merchants, inferred facts, commitments, questions, recommendations, reports | Agents and code, always recording where it came from |

- Every row the user owns carries `version`. A stale write returns 409.
- When an agent wants to change user-owned data, it files a **proposal**
  (shown in the Inbox). Nothing changes until the user accepts.

### 3.5 Optional MCP endpoint

- Off by default.
- Needs a token.
- Binds to loopback unless explicitly configured otherwise.
- CORS is locked down.

## 4. LLM connections and privacy

### 4.1 Connections

The user adds any number of connections. Each is a preset plus a base URL,
an optional key and optional headers, and has a "Test" button that lists the
models.

- **Local presets, auto-detected on their default ports:** Ollama, LM Studio,
  llama.cpp server, vLLM, Jan, Foundry Local.
- **Cloud presets (bring your own key):**
  - Anthropic, OpenAI, Google Gemini, OpenRouter;
  - Mistral, Groq, Together, xAI;
  - DeepSeek, Qwen (Alibaba Model Studio), Kimi (Moonshot), GLM (Z.ai);
  - custom OpenAI-compatible.
- **Adapters, one per API style:** OpenAI-compatible, Anthropic and Gemini.
  - Dependencies are `httpx` and `pydantic` only, with no vendor SDKs.
  - Deliberately no LiteLLM, to keep the supply chain small for an app that
    holds every provider key.

### 4.2 Model catalogue

For each model the catalogue records:
- context window;
- tool calling;
- structured output;
- vision;
- input and output price per million tokens.

Values come from the provider API where it offers them, otherwise from the
`model-catalogue` data pack.

### 4.3 Task routing

There is a fixed set of tasks:
- `read`: transcribe a statement;
- `categorise`;
- `review`;
- `research`: thinking model plus tools;
- `coach`: chat and question wording;
- `report`;
- `vision`: OCR.

How routing works:
- **Simple mode:** one model for everything.
- **Advanced mode:** each task has an ordered fallback chain of models.
- **Local pinning:** any task can be pinned to local-only.

### 4.4 Structured output

- Each provider's native support is used:
  - OpenAI's `json_schema` with strict mode;
  - Anthropic's structured output and tool use;
  - Gemini's `responseSchema`;
  - Ollama's `format`.
- **Fallback:** extract the JSON, validate it, then make one repair call with
  the validation errors.
- **One source of schemas:** Pydantic models.

### 4.5 Reliability and budgets

- **Retries:** exponential backoff that honours `Retry-After` on 429 and 5xx.
- **Circuit breaker** per connection.
- **Fallback** to the next model in the chain.
- **Timeouts** per task.
- **Prompt sizing:** prompts are built to fit about 60% of the model's
  context window, reserving room for the output (§10.3).
- **Spending caps:** per run and per calendar month, in £, computed from the
  catalogue prices and the usage the provider reports.
- **Usage page:** tokens and £ by task, model and day.

### 4.6 Privacy controls

| Control | Default | Behaviour |
|---|---|---|
| **Local only** | Off | Blocks every LLM and research call to a non-loopback host, at the HTTP client layer. Data-pack updates and live market data have their own on/off settings (§11.4, §12.1) |
| **Cloud notice** | Shown once per cloud connection | "This provider will see your statement text." An acknowledgement, not a gate on every call |
| **Pseudonymise before cloud calls** | **Off** | Swaps sensitive values for consistent stand-ins: household names → roles ("Adult A", "Child 1"); account and card numbers → "ACCT_2 ending ••34"; sort codes, IBANs, postcodes, emails and phone numbers → tokens; other people's names → "Person 3". The mapping stays local and replies are turned back into real values. Merchant names, amounts and dates are never masked. Statement header and address blocks are never sent |
| **Privacy log** | On | Every outbound call (LLM, research, market data, packs) with time, destination, task, bytes and redaction count |

Keys are stored as listed in §3.1 and never appear in logs, including the
privacy log.

## 5. Onboarding and household model

### 5.1 Principle

Onboarding builds the skeleton in about 5 minutes; the agent fills in the
detail through conversation later.
- Every step can be skipped and resumed, and edited later in Settings.
- **Profile completeness meter:** skipped details unlock prompts such as
  "Add children's birth years to check Tax-Free Childcare".

### 5.2 Wizard

1. **Welcome:**
   - the disclaimer;
   - UK nation: England, Wales, Scotland or Northern Ireland, which drives tax
     bands and bank holidays;
   - postcode **district** only, e.g. "LS6".
2. **Household type:**
   - **Individual:** "Any children or dependants?"
   - **Family:** add a partner, then dependants.
   - **Person fields:** display name, role (adult, child, dependent adult),
     and birth year for children.
3. **Adults' work and income:**
   - employment status: employed, self-employed, both, retired, student or not
     working;
   - optional gross income band, which unlocks the tax and benefit checks;
   - income sources, each with net amount, owner and receiving account;
   - variable components: bonus, overtime, commission;
   - a pay rule (§5.4) and the next pay date.
4. **Home:**
   - renting, mortgage, owned outright or living with family;
   - monthly amount;
   - bedrooms;
   - council tax band if known;
   - mortgage fixed-rate end date if relevant.
5. **Accounts and cards:**
   - provider (UK bank picker or "Other"), type (current, savings, credit
     card), nickname, last 4 digits, and owner (one person or joint);
   - cards also record: limit, purchase APR, any promo rate and its end date,
     and statement day.
6. **Loans and debts:**
   - **Types:** personal loan, car finance (PCP or HP), mortgage, student loan
     (Plan 1, 2, 4, 5 or postgraduate), BNPL, overdraft, informal/family loan.
   - **Details:** lender, balance, APR, monthly payment, end date.
   - **Car finance also records:** agreement start date, broker (yes/no),
     balloon payment, total amount payable, annual mileage allowance.
7. **Goals:** name, target, date and priority. Suggest an emergency fund if
   there isn't one.
8. **AI setup:**
   - auto-detect local servers, or add a key;
   - test the connection, pick a model (Simple mode), and pick a preset
     (§11.2);
   - a research-lookups toggle (§12.5), presented as recommended on, with a
     plain explanation of what gets sent.
9. **First upload:** "Drop in your last 3 months of statements."

Iteration 2 adds a "chat to set up" mode that fills the same forms through
the coach.

### 5.3 Profile timeline (effective dating)

- **What is effective-dated:** people, roles, employment, income sources, pay
  rules, accounts, address/nation and household membership. Each carries
  `valid_from` and `valid_to`.
- **Examples:**
  - a third child born in May 2027;
  - Adult A employed until 2026-04-30, then unemployed;
  - a partner moving in.
- **Reading the profile:** every analysis reads the profile *as of the
  transaction date*. Advisor skills read it *as of today*.
- **Ways it changes:**
  - the Settings › Household timeline editor;
  - accepted life-event proposals (§8.2);
  - coach confirm cards.

### 5.4 Pay rules and periods

- **Pay rules:**
  - weekly, fortnightly or 4-weekly from an anchor date;
  - fixed day of month;
  - last working day;
  - day *n* moved to the previous working day.
- **Working days** use the nation's bank-holiday calendar, from the `uk-tax`
  pack (§12.2).
- **Reporting period:** the calendar month by default. It can switch to a pay
  cycle anchored on any adult's main income.

### 5.5 Core user-owned tables

- `household`: nation, postcode district, currency (GBP), period mode
- `person`
- `profile_entry`: effective-dated attributes
- `account`: provider, kind, last 4, owners, limit, APR, promo end, statement
  day, status
- `income_source`, with `pay_rule` and variable components
- `debt` and `debt_entry`
- `goal`
- `holding` (§11.5)
- `cash_wallet` (§11.1)

## 6. Statement ingestion

### 6.1 Inputs

CSV, OFX/QFX, QIF, CAMT.053, XLSX, PDFs with a text layer, scanned PDFs, and
images (PNG, JPEG, HEIC).

### 6.2 Pipeline (LangGraph subgraph)

1. **Extract.**
   - PDFs with a text layer use `pdfplumber`, whose word positions let rows be
     rebuilt cleanly.
   - Scanned pages and images use RapidOCR (Apache-2.0, CPU).
   - The `vision` task can be selected instead for difficult scans, e.g. a
     local vision model via Ollama, or a cloud vision model.
   - No AWS Textract.
2. **Identify.** This happens on the machine and never needs an LLM.
   - **Bank fingerprint:**
     - CSV header signatures;
     - PDF text markers, such as a bank's legal name;
     - OFX `BANKID` and `ACCTID`.
   - **Account match:** provider, type and last 4 digits from the statement
     header, compared with registered accounts.
   - **One strong match:** assigned automatically.
   - **Otherwise:** an `interrupt()` question, "Which account is this?", with
     the best guess pre-selected and a "+ new account" option pre-filled from
     the statement.
   - The answer is remembered against that layout fingerprint.
3. **Parse.**
   - **Fixed importers:** OFX, QIF, CAMT, XLSX, and known CSV layouts from a
     YAML registry in the `uk-banks` pack. The pack starts with Monzo,
     Starling, HSBC, Barclays, Lloyds/Halifax, NatWest, Santander, Nationwide,
     Chase, Revolut, Amex and Barclaycard.
   - **Unknown CSV:**
     - the LLM sees the header plus 5 sample rows *once* and proposes a column
       mapping;
     - if that passes Check, it's saved as a local layout;
     - later files in that format need no LLM call.
   - **PDF or OCR text:** the LLM `read` step transcribes in chunks. Each row
     has `ref`, `date`, signed `amount`, `amount_text` and the raw
     description (v3's approach).
4. **Check** (ported from v3).
   - **Amount evidence:** each row's `amount_text` must appear on the line it
     came from and match |amount| within 0.005.
   - **Sign:** must follow the printed convention, with debit and credit
     handled per account type.
   - **Coverage:** every data line is either a transaction or explicitly
     skipped, exactly once.
   - **Dates:** within the statement period, ±3 days.
   - **Balances:** opening + Σ = closing within £0.01, and the running balance
     is continuous.
   - **Retry loop:** a failure loops back to Parse with the errors, at most 3
     attempts per chunk.
   - **After that:** the statement is marked `needs_review` and gets a fix-up
     screen.
   - **Screenshots** only get the amount-evidence and dedupe checks, and are
     labelled *balance unverified*.
5. **Dedupe.**
   - **Fingerprint:** account, date, amount, normalised description, and the
     occurrence index among identical lines.
   - **Constraint:** `UNIQUE(account_id, fingerprint)`.
6. **Persist.**
   - All rows are written in one transaction.
   - Closing balances update each account's balance history.
   - Hand-off to the analysis workflow (§8).

### 6.3 Evaluation

- **Corpus:** a synthetic statement for each importer, plus scanned and
  screenshot cases, with expectation files.
- **Harness output:** accuracy, cost and latency per model, plus a generated
  "minimum viable local model" table for the README.

Iteration 2 adds phone upload (Android share target, iOS Shortcut).

## 7. Knowledge model

Statement facts never change. Understanding is a separate, versioned layer
that keeps being revised.

| Table | Contents |
|---|---|
| `transaction` | Fixed: account, statement, date, amount, raw description, fingerprint |
| `understanding` | One row per transaction (see the field list below) |
| `understanding_history` | Every past version of an understanding row: audit trail and undo |
| `merchant` | The canonical merchant: known statement-text variants (e.g. "SQ *JS TRADING"), business type, Companies House number and SIC code, website, default category and purpose, evidence, confidence |
| `category` | **What** was bought. A tree of any depth (the UI shows 5 levels). Agents may add levels 2 and below; top-level categories are user-only |
| `purpose` | **Why** it was bought: the 5-whys graph. Nodes (Home, Car, Kid A, Commute, Side business…) have a parent and a kind (life driver, obligation, choice), and link to people |
| `fact` | Household knowledge (see the field list below) |
| `question` | `topic_key` (e.g. `housing.rent.why`, `merchant:<id>.identity`), kind (quick, why, life_event, verify, identify_account), targets, status, answer, `asked_count`, `cooldown_until` |
| `feedback` | Target (transaction, insight, question, report section, chat message), 👍 or 👎, correction, comment |
| `rule` | What it matches and what it does (see the field list below) |
| `commitment` | Bill, subscription or instalment (see the field list below) |
| `recommendation` | `skill`, £ impact, evidence, status (new, accepted, dismissed, snoozed, done), realised saving, and a fingerprint so a dismissed item is never repeated |
| `proposal` | A change to user-owned data that the agent wants made, waiting for the user to accept or reject |
| `knowledge_version` | A counter incremented on any change to a rule, fact, merchant, category, purpose or profile entry |

**`understanding` fields:**
- merchant, category, purpose and who it was for;
- `status`, moving `unknown → guessed → inferred → confirmed`;
- confidence;
- `decided_by`: rule, memory, research, llm, review or human;
- evidence (JSON);
- `knowledge_version`;
- `reviewed_at`.

**`fact` fields:**
- subject (an entity reference);
- topic tags;
- a statement plus a structured value;
- source: user, inferred or research;
- confidence;
- `valid_from` and `valid_to`;
- `review_after`;
- `supersedes_id`;
- evidence references.

**`rule` fields:**
- **Match:**
  - merchant;
  - description pattern;
  - amount range;
  - account;
  - direction;
  - date range;
  - person.
- **Action:** set category, purpose or who, mark as transfer, or ignore.
- **Bookkeeping:** source (user, learned or seed), confirmed flag, scope, hit
  count, and the feedback that created it.

**`commitment` fields:**
- kind: bill, subscription or instalment;
- merchant and category;
- cadence: weekly, fortnightly, 4-weekly, monthly, quarterly or annual;
- expected amount and date;
- next due date;
- annual cost;
- price history;
- status: active, lapsed or ended.

## 8. Agent team and the learning loop

### 8.1 Decision: fixed workflow for analysis, a free agent only in chat

- **The analysis graph is a fixed workflow.** Code decides the routing, not
  the LLM. Retry steps are bounded, so a runaway loop is impossible by
  construction.
- **The coach is the only open-ended agent,** and it runs under the guards in
  §10.1.

### 8.2 Specialists

| Stage | Specialist | Kind | Responsibility |
|---|---|---|---|
| Understand | **Categoriser** | Code first, then LLM | Applies rules and confirmed memory in code; the remaining rows go to the LLM in batches sized to the context budget; low-confidence rows go to `review`. Assigns the *deepest* category level it's confident in. When a category gets crowded it proposes sub-categories and re-files existing records into them (logged, undoable) |
| | **Transfer matcher** | Code | Pairs internal transfers and card repayments across accounts: opposite sign, equal amount, within 3 days |
| | **Commitments** | Code + LLM labels | Cadence detection from inter-payment gaps across all history (weekly to annual); price rises; lapsed or missed payments; duplicate services; free-trial conversions. The LLM only labels merchant type (bill, subscription or instalment) |
| Doubt | **Backlog sweep** | Code | Each run re-queues understanding rows where any of these hold: status is unknown or guessed; confidence < 0.7; or the row's `knowledge_version` is older than a change that touches its merchant, category, rule scope or purpose. Ordered by £ descending, capped by budget |
| Research | **Researcher** | Thinking model + tools | Identifies unknown merchants. Tools in order: `uk-merchants` pack lookup → Companies House lookup (name → company → SIC code) → web search → page fetch. It reasons over the evidence and writes a `merchant` with confidence. **It sends merchant text only, never amounts or personal details.** It needs the research toggle (§12.5) |
| Ask | **Question planner** | Code + LLM wording | Ranks unknowns by £ × uncertainty, picks the question kind, enforces the question budget and topic ledger (§10.2) |
| | **Purpose analyst** | LLM + facts | Runs **5 whys** on large items and commitments (see the example below). Each answer becomes a `fact` and a `purpose` link, reused everywhere (*Car* explains fuel, insurance, parking and road tax) |
| | **Life-events watcher** | Code + LLM wording | Spots changes in circumstances: wages stopping or starting; nursery or baby spending; a new rent payee; pension income; a child crossing an age threshold (3: funded hours; 12: Tax-Free Childcare ends; 16/18: Child Benefit). It asks to confirm, files a dated profile `proposal`, and triggers the affected skills |
| Learn | **Learner** | Code + LLM | A 👎 plus an edit becomes a candidate rule, shown as "Apply to *N* similar past transactions?" with a preview. Accepted rules are confirmed. Corrections become worked examples retrieved for similar merchants, and cases in the user's personal eval set |
| Revise | **Linter** | Code + LLM | Finds the same merchant in different categories, rows contradicting a rule or fact, stale low-confidence rows, unpaired transfers and wrongly categorised commitments. If a confirmed rule covers it, code fixes it; otherwise it goes to the review queue |
| Advise | **Skill runner**, **Report writer**, **Coach** | §9, §13 | Recommendations, reports, conversation |

**5-whys example:**
1. "£1,450 to Acme Lettings" → rent.
2. *Why above the LS6 typical for a 2-bed?* → "it includes bills and parking".
3. *Why parking?* → "we have two cars".
4. → linked to the *Car* purpose.

The analyst stops at a root driver, at depth 5, or when the user says
"that's just how it is".

### 8.3 Flow

```
ingest → Categoriser → Transfer matcher → Backlog sweep → Researcher
       → Send fan-out: Commitments ∥ Purpose analyst ∥ Life-events ∥ Skill runner
       → Question planner → Report refresh
```

- **Triggers:**
  - a new statement;
  - an answer;
  - feedback;
  - a rule or fact change;
  - a schedule (daily light run).
- **Answers and feedback** re-run only the affected specialists, with a 30 s
  debounce. Repeat triggers for the same scope merge into one pending job.

### 8.4 Visible improvement

The "How well Tuppence knows you" page charts four things over time:
- the share categorised automatically;
- the correction rate;
- the £ share still unknown;
- questions per statement.

## 9. Advisor skills

### 9.1 Skill format

`skills/<name>/` contains:
- `SKILL.md`, in Agent Skills convention, with frontmatter fields `name`,
  `description` and `triggers`, and these sections:
  - required facts;
  - procedure;
  - output schema;
  - wording guardrails;
  - signposts.
- `calc.py`: every number, deterministic and unit-tested.
- `tests/`.

How skills load:
- Only each skill's name and description sit in the coach's context.
- The full `SKILL.md` loads when the skill is used.
- Users can add skills from a user skills folder, and enable or disable any
  skill.

### 9.2 Iteration 1 skills

1. **Cash-flow health:** surplus, savings rate, safe to spend until payday.
2. **Emergency fund:** 3–6 months of essential spending; the gap; where it's
   held.
3. **Debt plan:**
   - UK priority debts first (rent or mortgage, council tax, energy, child
     maintenance);
   - interest per month;
   - avalanche vs snowball schedule;
   - 0% promo expiry;
   - balance-transfer and consolidation maths;
   - overdraft cost;
   - distress signals → signpost StepChange or MoneyHelper.
4. **Bills and subscriptions:** duplicates, price rises, broadband or mobile
   out of contract, energy vs the price cap, insurance renewal reminders.
5. **Housing cost:**
   - rent vs ONS local figures by bedrooms;
   - mortgage rate vs the Bank of England quoted rates, and the remortgage
     window;
   - council tax band and single-person discount.
6. **Tax check:**
   - Marriage Allowance;
   - the Child Benefit charge (£60k–£80k);
   - the personal-allowance taper (£100k–£125,140), with a salary-sacrifice
     idea;
   - Personal Savings Allowance;
   - work-from-home, uniform and professional-fee reliefs;
   - higher-rate pension relief;
   - Self Assessment triggers (trading or property income above £1,000);
   - student loan plan;
   - bonus effects.
7. **Benefits and support screen:**
   - **Covers:** Child Benefit, Tax-Free Childcare, funded childcare hours (by
     nation), Universal Credit, Help to Save, Healthy Start, Warm Home
     Discount, social tariffs, Council Tax Reduction, Pension Credit.
   - **Results** are *likely*, *possibly* or *unlikely*, each with the official
     calculator link. Never stated as definitive.
8. **Savings and ISAs:**
   - cash rates vs market rates (§11.4);
   - ISA, LISA and JISA allowances;
   - LISA for a first-home goal, with the withdrawal-penalty warning.
9. **Pension check:** employer match being missed, tax relief, state pension
   forecast link.
10. **Protection gap:** dependants but no life or income cover payments seen.
    Explains the gap; never names a product.
11. **Goal planner:** progress, projected date, extra per month needed.
12. **Car finance** (§11.6).
13. **Life-event playbooks:** new baby, job loss, new job or pay rise,
    separation, moving home, retirement, a child starting school or leaving
    home. Each gives a checklist, profile proposals, and re-runs the relevant
    skills.
14. **What-if** (coach): deterministic projections for questions like "if I
    lose my job", "an extra £200/month on card X", "another child", "move to
    a 3-bed".

### 9.3 Output guards

- **Number guard** (ported from v3): every £ and % figure in LLM text must
  match a computed fact (money within £1, percentages within 0.5). If it
  doesn't, one retry is made, after which the text is withheld.
- **Wording guard:**
  - blocks imperative product recommendations ("you should buy/switch to
    X");
  - requires guidance phrasing;
  - attaches the skill's signposts.
- **Recommendation lifecycle:**
  - new → accepted / dismissed / snoozed → done;
  - a done item records the realised saving, which feeds "Tuppence has helped
    you save £X a year".

## 10. Guards, memory correctness and context sizing

### 10.1 Runaway-loop guards

| Scope | Guard |
|---|---|
| Every graph | LangGraph `recursion_limit`; no cycles except bounded retry steps (max 3) |
| Analysis run | Caps on LLM calls, tokens, £ and wall-clock time, per run and per specialist. When a cap is hit: stop cleanly and mark remaining items `deferred` to the next run |
| Researcher | ≤20 merchants per run, ≤5 tool calls per merchant, ≤2 page fetches per merchant; results cached in `merchant` |
| Purpose analyst | Depth ≤5; ≤3 open "why" conversations at once |
| Coach (per turn) | See the list below |
| Jobs | One pending analysis per scope; 30 s debounce; one analysis at a time; imports queue |
| Global | Monthly £ cap; circuit breakers (§4.5) |

**Coach limits, per turn:**
- ≤8 tool calls;
- a time limit: 60 s for cloud models, 180 s for local ones, configurable;
- a £ and token cap;
- an identical repeated tool call returns the cached result with a note;
- per-tool limits;
- every tool call is answered, even when the cap stops execution part-way
  through a batch;
- the user can cancel.

### 10.2 Memory correctness

- **Order of authority:** confirmed by the user > rule > merchant confirmed
  by research > inferred memory > LLM.
- **Confirmed data is never overwritten by an agent.** If new evidence
  contradicts it, the agent raises a `verify` question.
- **Facts are never silently replaced.** A new fact supersedes an old one
  through `supersedes_id`, and a conflict becomes a question.
- **The topic ledger:**
  - a `topic_key` that has been answered is not asked again until its fact's
    `review_after` date, or until the underlying numbers move materially
    (default ±10%);
  - it then comes back as a "Still true?" check;
  - at most 5 questions are shown at once.
- **Provenance:** every write records `decided_by`/source and
  `knowledge_version`, and history tables make it undoable.
- **Property tests** guarantee:
  - a confirmed row never changes without user action;
  - an answered topic is never re-asked within its cooldown.

### 10.3 Context sizing

- **A `ContextBudget` per call,** worked out from the catalogue's context
  window. It reserves 25% for output and caps input at about 60%. Within
  that it allocates, in priority order:
  1. instructions and the skill playbook;
  2. topic-filtered facts (by the tags of the items in the batch);
  3. merchant memory for the batch's merchants only;
  4. the relevant category subtree;
  5. data rows, with the batch size computed to fit.
- **Coach:**
  - a rolling summary of older turns plus the last 8 turns;
  - tool results capped at 50 rows, with a paging handle;
  - summary tools preferred over raw rows.
- **Run summaries:** each analysis run writes a compact "what changed"
  summary for the coach.
- **Small-model mode** (from the Frugal preset): smaller batches, shorter
  prompts, more done in code.

## 11. Cash, configuration, live data, income and wealth

### 11.1 Cash and informal debts

- **Each person has a cash wallet account.** ATM withdrawals become transfers
  into it.
- **Capture through the coach.** For example: "Paid the window cleaner £20
  cash", "Borrowed £500 from my brother", "Paid him back £100". The coach
  shows a confirm card and writes only after ✓.
- **A quick-add form** does the same without chat.
- **Informal debts are a ledger:** counterparty, direction, entries, balance
  and optional due date. They're included in total debt.
- **Optional cash nudge, budget-limited:** "You withdrew £200 this month and
  £60 is accounted for. Anything big?"

### 11.2 Configurable agents

- **Each agent has a manifest,** `config/agents/<name>.toml`, covering:
  - enabled;
  - task and model chain;
  - budgets;
  - thresholds: confidence, review and £ significance;
  - question limits and cooldowns;
  - triggers and schedule;
  - allowed tools;
  - tone;
  - prompt template path.
- **Precedence:** repo defaults < user `config/*.toml` < Settings › Agents.
  Settings are validated by Pydantic, can be reset per field, and can be
  exported.
- **Extension points:**
  - user skills folder;
  - user importer layouts;
  - user market-data providers;
  - rules import and export;
  - prompt template overrides.
- **Presets:**
  - **Frugal:** local, minimal LLM, small-model mode.
  - **Balanced:** the default.
  - **Thorough:** cloud, research on, larger budgets.

### 11.3 Bonuses and variable pay

- **Variable components:** income sources can have bonus, overtime and
  commission components.
- **Bonus detection:** a payroll credit well above that source's usual amount
  triggers "Was this a bonus?". The answer becomes a fact, and income
  analysis separates regular from variable pay.
- **Tax effects:** the tax check skill covers the effects of a bonus (§9.2).

### 11.4 Live market data

All feeds sit behind a pluggable `MarketDataProvider` interface:
- results are cached in SQLite with a timestamp;
- every displayed figure shows its source and date;
- when offline, the last cached value is used, labelled "as of …".

| Feed | Source (verified 2026-10-07) | Key | Use |
|---|---|---|---|
| Bank Rate, quoted mortgage rates by term and LTV, savings rates | Bank of England database CSV export | None | Housing cost, savings, debt plan |
| CPI / CPIH | ONS timeseries API | None | Real-terms spend, inflation-adjusted goals, "bills rose faster than inflation" |
| Exchange rates | Frankfurter (ECB reference rates) | None | Foreign spend, foreign-currency holdings |
| Shares and ETFs | Yahoo chart endpoint (default, unofficial); Alpha Vantage, Twelve Data or Finnhub (free keys); manual prices | Optional | Holdings value |
| Crypto | CoinGecko | None | Crypto holdings |

- **Pence conversion:** London Stock Exchange prices quoted in pence (GBp)
  are converted to GBP.
- **The "Live market data" setting is on by default.**
  - The macro feeds send only series codes.
  - Price lookups reveal held tickers to the provider, and the UI says so.
  - With the setting off, prices are entered manually.

### 11.5 Holdings and net worth

- **`holding` fields:**
  - wrapper: ISA, LISA, SIPP/pension, GIA, workplace scheme or crypto;
  - instrument (ticker or ISIN);
  - quantity and cost basis;
  - live value and unrealised gain;
  - dividends, detected from statements.
- **Allowance tracking:**
  - ISA subscription;
  - dividend allowance;
  - capital-gains annual exempt amount.
  
  Thresholds come from the `uk-tax` pack.
- **Net worth** = cash + holdings + pensions (manual value) + property
  (optional manual estimate) − debts.
- **Iteration 1:** manual and CSV entry.
- **Iteration 2:**
  - broker statement importers;
  - capital-gains and dividend tax calculations;
  - RSU and Sharesave vesting schedules;
  - rental income.

### 11.6 Car finance skill

- **Calculations for PCP, HP and personal loans:**
  - APR;
  - total amount payable;
  - balloon/GFV;
  - mileage excess cost;
  - equity, using a car value the user enters;
  - estimated settlement figure (clearly labelled an estimate);
  - PCP vs HP vs loan comparison.
- **Voluntary-termination date:** when 50% of the total amount payable has
  been paid.
- **Commission redress check.** Applies to agreements entered between
  2007-04-06 and 2024-11-01 where a broker was used.
  - It explains the FCA motor finance redress scheme: rules in force from
    2026-03-31, with lenders contacting eligible customers.
  - It says how to check for free without a claims company.
  - Scheme facts live in the `uk-tax` pack so they can be updated.

## 12. UK data packs, entitlement engine and research

### 12.1 Pack format and pipeline

- **Format:**
  - a pack is a directory of JSON and CSV files plus `manifest.json`;
  - the manifest records id, version, publish date, source URLs, licence
    (mostly Open Government Licence v3) and file checksums;
  - each pack is signed with Ed25519, with the public key built into the app.
- **Build:**
  - a scheduled GitHub Action fetches the official sources, transforms them
    and tests them;
  - it then opens a PR for human review;
  - once merged, the pack is published as a GitHub release asset.
- **Install:**
  - a baseline set ships with every installer and image;
  - the app checks for updates daily with an anonymous GET (no telemetry);
  - it verifies the signature and installs atomically, keeping the previous
    version for rollback.

### 12.2 Iteration 1 packs

| Pack | Contents |
|---|---|
| `uk-tax-<year>` | Tax data (see the list below) |
| `uk-benefits` | Declarative screening rules (§12.3) |
| `uk-housing` | ONS private rents by local authority and bedrooms; postcode district → local authority lookup; average council tax by local authority |
| `uk-energy` | Ofgem price cap unit rates and standing charges by region and period |
| `uk-merchants` | Statement text patterns → canonical merchant and category; community-contributed |
| `uk-banks` | Importer layouts (CSV signatures and column maps) and PDF markers. New banks ship without an app release |
| `model-catalogue` | LLM context windows, capabilities and prices |
| `uk-spending-benchmarks` | ONS Family Spending by household type and region (for 10x idea #11, M6) |

**`uk-tax-<year>` contents:**
- income tax bands per nation (Scotland separate);
- National Insurance;
- personal allowance and its taper;
- dividend, savings, trading and property allowances;
- ISA, LISA and JISA limits;
- capital-gains annual exempt amount;
- student loan thresholds;
- Child Benefit rates and the Child Benefit charge;
- Marriage Allowance;
- bank-holiday calendars per nation;
- car finance redress scheme facts.

### 12.3 Entitlement engine

- **Rule format:** YAML with these fields:
  - `id`, `name`, `nations`;
  - `conditions`, in a small, safe expression language over profile and fact
    fields (no `eval`);
  - `estimate`, an optional value formula;
  - `links`, the official sources;
  - `last_reviewed`.
- **Evaluation:** against the profile as of today plus facts.
- **Result:**
  - *likely*, *possibly* or *unlikely*;
  - an estimated value;
  - a human-readable trace.
- **Missing inputs:** a result becomes "possibly — tell me X to check" and
  goes to the question queue.

### 12.4 Data-source decision

- **The default is data packs.** Only anonymous pack downloads and anonymous
  market-data calls leave the machine.
- **Live web research is opt-in** (§12.5).

### 12.5 Opt-in research lookups

- **One setting, "Research lookups,"** covers Companies House, web search and
  page fetch. Onboarding asks about it, presenting it as recommended on.
- **Search providers you can plug in:**
  - SearXNG (self-hosted);
  - Brave Search API (free tier);
  - DuckDuckGo HTML (no key, best effort);
  - Firecrawl (key).
- **Page fetch** uses `httpx` with main-text extraction.
- **Companies House** uses the official API with a free key the user
  registers.
- **What queries contain:** merchant text or area-level terms only. Never
  amounts, account details or household names.
- **Logging:** every query is in the privacy log.

## 13. Pages and reports

| Page | Purpose |
|---|---|
| **Home** | Safe to spend until payday, monthly surplus, bills due in 7 days, net worth, top 3 recommendations, ≤5 open questions, the learning meter |
| **Inbox** | One queue of questions, proposals and recommendations, ranked by £ |
| **Spending** | Treemap drill-down with breadcrumbs, from level 1 down to transactions. Filters (unknown, guessed, account, person, period). Inline edit with 👍/👎. Bulk re-categorise. A **"Why?" panel** showing the evidence and decision path |
| **Why your money goes** | The purpose view, e.g. *Car* £640/mo all-in, with each 5-whys chain |
| **Commitments** | Calendar; annual cost; price-rise, lapsed and duplicate flags |
| **Debts** | Cards, loans, car finance and informal debts; interest per month; payoff plan |
| **Goals and wealth** | Goals, holdings with live values, net worth over time |
| **Reports** | Report contents (see the list below) |
| **Coach** | Streaming chat with confirm cards, inline charts and tables, and a visible "Using: \<skill\>" label |
| **Statements** | Multi-file drag and drop, status per file, needs-review fix-up |
| **Settings** | Household timeline; accounts; AI connections and task routing; agents; privacy (local only, pseudonymise, research, live data, privacy log); data packs; usage and cost; backup, export and import; users (Docker) |

**Reports:**
- monthly, or on demand;
- sections:
  - summary;
  - where it went;
  - commitment changes;
  - debt and interest;
  - goals and net worth;
  - UK checks;
  - actions;
- every number links to its source;
- exports to HTML or PDF, generated locally.

**Cross-cutting UI requirements:**
- responsive down to phone width;
- WCAG 2.2 AA;
- light and dark themes;
- plain English;
- UK formats: £ and DD/MM/YYYY.

## 14. Failure handling, security and testing

### 14.1 Failure handling

- **Works without AI.**
  - Importers, rules, memory, commitments, calculators, budgets and UK
    checks are all code.
  - When every LLM in a chain fails, AI items are marked *awaiting AI* and a
    banner shows.
- **Statement failures:** parse or check failures become `needs_review` with
  a fix-up screen.
- **Packs or feeds unavailable:** the last cached values are used, labelled
  "as of …".
- **Crash or restart:** runs resume from checkpoints. Results are written in
  one transaction, so nothing is ever half-written.
- **Backups:**
  - a backup is taken before every migration;
  - a daily backup, with 7 kept;
  - a full export/import archive (database + files) moves data between
    desktop and Docker.
- **Concurrent edits:** a version conflict returns 409, and the UI asks the
  user to reload.

### 14.2 Security

- **Desktop:** loopback and a per-launch token; no remote access.
- **Docker:**
  - first-run admin setup;
  - Argon2id password hashing;
  - session cookies that are `HttpOnly`, `SameSite=Strict`, and `Secure`
    behind HTTPS;
  - CSRF tokens;
  - login rate limiting;
  - CSP and security headers.
  
  The docs cover Caddy for HTTPS and Tailscale for remote access, and say
  never to port-forward.
- **Uploads:**
  - size and type checks;
  - PDF and OCR parsing in a subprocess with timeouts;
  - protection against zip bombs.
- **Prompt injection:** statement text and web pages are untrusted.
  - All model output is schema-validated.
  - Background agents only get read-only tools plus proposals.
  - The Researcher only gets search, fetch and lookups.
  - Coach writes need a confirm card.
- **Supply chain:**
  - `uv` lock file with hashes;
  - minimal dependencies;
  - Renovate;
  - Sigstore/cosign signatures on wheels and images;
  - an SBOM per release.
- **Personal-data guard:** pre-commit and CI (§2).

### 14.3 Testing

- **pytest:**
  - calculators, with official worked examples;
  - importers, each against a fixture;
  - check;
  - entitlement rules;
  - API routes.
- **Property tests (Hypothesis):** the memory guarantees in §10.2.
- **Graph tests with a scripted fake LLM,** including hostile ones that:
  - call tools forever;
  - return garbage;
  - try to overwrite confirmed data;
  - exceed budgets.
- **Synthetic household generator:** 12–24 months of history with life
  events, for testing commitments, 5 whys, life events and learning curves.
- **Playwright end-to-end:** onboarding → upload → identify → questions → 👎
  → learned rule → report.
- **Eval harness** (§6.3).
- **Packaging smoke tests:** launch the frozen app on each OS in CI and hit
  `/health`.
- **CI (GitHub Actions):** ruff, pyright, pytest, vitest and the denylist
  guard. Release tags build multi-arch images, desktop artifacts and data
  packs.

## 15. Iteration 2+ roadmap

- **Open Banking sync:** opt-in only, if a viable free provider exists.
- **Phone upload, and "chat to set up" onboarding.**
- **Broker importers, capital-gains and dividend tax, RSU and Sharesave
  schedules, rental income.**
- **10x ideas,** full list in the private go-to-market doc:
  1. "Find my money" web check (M6)
  2. zero-setup bundled local model (M7)
  3. debt-distress mode with a Standard Financial Statement export, subject
     to its licence
  4. life-event entry points
  5. Making Tax Digital for Income Tax bridging for sole traders and
     landlords (iteration 3)
  6. community knowledge flywheel: opt-in sharing of merchant and bank
     layouts only
  7. couples' co-pilot
  8. proactive nudges via ntfy, Apprise or desktop (M6)
  9. channels: MCP connector, Telegram/WhatsApp bridge, phone capture
  10. inclusion: plain-English mode, Welsh, voice via local Whisper,
      community languages
  11. "people like you" benchmarks (M6)
  12. workplace edition
  13. education and first-payslip mode

## 16. Delivery

| Milestone | Scope | Exit |
|---|---|---|
| **M0 Bootstrap** | Repo, licence, README with privacy claim, CONTRIBUTING/SECURITY/PRIVACY, CI with denylist guard, `uv` project, FastAPI + Svelte skeletons, **hello-world in all three shells** | Desktop builds launch on Windows, macOS and Linux; Docker image runs; `uvx` runs |
| **M1 Foundation** | Store and migrations, profile timeline core, config and manifests, LLM layer (§4), auth modes, jobs, privacy log | Connection test passes against Ollama, OpenAI-compatible, Anthropic and Gemini; Local only proven by test |
| **M2 Onboarding** | Wizard, timeline editor, accounts, incomes, pay rules, debts, goals | End-to-end onboarding test |
| **M3 Ingestion** | §6 complete, eval harness v1 | 12 layouts + OFX/QIF/CAMT + PDF/scan/screenshot fixtures pass |
| **M4 Understanding** | Knowledge model, categoriser, transfer matcher, commitments, backlog sweep, Spending and Commitments pages | **Developer preview** (Docker, source, unsigned desktop); accuracy baseline recorded |
| **M5 Learning loop** | Researcher, question planner, purpose analyst, life-events watcher, learner, linter, feedback UI, coach + guards | Learning and guard tests from §1.2 pass |
| **M6 Advisor** | Skills framework + §9.2 skills, data-pack pipeline and signing, live feeds, holdings, cash and informal debts, car finance, reports, nudges, "Find my money" web check | All skill tests pass; packs signed and verified |
| **M7 Release** | Security review, regulatory wording review, signed installers (macOS notarisation, Windows signing), GHCR images, docs site, bundled local model option | **Public v1.0** |

- **This spec covers M0 and M1 in enough detail for an implementation plan.**
  M2–M7 each get a short spec when they start.
- **The repo starts locally with docs only.** It is pushed to the public
  GitHub repo once the M0/M1 plan is approved.
- **Project tracking (Jira, Confluence) and the go-to-market strategy are kept
  outside this public repo.**

## 17. Decisions log

| # | Decision | Why |
|---|---|---|
| D1 | Separate product from the predecessor; no migration | Free of household-specific concepts |
| D2 | Iteration 1 includes the UK advisor | The "finds you £X" headline is the launch hook |
| D3 | Data packs by default, live web research opt-in | Anonymous by default; freshness when chosen |
| D4 | One FastAPI process; Docker + pywebview/PyInstaller + uvx | Python-only toolchain, fastest to all three targets; the desktop shell can move to Tauri later without app changes |
| D5 | Name: Tuppence | Distinctly British, not tied to a single goal |
| D6 | AGPL-3.0 | Stops closed hosted forks |
| D7 | Svelte 5 + Vite, prebuilt into the wheel | Rich UI (wizard, streaming chat, big tables, charts); users never need Node |
| D8 | Own thin LLM adapters, no LiteLLM | Small supply chain for an app holding every key |
| D9 | Pseudonymisation is a toggle, default off; account identification is local | Keeps the agent flow intact and is honest about what is sent |
| D10 | Fixed workflow for analysis; only the coach is free | Runaway loops impossible by construction |
| D11 | Knowledge in our own tables behind `KnowledgeStore`; LangGraph Store not used in v1 | One source of truth that can be queried, versioned and tested |
| D12 | Fixed importers for structured formats; LLM only for PDFs, OCR text and unknown CSV mappings | Speed, cost, works with small local models |
| D13 | RapidOCR + optional vision model; no Textract | Local by default; no AWS dependency |
| D14 | Live market data on by default (anonymous); tickers disclosed | Useful and low sensitivity |
| D15 | Holdings: manual and CSV in iteration 1; broker importers and capital-gains/dividend tax in iteration 2 | Scope control |
