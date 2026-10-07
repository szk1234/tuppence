"""Find JSON in model output; make schemas acceptable to strict structured-output modes."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


_MAX_SCAN = 200_000


def _first_value(chunk: str, opener: str, begin: int = 0) -> tuple[Any, int, int] | None:
    """First decodable value starting with ``opener``: (value, start, end)."""
    decoder = json.JSONDecoder()
    start = chunk.find(opener, begin)
    while start != -1:
        try:
            value, end = decoder.raw_decode(chunk[start:])
        except (json.JSONDecodeError, RecursionError):
            start = chunk.find(opener, start + 1)
            continue
        return value, start, start + end
    return None


def extract_json(text: str) -> Any:
    """Return the JSON value in ``text``; ValueError if none.

    A first-found object wins. A first-found array wins only if it encloses any
    object (or no object follows it), because structured calls expect objects and
    prose like ``see [1] and {"a": 1}`` should yield the object. Only the first
    200k characters are scanned so the worst case stays bounded.
    """
    text = _THINK.sub("", text[:_MAX_SCAN])
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for chunk in candidates:
        obj = _first_value(chunk, "{")
        arr = _first_value(chunk, "[")
        if arr is not None and (obj is None or arr[1] < obj[1]):
            if obj is not None and arr[2] <= obj[1]:
                return obj[0]
            return arr[0]
        if obj is not None:
            return obj[0]
    raise ValueError("No JSON found in the model's reply")


def _resolve(node: Any, defs: dict[str, Any], seen: tuple[str, ...] = ()) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).split("/")[-1]
            if name in seen:
                raise ValueError(f"Recursive schema ({name}) can't be used in strict mode")
            if name not in defs:
                raise ValueError(f"Schema refers to unknown definition {name}")
            merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
            return _resolve(merged, defs, (*seen, name))
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in ("$defs", "title", "default"):
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: _resolve(sub, defs, seen) for name, sub in v.items()}
            else:
                out[k] = _resolve(v, defs, seen)
        if out.get("type") == "object" and "properties" in out:
            # Forced even if pydantic emitted a schema-valued additionalProperties.
            # Free-form dict fields (no fixed properties) are left as emitted; strict
            # mode cannot express them, so callers should avoid dict fields.
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out
    if isinstance(node, list):
        return [_resolve(v, defs, seen) for v in node]
    return node


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return _resolve(schema, schema.get("$defs", {}))
