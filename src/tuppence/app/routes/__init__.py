"""All API routers. Included by create_app() before the /api catch-all."""

from __future__ import annotations

from fastapi import FastAPI

from tuppence.app.routes import settings


def include_routers(app: FastAPI) -> None:
    app.include_router(settings.router)
