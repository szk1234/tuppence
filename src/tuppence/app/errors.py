"""Map domain exceptions to friendly JSON errors."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.settings_store import SettingInvalid

CONFLICT_MESSAGE = "This was changed somewhere else. Reload and try again."


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(VersionConflict)
    async def _conflict(_r: Request, exc: VersionConflict) -> JSONResponse:
        return JSONResponse(
            {"detail": CONFLICT_MESSAGE, "current_version": exc.current}, status_code=409
        )

    @app.exception_handler(NotFound)
    async def _missing(_r: Request, _exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.exception_handler(SettingInvalid)
    async def _invalid_setting(_r: Request, exc: SettingInvalid) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(InputError)
    async def _invalid_input(_r: Request, exc: InputError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)
