# Security policy

Report vulnerabilities privately via GitHub Security Advisories (Security →
Report a vulnerability). Please don't open public issues for security problems.

**Scope:** anything that could expose a user's financial data, credentials or
API keys, or let someone on the network read or change data.

## Threat model

The desktop app binds to loopback only. The Docker image is intended for a
trusted home network behind a VPN and must never be port-forwarded to the
internet.
