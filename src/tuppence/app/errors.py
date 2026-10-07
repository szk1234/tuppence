"""Map domain exceptions to friendly JSON errors.

Every `detail` goes through `safe_error_text`: never the raw text of a library error."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tuppence.core.errors import InputError, UserFacing, safe_error_text
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.secrets import SecretError
from tuppence.core.settings_store import SettingInvalid
from tuppence.llm.types import (
    AllModelsBlocked,
    AllModelsFailed,
    BudgetExceeded,
    LLMError,
    NoModelConfigured,
    NoticeRequired,
)
from tuppence.net.client import LocalOnlyBlocked

CONFLICT_MESSAGE = "This was changed somewhere else. Reload and try again."


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(_r: Request, exc: RequestValidationError) -> JSONResponse:
        # Where and what, never the submitted value: it may be an API key.
        errors = [
            {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
            for e in exc.errors()
        ]
        return JSONResponse({"detail": errors}, status_code=422)

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
        return JSONResponse({"detail": safe_error_text(exc)}, status_code=422)

    @app.exception_handler(InputError)
    async def _invalid_input(_r: Request, exc: InputError) -> JSONResponse:
        return JSONResponse({"detail": safe_error_text(exc)}, status_code=422)

    @app.exception_handler(SecretError)
    async def _secret(_r: Request, exc: SecretError) -> JSONResponse:
        return JSONResponse({"detail": safe_error_text(exc)}, status_code=409)

    @app.exception_handler(LocalOnlyBlocked)
    async def _local_only(_r: Request, exc: LocalOnlyBlocked) -> JSONResponse:
        return JSONResponse({"detail": safe_error_text(exc)}, status_code=409)

    @app.exception_handler(AllModelsBlocked)
    async def _blocked(_r: Request, exc: AllModelsBlocked) -> JSONResponse:
        return JSONResponse({"detail": safe_error_text(exc)}, status_code=409)

    @app.exception_handler(NoticeRequired)
    async def _notice(_r: Request, exc: NoticeRequired) -> JSONResponse:
        return JSONResponse(
            {
                "detail": safe_error_text(exc),
                "code": "notice_required",
                "connection_id": exc.connection_id,
            },
            status_code=409,
        )

    @app.exception_handler(NoModelConfigured)
    async def _no_model(_r: Request, exc: NoModelConfigured) -> JSONResponse:
        return JSONResponse({"detail": safe_error_text(exc)}, status_code=409)

    @app.exception_handler(BudgetExceeded)
    async def _budget(_r: Request, exc: BudgetExceeded) -> JSONResponse:
        return JSONResponse({"detail": safe_error_text(exc)}, status_code=429)

    @app.exception_handler(AllModelsFailed)
    async def _all_failed(_r: Request, exc: AllModelsFailed) -> JSONResponse:
        return JSONResponse({"detail": safe_error_text(exc)}, status_code=502)

    @app.exception_handler(LLMError)
    async def _llm(_r: Request, exc: LLMError) -> JSONResponse:
        detail = safe_error_text(exc)
        if not isinstance(exc, UserFacing):
            detail = f"The AI service couldn't answer ({detail})."
        return JSONResponse({"detail": detail}, status_code=502)
