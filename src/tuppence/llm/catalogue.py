"""Model facts the providers don't tell us: context windows, capabilities, prices."""

from __future__ import annotations

import json
import re
from importlib import resources

from pydantic import BaseModel, Field

_DATE = re.compile(r"-\d{8}$")
# After a catalogue id, only a date, "latest" or a build number marks the same model; anything
# else ("-deep-research", "-realtime-preview") is a different product with its own figures.
_VARIANT_OK = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2}|latest|\d{4})")
_SUFFIX = re.compile(r":(free|beta|latest|extended|thinking)$")


def normalise_model_id(model_id: str) -> str:
    m = model_id.strip().lower()
    m = _SUFFIX.sub("", m)
    m = m.split("/", 1)[-1]
    m = _DATE.sub("", m)
    return m.replace(".", "-")


class CatalogueEntry(BaseModel):
    id: str
    aliases: list[str] = Field(default_factory=list)
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool | None = None
    supports_json_schema: bool | None = None
    supports_vision: bool | None = None
    price_in_usd_per_mtok: float | None = None
    price_out_usd_per_mtok: float | None = None


class ModelCatalogue:
    def __init__(self, entries: list[CatalogueEntry]) -> None:
        self.by_key: dict[str, CatalogueEntry] = {}
        for e in entries:  # later entries win
            for key in {normalise_model_id(e.id), *(normalise_model_id(a) for a in e.aliases)}:
                self.by_key[key] = e
        self.keys_by_length = sorted(self.by_key, key=len, reverse=True)

    def lookup(self, model_id: str) -> CatalogueEntry | None:
        key = normalise_model_id(model_id)
        if key in self.by_key:
            return self.by_key[key]
        for candidate in self.keys_by_length:  # longest first
            if key.startswith(candidate) and _VARIANT_OK.fullmatch(key[len(candidate) :]):
                return self.by_key[candidate]
        return None


def load_baseline() -> ModelCatalogue:
    path = resources.files("tuppence.datapacks.baseline").joinpath("model-catalogue.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return ModelCatalogue([CatalogueEntry(**e) for e in data["models"]])
