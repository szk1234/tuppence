"""Application factory: one FastAPI app for every shell."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from tuppence import __version__
from tuppence.app.security import SecurityHeadersMiddleware
from tuppence.app.static import mount_web
from tuppence.paths import DataPaths
from tuppence.settings import RuntimeSettings


def create_app(settings: RuntimeSettings) -> FastAPI:
    app = FastAPI(
        title="Tuppence",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.paths = DataPaths(settings.data_dir).ensure()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "mode": settings.mode}

    api = APIRouter(prefix="/api")

    @api.api_route(
        "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False
    )
    def api_not_found(path: str) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    app.state.api_fallback = api
    mount_web(app, settings.web_dir)
    app.include_router(api)
    return app
