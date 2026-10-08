"""Time limits for this device's own work on a statement (R-M3-23 (d)).

Every pass over a statement's lines or rows takes time in proportion to what it reads, and it
also looks at a deadline as it goes, so no upload can hold a worker for long whatever it
holds. A deadline is a callable that raises once the time is up: the extraction's
(`extract._Deadline.remaining`) while a file is prepared, or `after(seconds)` for the parse,
check and fix-up steps."""

from __future__ import annotations

import time
from collections.abc import Callable

from tuppence.core.errors import InputError

Deadline = Callable[[], object]  # raises once the time is up
EVERY = 256  # lines or rows between two looks at the deadline


class CheckTimeout(InputError):
    """Checking a statement took longer than its limit. Shown as it is (it quotes nothing)."""


def no_deadline() -> None:
    return None


def ticking(deadline: Deadline | None, every: int = EVERY) -> Callable[[int], None]:
    """A per-item hook that calls `deadline` every `every` items."""
    check = deadline or no_deadline

    def tick(i: int) -> None:
        if i % every == 0:
            check()

    return tick


def after(seconds: float, what: str = "Checking this statement") -> Deadline:
    """A deadline `seconds` from now: past it, `CheckTimeout` with a plain message."""
    end = time.monotonic() + seconds

    def check() -> None:
        if time.monotonic() > end:
            raise CheckTimeout(
                f"{what} took longer than {int(seconds)} seconds, so it was stopped. "
                "If the file is very large, try a smaller export."
            )

    return check
