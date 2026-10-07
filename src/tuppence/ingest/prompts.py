"""Prompt templates: shipped defaults, overridable per install (spec §11.2)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

PROMPTS = "tuppence.config.defaults"


def load_prompt(name: str, user_dir: Path | None = None) -> str:
    """`<data>/config/prompts/<name>.txt` when present, else the shipped default."""
    if user_dir is not None:
        override = user_dir / "prompts" / f"{name}.txt"
        if override.is_file():
            return override.read_text(encoding="utf-8")
    return resources.files(PROMPTS).joinpath("prompts", f"{name}.txt").read_text(encoding="utf-8")
