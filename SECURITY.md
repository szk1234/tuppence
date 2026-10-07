# Security policy

Report vulnerabilities privately via GitHub Security Advisories (Security →
Report a vulnerability). Please don't open public issues for security problems.

**Scope:** anything that could expose a user's financial data, credentials or
API keys, or let someone on the network read or change data.

## Threat model

The desktop app binds to loopback only. The Docker image is intended for a
trusted home network behind a VPN and must never be port-forwarded to the
internet.

### Server (Docker) mode settings

- `TUPPENCE_ALLOWED_HOSTS`: set it to the hostnames you use to reach Tuppence
  (comma-separated, e.g. `money.home.lan`), so DNS-rebinding attacks are
  blocked. Loopback names (`127.0.0.1`, `localhost`, `[::1]`) always stay
  allowed, so the container health check keeps working.
- `TUPPENCE_SECURE_COOKIES=1` (or `tuppence serve --secure-cookies`): marks the
  sign-in cookie `Secure`, so browsers only send it over HTTPS. Turn it on when
  you reach Tuppence through an HTTPS reverse proxy (e.g. Caddy). Leave it off
  for plain `http://` on your LAN: browsers drop `Secure` cookies over HTTP and
  you couldn't sign in.

Both are shown, commented out, in `compose.yaml`.

### Saved AI keys and the secret key

On the desktop app and with `uvx`/`pipx`, saved AI keys and custom header values go into
the operating system's keychain when one is available. Otherwise, and always in server
(Docker) mode, they are encrypted in the database with a Fernet key:

- By default Tuppence creates that key as `secret.key` (mode `0600`) in the data folder.
  That keeps it in the same place, and the same Docker volume, as the database and its
  backups: anyone with a copy of the folder can decrypt the saved keys.
- To keep them apart, put the key somewhere else and set `TUPPENCE_SECRET_KEY_FILE` to
  its path. With Docker, use a secret: create the key once with
  `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > tuppence_secret_key.txt`,
  make it readable only by the container's user (`sudo chown 10001 tuppence_secret_key.txt
  && chmod 400 tuppence_secret_key.txt`), and uncomment the `secrets` lines and
  `TUPPENCE_SECRET_KEY_FILE` in `compose.yaml`. If you start using a new key file, keys
  saved under the old one can't be read: choose "Forget saved AI keys" in Settings › AI
  and enter them again.
- If `TUPPENCE_SECRET_KEY_FILE` is set but the file is missing, empty or not a valid key,
  Tuppence refuses to start and names the file. If the default `secret.key` goes missing,
  Tuppence still starts and asks you to re-enter your keys.

API keys must be plain printable characters; Tuppence never shows a saved key again, and
error messages, the privacy log and the usage ledger record only the type of a failure,
never its text, so a key echoed in a library error can't end up stored.

### Outbound connections

Every outbound request goes through one guarded HTTP client
(`src/tuppence/net/client.py`), and a test fails if any other module builds its own. The
guard resolves each host once and connects to the address it checked, refuses cloud
metadata addresses for every purpose, enforces Local only and per-task pins, refuses a
connection set up as local whose host no longer resolves inside your network, never
follows redirects, ignores proxy settings from the environment, and holds every request
to a wall-clock time limit (a server can't keep a call open by sending bytes slowly).

### Data folder

Tuppence creates its data folder readable only by your user (`0700` on Linux
and macOS). If you point `TUPPENCE_DATA_DIR` or `--data-dir` at a folder that
already exists, its permissions are left as they are, so check them yourself.
