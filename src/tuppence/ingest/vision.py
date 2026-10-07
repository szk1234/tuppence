"""Optional: read scans and screenshots with the AI `vision` task instead of RapidOCR
(spec §4.3, §6.2 step 1). Off unless the person turns on `ingest.vision_for_scans`."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from tuppence.ingest.prompts import load_prompt
from tuppence.llm.types import ImageData, Message, NoModelConfigured


class VisionLines(BaseModel):
    lines: list[str]


class VisionOCR:
    def __init__(self, llm: Any, run: Any, *, prompt: str) -> None:
        self.llm, self.run, self.prompt = llm, run, prompt

    def transcribe(self, image: bytes, media_type: str) -> list[str]:
        kind: Literal["image/png", "image/jpeg"] = (
            "image/png" if media_type == "image/png" else "image/jpeg"
        )
        page = Message(
            role="user",
            content="Transcribe every line of text on this page.",
            images=[ImageData(media_type=kind, data_b64=base64.b64encode(image).decode("ascii"))],
        )
        out = self.llm.structured(
            "vision",
            [Message(role="system", content=self.prompt), page],
            VisionLines,
            max_tokens=2048,
            run=self.run,
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
