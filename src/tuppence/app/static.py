"""Serve the prebuilt Svelte UI with an SPA fallback (spec §3.2)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import tuppence

NOT_BUILT_HTML = """<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8"><title>Tuppence</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem;
 margin: 3rem auto; padding: 0 1rem">
<h1>Tuppence is running</h1>
<p>The web UI hasn't been built yet. From the repository root run:</p>
<pre>npm --prefix web ci &amp;&amp; npm --prefix web run build</pre>
<p>The API is available and <a href="/health">/health</a> reports status.</p>
</body></html>"""

_RESERVED_PREFIXES = ("/api/", "/health")


def default_web_dir() -> Path:
    return Path(tuppence.__file__).parent / "web_dist"


def mount_web(app: FastAPI, web_dir: Path | None) -> None:
    root = (web_dir or default_web_dir()).resolve()
    index = root / "index.html"
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa(full_path: str, request: Request) -> FileResponse | HTMLResponse:
        path = "/" + full_path
        if path.startswith(_RESERVED_PREFIXES) or path.startswith("/assets/"):
            raise HTTPException(status_code=404)
        if not index.is_file():
            return HTMLResponse(NOT_BUILT_HTML)
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
