# Privacy

Tuppence is local-first. There is no Tuppence account, and no telemetry.

> **Status: pre-alpha.** The controls below (Local only, the cloud notice, the privacy
> log and pseudonymise) are in the code. Statement import and the agents that use them
> are still being built.

## What is stored, and where

**Your data folder** holds almost everything: the SQLite database (`tuppence.db`), the
statement files you upload, automatic backups, your agent settings and, in some setups,
`secret.key` (see below). It is `TUPPENCE_DATA_DIR` if you set it; otherwise:

- macOS: `~/Library/Application Support/Tuppence`
- Windows: `%LOCALAPPDATA%\Tuppence`
- Linux: `~/.local/share/Tuppence`
- Docker: the `tuppence-data` volume

**Saved AI keys and custom header values** are kept separately from your other data:

- **Desktop app and `uvx`/`pipx`:** in your operating system's keychain when one is
  available (Keychain on macOS, Credential Manager on Windows, the Secret Service on
  Linux). **These entries live outside the data folder.**
- **Docker, or a computer with no keychain:** encrypted in the database. The key that
  decrypts them is `secret.key` in the data folder, or the file `TUPPENCE_SECRET_KEY_FILE`
  points to (see [SECURITY.md](SECURITY.md)).

The database itself only ever holds a reference to each key, never the key. Keys are not
shown again after you save them, and never appear in the privacy log or in error messages.

## What leaves your machine

Only the following, and nothing else:

- Calls to the AI provider you configure. With a local model these never leave your
  machine or network; with a cloud model, statement text goes to the provider you chose.
- Optional research lookups, which send merchant names only.
- Optional downloads of anonymous data packs and market data.

## Controls

### Local only (off by default)

When it is on, Tuppence refuses every AI call and research lookup to anything outside your
own computer or network. The check is made in the HTTP layer, before anything is sent, and
every refusal is logged as `blocked`.

- **What counts as local:** this computer (`localhost`, `127.0.0.0/8`, `::1`), private
  network addresses (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, IPv6 `fc00::/7`),
  link-local addresses (`169.254.0.0/16`, `fe80::/10`) and `100.64.0.0/10` (used by
  Tailscale). A host name counts as local only if every address it resolves to is local;
  a name that doesn't resolve is not local.
- **Cloud presets** (OpenAI, Anthropic, Gemini and the others) always count as cloud, even
  if you point one at a local address.
- **A blocked cloud connection is not even looked up:** Tuppence refuses it by how the
  connection is set up, before any DNS query for its name.
- **What it does not cover:** live market data and data-pack updates. They have their own
  switches on the Privacy page.

Some protections apply whether or not Local only is on:

- **Per-task pins:** in Settings › AI you can keep any task on this device. That task then
  only ever uses local models.
- **Local stays local:** a connection set up as local is used only while every address its
  host resolves to is still local. If the name starts pointing elsewhere, Tuppence refuses
  the call (and logs it) until you press Test on that connection.
- **Never cloud metadata:** cloud instance-metadata addresses (such as `169.254.169.254`
  and `metadata.google.internal`) are always refused, whatever they are used for.
- Redirects are never followed, and proxy settings from the environment are ignored.

### Cloud notice

Before a cloud connection is used for the first time, Tuppence asks you to confirm that
the provider will see the statement text it sends. It asks again if you change that
connection to a different host.

### Privacy log

Every outbound call, and every call that was stopped, is logged on the Privacy page. The
log records **details about each call, not its content**:

- time, purpose (AI, research, market data or data packs) and task;
- which connection, the destination host and the path (never the query string);
- bytes sent and received, and the HTTP status;
- how many values pseudonymise swapped for stand-ins;
- the outcome (sent, blocked or error) and, for an error, only its type.

It never stores what was sent or received, API keys or header values. Entries older than
13 months are deleted automatically. The AI usage ledger behind the Usage page (time, task,
model, token counts, cost, and an error type for failed calls) is kept for 13 months too.

### Pseudonymise (off by default)

Before a call to a cloud model, Tuppence can swap sensitive values for consistent
stand-ins and swap them back in the reply, on your machine:

- household members' names become roles such as "Adult A";
- the other names you list on the Privacy page become "Person 1" and so on;
- account and card numbers, sort codes, IBANs, postcodes, email addresses and phone
  numbers become tokens such as "ACCT_2".

Merchant names, amounts and dates are never masked, because the AI needs them.

**This is best-effort.** It works by recognising patterns, so some formats can slip
through, for example numbers written out in words, a sort code and account number on
different lines, or a one-word name written in lower case in free text. It can also hide a
little too much, such as a date written right next to an eight-digit number. Don't rely on
it alone for text you wouldn't want the provider to see: use a local model for that.

## Deleting everything

1. In Settings › AI, choose **Forget saved AI keys**. This removes every saved key and
   header value, including the entries in your keychain, which deleting the data folder
   would not remove. If your keychain is locked, Tuppence tells you how many entries it
   couldn't remove: unlock it and choose Forget again, or delete the entries named
   `Tuppence` in your keychain app (Keychain Access on macOS, Credential Manager on
   Windows, or your desktop's passwords and keys app on Linux).
2. Quit Tuppence and delete the data folder. That removes the database, uploaded files,
   backups, logs and `secret.key`. With Docker, remove the `tuppence-data` volume.
3. If you set `TUPPENCE_SECRET_KEY_FILE` (or a Docker secret), delete that file too.
