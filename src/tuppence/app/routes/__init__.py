"""All API routers. Included by create_app() before the /api catch-all."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from tuppence.app.deps import require_session
from tuppence.app.routes import (
    accounts,
    auth,
    config,
    household,
    income,
    jobs,
    llm,
    privacy,
    settings,
    usage,
)

PROTECTED = [
    settings.router,
    household.router,
    accounts.router,
    income.router,
    config.router,
    jobs.router,
    privacy.router,
    llm.router,
    usage.router,
]


def include_routers(app: FastAPI) -> None:
    app.include_router(auth.router)
    for router in PROTECTED:
        app.include_router(router, dependencies=[Depends(require_session)])
