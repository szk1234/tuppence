"""Application factory: one FastAPI app for every shell."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from tuppence import __version__
from tuppence.app.errors import install_error_handlers
from tuppence.app.routes import include_routers
from tuppence.app.security import HostAllowlistMiddleware, SecurityHeadersMiddleware
from tuppence.app.services import build_services
from tuppence.app.session_cookie import SessionCookieRefreshMiddleware
from tuppence.app.static import mount_web
from tuppence.settings import RuntimeSettings


def create_app(settings: RuntimeSettings) -> FastAPI:
    services = build_services(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        services.start()
        try:
            yield
        finally:
            services.stop()

    app = FastAPI(
        lifespan=lifespan,
        title="Tuppence",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.services = services
    app.state.paths = services.paths
    install_error_handlers(app)
    app.add_middleware(HostAllowlistMiddleware, settings=settings)
    app.add_middleware(SessionCookieRefreshMiddleware, secure=settings.secure_cookies)
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

    include_routers(app)
    app.include_router(api)
    mount_web(app, settings.web_dir)
    return app
