# Privacy

Tuppence is local-first. There is no Tuppence account, and no telemetry.

> **Status: pre-alpha.** Controls such as the Local only switch, the privacy log and the pseudonymise toggle are being built; some are not in the code yet.

## What is stored

Everything lives in your data folder: a SQLite database and the statement files
you upload. Nothing is stored anywhere else.

## What leaves your machine

Only the following, and nothing else:

- Calls to the LLM provider you configure. With a local model these never leave
  your machine; with a cloud model, statement text goes to the provider you chose.
- Optional research lookups, which send merchant names only.
- Optional downloads of anonymous data packs and market data.

## Controls

- **Local only switch:** blocks every outbound call except to a local model.
- **Privacy log:** every call that leaves your machine is logged, so you can see
  exactly what was sent and where.
- **Pseudonymise toggle:** replaces names and account details before text is
  sent to a cloud model. Off by default.

## Deleting everything

Delete the data folder. That removes the database, uploaded files and logs.
