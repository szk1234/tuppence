# Run Tuppence with Docker

For home servers and NAS boxes. Keep Tuppence on your home network; for remote access use a
VPN such as Tailscale, and never forward a port to it from the internet.

```bash
mkdir tuppence && cd tuppence
curl -fsSLO https://raw.githubusercontent.com/szk1234/tuppence/v0.2.0-dev.1/compose.yaml
TUPPENCE_VERSION=v0.2.0-dev.1 docker compose up -d
```

Open `http://<server>:8040` and create the admin account (there are no default passwords).
Data is kept in the `tuppence-data` volume.

- **Local AI in the same compose file:** `TUPPENCE_VERSION=v0.2.0-dev.1 docker compose --profile ollama up -d`,
  then add the `ollama` connection in Settings › AI (base URL `http://ollama:11434`).
- **Images:** `ghcr.io/szk1234/tuppence:v0.2.0-dev.1` for amd64 and arm64. Preview tags never
  move `latest`.
- **Updating:** change `TUPPENCE_VERSION` and run `docker compose up -d` again. A database backup
  is taken before every migration.
- **HTTPS:** put Caddy or another reverse proxy in front of it, as SECURITY.md describes.
