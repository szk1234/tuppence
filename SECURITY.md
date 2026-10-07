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
