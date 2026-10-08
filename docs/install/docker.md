# Run Tuppence with Docker

For home servers and NAS boxes. Keep Tuppence on your home network; for remote access use a
VPN such as Tailscale, and never forward a port to it from the internet.

No image has been published yet, so Docker builds Tuppence from a clone of this repository:

```bash
git clone https://github.com/szk1234/tuppence && cd tuppence
docker compose up -d --build
```

Open `http://<server>:8040` and create the admin account (there are no default passwords).
Data is kept in the `tuppence-data` volume.

- **Local AI in the same compose file:** `docker compose --profile ollama up -d --build`,
  then add the `ollama` connection in Settings › AI (base URL `http://ollama:11434`).
- **Updating:** `git pull`, then `docker compose up -d --build` again. A database backup is
  taken before every migration.
- **HTTPS:** put Caddy or another reverse proxy in front of it, as SECURITY.md describes.
