from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def mount_web(app: FastAPI, web_dir: Path | None) -> None:
    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse("<!doctype html><title>Tuppence</title><p>Tuppence is running.</p>")
