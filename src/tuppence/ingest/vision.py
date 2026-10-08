"""Optional: read scans and screenshots with the AI `vision` task instead of RapidOCR
(spec §4.3, §6.2 step 1). Off unless the person turns on `ingest.vision_for_scans`."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from tuppence.core.errors import UserFacing
from tuppence.ingest.prompts import load_prompt
from tuppence.llm.types import BudgetExceeded, ImageData, Message, NoModelConfigured

READ_RESERVE = 2  # AI calls kept for reading the transcribed rows afterwards


class VisionLines(BaseModel):
    lines: list[str]


class VisionTooLong(UserFacing, ValueError):
    """A scan with more pages than one statement's AI calls can read."""


class _Bounded:
    """A run budget whose time is also bounded by the extraction deadline, so the LLM client
    gives each vision call only what is left of it (and never its own full timeout)."""

    def __init__(self, run: Any, left: Callable[[], float], limit_s: float) -> None:
        self._run, self._left, self.max_seconds = run, left, min(run.max_seconds, limit_s)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._run, name)

    def remaining_seconds(self) -> float:
        return min(self._run.remaining_seconds(), self._left())

    def check_time(self) -> None:
        if self._left() <= 0:
            raise BudgetExceeded(
                f"Reading this file took longer than {self.max_seconds:.0f} seconds."
            )
        self._run.check_time()


class VisionOCR:
    def __init__(self, llm: Any, run: Any, *, prompt: str) -> None:
        self.llm, self.run, self.prompt = llm, run, prompt
        self._left: Callable[[], float] | None = None
        self._limit_s = 0.0

    def bound(self, left: Callable[[], float], limit_s: float) -> None:
        """Every call from now on gets at most `left()` seconds (the extraction's deadline)."""
        self._left, self._limit_s = left, limit_s

    def check_pages(self, pages: int) -> None:
        """Refuse, before any call, a scan with more pages than this run's AI calls can read
        (one call a page, a few kept for reading the rows)."""
        max_calls = getattr(self.run, "max_calls", None)
        if max_calls is None:
            return
        left = max_calls - self.run.calls - READ_RESERVE
        if pages > left:
            raise VisionTooLong(
                f"This scan has {pages} pages, more than your AI vision model can read for one "
                f"statement ({max(left, 0)}). On the Statements page, under “Reading scans and "
                "screenshots”, turn off the AI vision model to read it on this device, or upload "
                "fewer pages at a time."
            )

    def transcribe(self, image: bytes, media_type: str) -> list[str]:
        kind: Literal["image/png", "image/jpeg"] = (
            "image/png" if media_type == "image/png" else "image/jpeg"
        )
        page = Message(
            role="user",
            content="Transcribe every line of text on this page.",
            images=[ImageData(media_type=kind, data_b64=base64.b64encode(image).decode("ascii"))],
        )
        run = self.run
        if self._left is not None and run is not None:
            run = _Bounded(run, self._left, self._limit_s)
        out = self.llm.structured(
            "vision",
            [Message(role="system", content=self.prompt), page],
            VisionLines,
            max_tokens=2048,
            run=run,
        )
        return [" ".join(line.split("\n")) for line in out.lines if line.strip()]


def vision_factory(
    llm: Any, router: Any, prompts_dir: Path | None
) -> Callable[[Any], VisionOCR | None]:
    """run budget → a VisionOCR, or None when no vision model is set up (RapidOCR is used)."""

    def make(run: Any) -> VisionOCR | None:
        try:
            router.chain_for("vision")
        except NoModelConfigured:
            return None
        return VisionOCR(llm, run, prompt=load_prompt("vision_ocr", prompts_dir))

    return make
